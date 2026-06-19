# cogs/reminders.py
# Rappels personnels persistants.

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from config.settings import settings
from utils.database import db_get, db_get_all, db_set
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.reminders")

COLLECTION = "reminders"
CHECK_INTERVAL_SECONDS = 30
MAX_REMINDER_SECONDS = 60 * 86400


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_duration(duration: str) -> int | None:
    """Accepte 10m, 2h, 1j, 1h30m, etc."""
    compact = duration.lower().replace(" ", "")
    matches = list(re.finditer(r"(\d+)([smhjd])", compact))
    if not matches or "".join(match.group(0) for match in matches) != compact:
        return None

    units = {"s": 1, "m": 60, "h": 3600, "j": 86400, "d": 86400}
    total = sum(int(amount) * units[unit] for amount, unit in (match.groups() for match in matches))
    return total if total > 0 else None


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


def parse_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def discord_timestamp(dt: datetime, style: str = "R") -> str:
    return f"<t:{int(dt.timestamp())}:{style}>"


class RemindersCog(commands.Cog, name="Rappels"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._check_task = asyncio.create_task(self._check_reminders())

    def cog_unload(self) -> None:
        self._check_task.cancel()

    async def _check_reminders(self) -> None:
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            try:
                now = utc_now()
                reminders = await db_get_all(COLLECTION, {"done": False})

                for reminder in reminders:
                    due_at = parse_datetime(reminder["due_at"])
                    if due_at <= now:
                        await self._deliver_reminder(reminder)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Erreur vérification rappels : {e}")

            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def _deliver_reminder(self, reminder: dict) -> None:
        reminder["done"] = True
        reminder["sent_at"] = utc_now().isoformat()
        await db_set(COLLECTION, {"reminder_id": reminder["reminder_id"]}, reminder)

        user_id = int(reminder["user_id"])
        channel_id = int(reminder["channel_id"])

        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                user = None

        embed = discord.Embed(
            title="⏰ Rappel",
            description=reminder["message"],
            color=settings.COLOR_INFO,
            timestamp=utc_now(),
        )
        embed.add_field(
            name="Créé",
            value=discord_timestamp(parse_datetime(reminder["created_at"]), "R"),
            inline=True,
        )
        embed.set_footer(text=f"ID rappel : {reminder['reminder_id']}")

        mention = user.mention if user else f"<@{user_id}>"
        content = f"{mention}, tu m'as demandé de te rappeler :"
        channel = self.bot.get_channel(channel_id)

        try:
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                await channel.send(content=content, embed=embed)
                return
        except discord.HTTPException as e:
            logger.warning(f"Rappel {reminder['reminder_id']} impossible dans le salon : {e}")

        if user:
            try:
                await user.send(embed=embed)
            except discord.HTTPException as e:
                logger.warning(f"Rappel {reminder['reminder_id']} impossible en DM : {e}")

    @commands.hybrid_command(
        name="rappel",
        aliases=["remind", "remindme"],
        description="Crée un rappel personnel.",
    )
    @commands.guild_only()
    @commands.cooldown(3, 60, commands.BucketType.user)
    @app_commands.describe(
        duree="Durée avant le rappel (ex: 10m, 2h, 1j, 1h30m)",
        message="Texte du rappel",
    )
    async def rappel(self, ctx: commands.Context, duree: str, *, message: str) -> None:
        seconds = parse_duration(duree)
        if not seconds:
            await ctx.send("❌ Format invalide. Exemples : `10m`, `2h`, `1j`, `1h30m`.", ephemeral=True)
            return
        if seconds < 60:
            await ctx.send("❌ Durée minimum : **1 minute**.", ephemeral=True)
            return
        if seconds > MAX_REMINDER_SECONDS:
            await ctx.send("❌ Durée maximum : **60 jours**.", ephemeral=True)
            return
        if len(message) > 1000:
            await ctx.send("❌ Ton rappel doit faire moins de **1000 caractères**.", ephemeral=True)
            return

        now = utc_now()
        due_at = now + timedelta(seconds=seconds)
        reminder_id = uuid.uuid4().hex[:8]

        await db_set(COLLECTION, {"reminder_id": reminder_id}, {
            "reminder_id": reminder_id,
            "user_id": str(ctx.author.id),
            "guild_id": str(ctx.guild.id),
            "channel_id": str(ctx.channel.id),
            "message": message,
            "created_at": now.isoformat(),
            "due_at": due_at.isoformat(),
            "done": False,
            "cancelled": False,
        })

        embed = discord.Embed(
            title="⏰ Rappel programmé",
            description=message,
            color=settings.COLOR_SUCCESS,
            timestamp=now,
        )
        embed.add_field(name="Quand", value=f"{discord_timestamp(due_at)} ({discord_timestamp(due_at, 'F')})", inline=False)
        embed.add_field(name="Durée", value=f"`{format_duration(seconds)}`", inline=True)
        embed.add_field(name="ID", value=f"`{reminder_id}`", inline=True)
        embed.set_footer(text="Utilise /rappel-annuler avec l'ID pour annuler.")

        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="rappels",
        aliases=["reminders"],
        description="Liste tes rappels actifs.",
    )
    @commands.guild_only()
    async def rappels(self, ctx: commands.Context) -> None:
        reminders = await db_get_all(COLLECTION, {
            "user_id": str(ctx.author.id),
            "done": False,
        })

        if not reminders:
            await ctx.send("ℹ️ Tu n'as aucun rappel actif.", ephemeral=True)
            return

        reminders.sort(key=lambda item: parse_datetime(item["due_at"]))
        embed = discord.Embed(
            title="⏰ Tes rappels actifs",
            color=settings.COLOR_INFO,
            timestamp=utc_now(),
        )

        for reminder in reminders[:10]:
            due_at = parse_datetime(reminder["due_at"])
            message = reminder["message"]
            if len(message) > 160:
                message = message[:157] + "..."

            embed.add_field(
                name=f"ID `{reminder['reminder_id']}`",
                value=f"{discord_timestamp(due_at)} - {message}",
                inline=False,
            )

        if len(reminders) > 10:
            embed.set_footer(text=f"{len(reminders) - 10} autre(s) rappel(s) non affiché(s).")
        else:
            embed.set_footer(text="Fantoma • Rappels")

        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="rappel-annuler",
        aliases=["cancelreminder"],
        description="Annule un de tes rappels.",
    )
    @commands.guild_only()
    @app_commands.describe(rappel_id="ID du rappel à annuler")
    async def rappel_annuler(self, ctx: commands.Context, rappel_id: str) -> None:
        reminder = await db_get(COLLECTION, {"reminder_id": rappel_id.lower()})

        if not reminder or reminder.get("done") or reminder.get("user_id") != str(ctx.author.id):
            await ctx.send("❌ Rappel introuvable dans tes rappels actifs.", ephemeral=True)
            return

        reminder["done"] = True
        reminder["cancelled"] = True
        reminder["cancelled_at"] = utc_now().isoformat()
        await db_set(COLLECTION, {"reminder_id": reminder["reminder_id"]}, reminder)

        await ctx.send(f"✅ Rappel `{reminder['reminder_id']}` annulé.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RemindersCog(bot))
    logger.info("Cog 'Rappels' chargé.")
