# cogs/birthdays.py
# Anniversaires automatiques avec salon et rôle temporaire.

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config.settings import settings
from utils.database import db_delete, db_get, db_get_all, db_set
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.birthdays")

COLLECTION = "birthdays"
CONFIG_COLLECTION = "birthday_config"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def today_key() -> str:
    now = utc_now()
    return f"{now.month:02d}-{now.day:02d}"


def validate_date(day: int, month: int) -> bool:
    try:
        datetime(2000, month, day)
    except ValueError:
        return False
    return True


def age_for(year: int | None) -> int | None:
    if not year:
        return None
    now = utc_now()
    age = now.year - year
    return max(0, age)


class BirthdaysCog(commands.Cog, name="Anniversaires"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.birthday_loop.start()

    def cog_unload(self) -> None:
        self.birthday_loop.cancel()

    async def _get_config(self, guild_id: int) -> dict:
        doc = await db_get(CONFIG_COLLECTION, {"guild_id": str(guild_id)})
        if doc:
            return doc
        return {"guild_id": str(guild_id), "channel_id": None, "role_id": None, "enabled": True}

    @tasks.loop(hours=1)
    async def birthday_loop(self) -> None:
        current_key = today_key()

        for guild in self.bot.guilds:
            config = await self._get_config(guild.id)
            if not config.get("enabled", True):
                continue

            role = guild.get_role(int(config["role_id"])) if config.get("role_id") else None
            entries = await db_get_all(COLLECTION, {"guild_id": str(guild.id)})

            for entry in entries:
                member = guild.get_member(int(entry["user_id"]))
                if not member:
                    continue

                is_today = entry.get("date_key") == current_key
                if role and role in member.roles and not is_today:
                    try:
                        await member.remove_roles(role, reason="Fin du rôle anniversaire")
                    except discord.HTTPException:
                        pass

                if not is_today or entry.get("last_sent") == current_key:
                    continue

                if role and role not in member.roles:
                    try:
                        await member.add_roles(role, reason="Anniversaire")
                    except discord.HTTPException:
                        pass

                await self._announce_birthday(guild, member, entry, config)
                entry["last_sent"] = current_key
                await db_set(COLLECTION, {"guild_id": str(guild.id), "user_id": str(member.id)}, entry)

    @birthday_loop.before_loop
    async def before_birthday_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _announce_birthday(self, guild: discord.Guild, member: discord.Member, entry: dict, config: dict) -> None:
        channel = self.bot.get_channel(int(config["channel_id"])) if config.get("channel_id") else None
        if not isinstance(channel, discord.TextChannel):
            channel = guild.system_channel
        if not isinstance(channel, discord.TextChannel):
            return

        age = age_for(entry.get("year"))
        age_text = f" **{age} ans**" if age else ""
        embed = discord.Embed(
            title="🎂 Joyeux anniversaire !",
            description=f"Souhaitons un joyeux anniversaire à {member.mention}{age_text} !",
            color=settings.COLOR_SUCCESS,
            timestamp=utc_now(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Fantoma • Anniversaires")
        await channel.send(content=member.mention, embed=embed)

    @commands.hybrid_group(name="anniversaire", aliases=["birthday"], description="Gère les anniversaires.")
    @commands.guild_only()
    async def anniversaire_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @anniversaire_group.command(name="set", description="Enregistre ton anniversaire.")
    @app_commands.describe(jour="Jour de naissance", mois="Mois de naissance", annee="Année optionnelle")
    async def anniversaire_set(self, ctx: commands.Context, jour: int, mois: int, annee: int | None = None) -> None:
        if not validate_date(jour, mois):
            await ctx.send("❌ Date invalide.", ephemeral=True)
            return
        if annee and (annee < 1900 or annee > utc_now().year):
            await ctx.send("❌ Année invalide.", ephemeral=True)
            return

        date_key = f"{mois:02d}-{jour:02d}"
        await db_set(COLLECTION, {"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)}, {
            "guild_id": str(ctx.guild.id),
            "user_id": str(ctx.author.id),
            "day": jour,
            "month": mois,
            "year": annee,
            "date_key": date_key,
            "updated_at": utc_now().isoformat(),
        })
        year_text = f"/{annee}" if annee else ""
        await ctx.send(f"✅ Anniversaire enregistré : `{jour:02d}/{mois:02d}{year_text}`.", ephemeral=True)

    @anniversaire_group.command(name="remove", description="Supprime ton anniversaire.")
    async def anniversaire_remove(self, ctx: commands.Context) -> None:
        await db_delete(COLLECTION, {"guild_id": str(ctx.guild.id), "user_id": str(ctx.author.id)})
        await ctx.send("✅ Ton anniversaire a été supprimé.", ephemeral=True)

    @anniversaire_group.command(name="today", description="Affiche les anniversaires du jour.")
    async def anniversaire_today(self, ctx: commands.Context) -> None:
        entries = await db_get_all(COLLECTION, {"guild_id": str(ctx.guild.id), "date_key": today_key()})
        members = [ctx.guild.get_member(int(entry["user_id"])) for entry in entries]
        members = [member for member in members if member]

        if not members:
            await ctx.send("ℹ️ Aucun anniversaire enregistré aujourd'hui.", ephemeral=True)
            return

        await ctx.send(embed=discord.Embed(
            title="🎂 Anniversaires du jour",
            description="\n".join(member.mention for member in members),
            color=settings.COLOR_SUCCESS,
            timestamp=utc_now(),
        ))

    @anniversaire_group.command(name="list", description="Liste les prochains anniversaires.")
    async def anniversaire_list(self, ctx: commands.Context) -> None:
        entries = await db_get_all(COLLECTION, {"guild_id": str(ctx.guild.id)})
        if not entries:
            await ctx.send("ℹ️ Aucun anniversaire enregistré.", ephemeral=True)
            return

        now = utc_now()

        def sort_key(entry: dict) -> tuple[int, int]:
            month = int(entry["month"])
            day = int(entry["day"])
            year = now.year + (1 if (month, day) < (now.month, now.day) else 0)
            target = datetime(year, month, day, tzinfo=timezone.utc)
            return ((target - now).days, int(entry["user_id"]))

        entries.sort(key=sort_key)
        embed = discord.Embed(title="🎂 Prochains anniversaires", color=settings.COLOR_INFO, timestamp=utc_now())

        lines = []
        for entry in entries[:15]:
            member = ctx.guild.get_member(int(entry["user_id"]))
            name = member.mention if member else f"<@{entry['user_id']}>"
            lines.append(f"{name} — `{int(entry['day']):02d}/{int(entry['month']):02d}`")

        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    @anniversaire_group.command(name="salon", description="[ADMIN] Définit le salon des anniversaires.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(salon="Salon cible")
    async def anniversaire_salon(self, ctx: commands.Context, salon: discord.TextChannel) -> None:
        config = await self._get_config(ctx.guild.id)
        config["channel_id"] = str(salon.id)
        await db_set(CONFIG_COLLECTION, {"guild_id": str(ctx.guild.id)}, config)
        await ctx.send(f"✅ Salon des anniversaires défini : {salon.mention}.", ephemeral=True)

    @anniversaire_group.command(name="role", description="[ADMIN] Définit le rôle anniversaire temporaire.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(role="Rôle à donner le jour J")
    async def anniversaire_role(self, ctx: commands.Context, role: discord.Role) -> None:
        if ctx.guild.me and role >= ctx.guild.me.top_role:
            await ctx.send("❌ Mon rôle doit être au-dessus du rôle anniversaire.", ephemeral=True)
            return

        config = await self._get_config(ctx.guild.id)
        config["role_id"] = str(role.id)
        await db_set(CONFIG_COLLECTION, {"guild_id": str(ctx.guild.id)}, config)
        await ctx.send(f"✅ Rôle anniversaire défini : {role.mention}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BirthdaysCog(bot))
    logger.info("Cog 'Anniversaires' chargé.")
