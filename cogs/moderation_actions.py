# cogs/moderation_actions.py
# Sanctions complètes avec historique MongoDB.

import uuid
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config.settings import settings
from utils.database import db_get_all, db_set
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.moderation_actions")

COLLECTION = "sanctions"
MAX_TIMEOUT_SECONDS = 28 * 86400


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_duration(value: str) -> int | None:
    units = {"s": 1, "m": 60, "h": 3600, "j": 86400, "d": 86400}
    total = 0
    current = ""

    for char in value.lower().strip():
        if char.isdigit():
            current += char
        elif char in units and current:
            total += int(current) * units[char]
            current = ""
        else:
            return None

    return total if total > 0 and not current else None


def format_duration(seconds: int) -> str:
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if days:
        parts.append(f"{days}j")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs:
        parts.append(f"{secs}s")
    return " ".join(parts) if parts else "0s"


def timestamp(dt: datetime, style: str = "R") -> str:
    return f"<t:{int(dt.timestamp())}:{style}>"


class ModerationActionsCog(commands.Cog, name="Sanctions"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.expire_sanctions_loop.start()

    def cog_unload(self) -> None:
        self.expire_sanctions_loop.cancel()

    @tasks.loop(minutes=5)
    async def expire_sanctions_loop(self) -> None:
        active = await db_get_all(COLLECTION, {"active": True})
        now = utc_now()

        for sanction in active:
            expires_at_raw = sanction.get("expires_at")
            if not expires_at_raw:
                continue
            try:
                expires_at = datetime.fromisoformat(expires_at_raw)
            except ValueError:
                continue
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at > now:
                continue

            guild = self.bot.get_guild(int(sanction["guild_id"]))
            if guild and sanction.get("action") == "mute":
                member = guild.get_member(int(sanction["user_id"]))
                if member:
                    try:
                        await member.timeout(None, reason="Expiration automatique de sanction")
                    except discord.HTTPException:
                        pass
            elif guild and sanction.get("action") == "tempban":
                try:
                    user = await self.bot.fetch_user(int(sanction["user_id"]))
                    await guild.unban(user, reason="Expiration automatique de tempban")
                except discord.HTTPException:
                    pass

            sanction["active"] = False
            sanction["expired_at"] = now.isoformat()
            await db_set(COLLECTION, {"sanction_id": sanction["sanction_id"]}, sanction)

    @expire_sanctions_loop.before_loop
    async def before_expire_sanctions_loop(self) -> None:
        await self.bot.wait_until_ready()

    def _can_target(self, ctx: commands.Context, member: discord.Member) -> tuple[bool, str]:
        if member.id == ctx.guild.owner_id:
            return False, "❌ Tu ne peux pas sanctionner le propriétaire du serveur."
        if member == ctx.author:
            return False, "❌ Tu ne peux pas te sanctionner toi-même."

        bot_member = ctx.guild.me
        if bot_member and member.top_role >= bot_member.top_role:
            return False, "❌ Mon rôle doit être au-dessus de celui du membre ciblé."
        if ctx.author.id != ctx.guild.owner_id and member.top_role >= ctx.author.top_role:
            return False, "❌ Tu ne peux pas sanctionner un membre avec un rôle égal ou supérieur au tien."

        return True, ""

    async def _record(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        action: str,
        reason: str,
        expires_at: datetime | None = None,
        active: bool = False,
    ) -> str:
        sanction_id = uuid.uuid4().hex[:8]
        await db_set(COLLECTION, {"sanction_id": sanction_id}, {
            "sanction_id": sanction_id,
            "guild_id": str(guild_id),
            "user_id": str(user_id),
            "moderator_id": str(moderator_id),
            "action": action,
            "reason": reason,
            "created_at": utc_now().isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else None,
            "active": active,
        })
        return sanction_id

    async def _close_active_mutes(self, guild_id: int, user_id: int) -> None:
        active_mutes = await db_get_all(COLLECTION, {
            "guild_id": str(guild_id),
            "user_id": str(user_id),
            "action": "mute",
            "active": True,
        })
        for sanction in active_mutes:
            sanction["active"] = False
            sanction["ended_at"] = utc_now().isoformat()
            await db_set(COLLECTION, {"sanction_id": sanction["sanction_id"]}, sanction)

    async def _close_active_bans(self, guild_id: int, user_id: int) -> None:
        active_bans = await db_get_all(COLLECTION, {
            "guild_id": str(guild_id),
            "user_id": str(user_id),
            "active": True,
        })
        for sanction in active_bans:
            if sanction.get("action") not in {"ban", "tempban"}:
                continue
            sanction["active"] = False
            sanction["ended_at"] = utc_now().isoformat()
            await db_set(COLLECTION, {"sanction_id": sanction["sanction_id"]}, sanction)

    @commands.hybrid_command(name="mute", aliases=["timeout"], description="Mute temporairement un membre.")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(membre="Membre à mute", duree="Durée (ex: 10m, 2h, 1j)", raison="Raison")
    async def mute(self, ctx: commands.Context, membre: discord.Member, duree: str, *, raison: str = "Aucune raison") -> None:
        ok, message = self._can_target(ctx, membre)
        if not ok:
            await ctx.send(message, ephemeral=True)
            return

        seconds = parse_duration(duree)
        if not seconds:
            await ctx.send("❌ Durée invalide. Exemples : `10m`, `2h`, `1j`.", ephemeral=True)
            return
        if seconds < 60 or seconds > MAX_TIMEOUT_SECONDS:
            await ctx.send("❌ Le mute doit durer entre **1 minute** et **28 jours**.", ephemeral=True)
            return

        expires_at = utc_now() + timedelta(seconds=seconds)
        await membre.timeout(expires_at, reason=f"{raison} | Par {ctx.author}")
        sanction_id = await self._record(ctx.guild.id, membre.id, ctx.author.id, "mute", raison, expires_at, active=True)

        embed = discord.Embed(title="🔇 Membre mute", color=settings.COLOR_WARNING, timestamp=utc_now())
        embed.add_field(name="Membre", value=membre.mention, inline=True)
        embed.add_field(name="Durée", value=f"`{format_duration(seconds)}`", inline=True)
        embed.add_field(name="Expire", value=timestamp(expires_at), inline=True)
        embed.add_field(name="Raison", value=raison, inline=False)
        embed.set_footer(text=f"Sanction ID : {sanction_id}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="unmute", aliases=["demute"], description="Retire le mute d'un membre.")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(membre="Membre à démute", raison="Raison")
    async def unmute(self, ctx: commands.Context, membre: discord.Member, *, raison: str = "Aucune raison") -> None:
        await membre.timeout(None, reason=f"{raison} | Par {ctx.author}")
        await self._close_active_mutes(ctx.guild.id, membre.id)
        sanction_id = await self._record(ctx.guild.id, membre.id, ctx.author.id, "unmute", raison)

        embed = discord.Embed(
            title="🔊 Membre démute",
            description=f"{membre.mention} peut de nouveau parler.",
            color=settings.COLOR_SUCCESS,
            timestamp=utc_now(),
        )
        embed.add_field(name="Raison", value=raison, inline=False)
        embed.set_footer(text=f"Sanction ID : {sanction_id}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="kick", description="Expulse un membre du serveur.")
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    @app_commands.default_permissions(kick_members=True)
    @app_commands.describe(membre="Membre à expulser", raison="Raison")
    async def kick(self, ctx: commands.Context, membre: discord.Member, *, raison: str = "Aucune raison") -> None:
        ok, message = self._can_target(ctx, membre)
        if not ok:
            await ctx.send(message, ephemeral=True)
            return

        sanction_id = await self._record(ctx.guild.id, membre.id, ctx.author.id, "kick", raison)
        await membre.kick(reason=f"{raison} | Par {ctx.author}")
        await ctx.send(embed=discord.Embed(
            title="👢 Membre expulsé",
            description=f"{membre.mention} a été expulsé.\n**Raison :** {raison}\n`ID : {sanction_id}`",
            color=settings.COLOR_WARNING,
            timestamp=utc_now(),
        ))

    @commands.hybrid_command(name="ban", description="Bannit un membre du serveur.")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(membre="Membre à bannir", raison="Raison")
    async def ban(self, ctx: commands.Context, membre: discord.Member, *, raison: str = "Aucune raison") -> None:
        ok, message = self._can_target(ctx, membre)
        if not ok:
            await ctx.send(message, ephemeral=True)
            return

        sanction_id = await self._record(ctx.guild.id, membre.id, ctx.author.id, "ban", raison, active=True)
        await membre.ban(reason=f"{raison} | Par {ctx.author}", delete_message_days=1)
        await ctx.send(embed=discord.Embed(
            title="🔨 Membre banni",
            description=f"{membre.mention} a été banni.\n**Raison :** {raison}\n`ID : {sanction_id}`",
            color=settings.COLOR_ERROR,
            timestamp=utc_now(),
        ))

    @commands.hybrid_command(name="tempban", description="Bannit temporairement un membre.")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(membre="Membre à bannir temporairement", duree="Durée (ex: 1h, 2j)", raison="Raison")
    async def tempban(self, ctx: commands.Context, membre: discord.Member, duree: str, *, raison: str = "Aucune raison") -> None:
        ok, message = self._can_target(ctx, membre)
        if not ok:
            await ctx.send(message, ephemeral=True)
            return

        seconds = parse_duration(duree)
        if not seconds:
            await ctx.send("❌ Durée invalide. Exemples : `1h`, `2j`, `7d`.", ephemeral=True)
            return

        expires_at = utc_now() + timedelta(seconds=seconds)
        sanction_id = await self._record(ctx.guild.id, membre.id, ctx.author.id, "tempban", raison, expires_at, active=True)
        await membre.ban(reason=f"Tempban {format_duration(seconds)} : {raison} | Par {ctx.author}", delete_message_days=1)
        await ctx.send(embed=discord.Embed(
            title="⏳ Membre tempban",
            description=(
                f"{membre.mention} a été banni temporairement.\n"
                f"**Durée :** `{format_duration(seconds)}`\n"
                f"**Expire :** {timestamp(expires_at)}\n"
                f"**Raison :** {raison}\n"
                f"`ID : {sanction_id}`"
            ),
            color=settings.COLOR_ERROR,
            timestamp=utc_now(),
        ))

    @commands.hybrid_command(name="unban", description="Débannit un utilisateur par ID.")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(user_id="ID Discord de l'utilisateur", raison="Raison")
    async def unban(self, ctx: commands.Context, user_id: str, *, raison: str = "Aucune raison") -> None:
        try:
            user = await self.bot.fetch_user(int(user_id))
        except (ValueError, discord.HTTPException):
            await ctx.send("❌ ID utilisateur invalide ou introuvable.", ephemeral=True)
            return

        await ctx.guild.unban(user, reason=f"{raison} | Par {ctx.author}")
        await self._close_active_bans(ctx.guild.id, user.id)
        sanction_id = await self._record(ctx.guild.id, user.id, ctx.author.id, "unban", raison)
        await ctx.send(embed=discord.Embed(
            title="✅ Utilisateur débanni",
            description=f"**{user}** a été débanni.\n**Raison :** {raison}\n`ID : {sanction_id}`",
            color=settings.COLOR_SUCCESS,
            timestamp=utc_now(),
        ))

    @commands.hybrid_command(name="sanctions", aliases=["history"], description="Affiche l'historique des sanctions d'un membre.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(membre="Membre ciblé")
    async def sanctions(self, ctx: commands.Context, membre: discord.Member) -> None:
        sanctions = await db_get_all(COLLECTION, {
            "guild_id": str(ctx.guild.id),
            "user_id": str(membre.id),
        })
        sanctions.sort(key=lambda item: item.get("created_at", ""), reverse=True)

        if not sanctions:
            await ctx.send(f"✅ Aucun historique de sanction pour {membre.mention}.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🛡️ Sanctions de {membre.display_name}",
            color=settings.COLOR_INFO,
            timestamp=utc_now(),
        )
        for sanction in sanctions[:10]:
            created = datetime.fromisoformat(sanction["created_at"])
            moderator = f"<@{sanction['moderator_id']}>"
            active = " • actif" if sanction.get("active") else ""
            embed.add_field(
                name=f"{sanction['action'].upper()} `{sanction['sanction_id']}`{active}",
                value=f"{sanction.get('reason', 'Aucune raison')}\nPar {moderator} • {timestamp(created)}",
                inline=False,
            )
        embed.set_footer(text=f"{len(sanctions)} sanction(s) au total")
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModerationActionsCog(bot))
    logger.info("Cog 'Sanctions' chargé.")
