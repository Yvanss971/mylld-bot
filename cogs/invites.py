# cogs/invites.py
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

from utils.database import db_get, db_set, db_get_all
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.invites")


class InvitesCog(commands.Cog, name="Invites"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Cache : {guild_id: {code: discord.Invite}}
        self._invite_cache: dict[int, dict[str, discord.Invite]] = {}

    # ──────────────────────────────────────────────────────────────────────────
    # HELPERS CACHE
    # ──────────────────────────────────────────────────────────────────────────

    async def _cache_invites(self, guild: discord.Guild) -> None:
        try:
            invites = await guild.invites()
            self._invite_cache[guild.id] = {inv.code: inv for inv in invites}
        except (discord.Forbidden, discord.HTTPException):
            self._invite_cache[guild.id] = {}

    async def _find_inviter(self, guild: discord.Guild) -> discord.Member | None:
        """Compare l'ancien cache avec les nouvelles invitations → trouve l'inviteur."""
        try:
            new_invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return None

        old_cache = self._invite_cache.get(guild.id, {})
        inviter   = None

        for invite in new_invites:
            old = old_cache.get(invite.code)
            if old and invite.uses > old.uses:
                inviter = invite.inviter
                break

        # Mise à jour du cache
        self._invite_cache[guild.id] = {inv.code: inv for inv in new_invites}
        return inviter

    # ──────────────────────────────────────────────────────────────────────────
    # HELPERS DB
    # ──────────────────────────────────────────────────────────────────────────

    async def _get_counts(self, guild_id: int, user_id: int) -> dict:
        data = await db_get("invite_counts", {"guild_id": guild_id, "user_id": user_id})
        if not data:
            return {"guild_id": guild_id, "user_id": user_id, "total": 0, "left": 0, "fake": 0}
        return data

    async def _save_counts(self, data: dict) -> None:
        await db_set(
            "invite_counts",
            {"guild_id": data["guild_id"], "user_id": data["user_id"]},
            data,
        )

    async def get_real_invites(self, guild_id: int, user_id: int) -> int:
        """Retourne le nombre d'invitations réelles (utilisé par giveaway.py)."""
        counts = await self._get_counts(guild_id, user_id)
        return max(0, counts["total"] - counts["left"] - counts["fake"])

    # ──────────────────────────────────────────────────────────────────────────
    # LISTENERS
    # ──────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            await self._cache_invites(guild)
        logger.info(f"Invitations cachées pour {len(self.bot.guilds)} serveurs.")

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._cache_invites(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        if invite.guild:
            self._invite_cache.setdefault(invite.guild.id, {})[invite.code] = invite

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        if invite.guild:
            self._invite_cache.get(invite.guild.id, {}).pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        guild   = member.guild
        inviter = await self._find_inviter(guild)

        # Log en base
        await db_set(
            "invite_logs",
            {"guild_id": guild.id, "invited_id": member.id},
            {
                "guild_id":   guild.id,
                "invited_id": member.id,
                "inviter_id": inviter.id if inviter else None,
                "invited_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Incrémente le compteur de l'inviteur
        if inviter:
            counts          = await self._get_counts(guild.id, inviter.id)
            counts["total"] += 1
            await self._save_counts(counts)

        # Dispatche l'événement → welcome.py + advanced_logs.py l'écoutent
        self.bot.dispatch("member_invited", member, inviter)
        logger.info(f"{member} a rejoint {guild.name} — invité par {inviter}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Quand un membre part → 'left' de l'inviteur +1."""
        if member.bot:
            return

        log = await db_get("invite_logs", {"guild_id": member.guild.id, "invited_id": member.id})
        if not log or not log.get("inviter_id"):
            return

        counts        = await self._get_counts(member.guild.id, log["inviter_id"])
        counts["left"] += 1
        await self._save_counts(counts)

    # ──────────────────────────────────────────────────────────────────────────
    # COMMANDES
    # ──────────────────────────────────────────────────────────────────────────

    @commands.hybrid_command(
        name="invites",
        aliases=["inv"],
        description="Affiche le nombre d'invitations d'un membre.",
    )
    @commands.guild_only()
    @app_commands.describe(membre="Membre à vérifier (toi par défaut)")
    async def invites_command(
        self, ctx: commands.Context, membre: discord.Member = None
    ) -> None:
        target = membre or ctx.author
        counts = await self._get_counts(ctx.guild.id, target.id)

        total = counts["total"]
        left  = counts["left"]
        fake  = counts["fake"]
        real  = max(0, total - left - fake)

        embed = discord.Embed(
            title=f"📨 Invitations — {target.display_name}",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="✅ Réelles", value=f"`{real}`",  inline=True)
        embed.add_field(name="📊 Total",   value=f"`{total}`", inline=True)
        embed.add_field(name="🚪 Partis",  value=f"`{left}`",  inline=True)
        embed.set_footer(text=f"{ctx.guild.name} • Invitations")
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="invites-top",
        aliases=["invtop"],
        description="Classement des meilleurs inviteurs.",
    )
    @commands.guild_only()
    async def invites_top(self, ctx: commands.Context) -> None:
        all_counts = await db_get_all("invite_counts", {"guild_id": ctx.guild.id})

        sorted_counts = sorted(
            all_counts,
            key=lambda x: max(0, x.get("total", 0) - x.get("left", 0) - x.get("fake", 0)),
            reverse=True,
        )[:10]

        if not sorted_counts:
            await ctx.send("Aucune invitation enregistrée.", ephemeral=True)
            return

        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, entry in enumerate(sorted_counts):
            m    = ctx.guild.get_member(entry["user_id"])
            name = m.display_name if m else f"ID {entry['user_id']}"
            real = max(0, entry.get("total", 0) - entry.get("left", 0) - entry.get("fake", 0))
            icon = medals[i] if i < 3 else f"`#{i+1}`"
            lines.append(f"{icon} **{name}** — `{real}` invitations")

        embed = discord.Embed(
            title="📨 Top Inviteurs",
            description="\n".join(lines),
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=ctx.guild.name)
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="invites-reset",
        description="Remet à zéro les invitations d'un membre.",
    )
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @app_commands.describe(membre="Membre à réinitialiser")
    async def invites_reset(self, ctx: commands.Context, membre: discord.Member) -> None:
        await self._save_counts(
            {"guild_id": ctx.guild.id, "user_id": membre.id, "total": 0, "left": 0, "fake": 0}
        )
        await ctx.send(
            embed=discord.Embed(
                description=f"✅ Invitations de {membre.mention} remises à zéro.",
                color=0x57F287,
            )
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InvitesCog(bot))
    logger.info("Cog 'Invites' chargé.")