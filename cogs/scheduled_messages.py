# cogs/scheduled_messages.py
# Messages programmés et récurrents.

import uuid
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config.settings import settings
from utils.database import db_get, db_get_all, db_set
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.scheduled_messages")

COLLECTION = "scheduled_messages"
MIN_DELAY_SECONDS = 60
MAX_DELAY_SECONDS = 90 * 86400


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


def parse_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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


class ScheduledMessagesCog(commands.Cog, name="Messages programmés"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.schedule_loop.start()

    def cog_unload(self) -> None:
        self.schedule_loop.cancel()

    @tasks.loop(seconds=30)
    async def schedule_loop(self) -> None:
        now = utc_now()
        messages = await db_get_all(COLLECTION, {"active": True})

        for item in messages:
            try:
                next_run = parse_datetime(item["next_run"])
            except (KeyError, ValueError):
                continue
            if next_run > now:
                continue

            await self._send_scheduled_message(item)

    @schedule_loop.before_loop
    async def before_schedule_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _send_scheduled_message(self, item: dict) -> None:
        channel = self.bot.get_channel(int(item["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            item["active"] = False
            item["disabled_reason"] = "Salon introuvable"
            await db_set(COLLECTION, {"schedule_id": item["schedule_id"]}, item)
            return

        try:
            await channel.send(item["message"])
        except discord.HTTPException as e:
            logger.warning(f"Message programmé {item['schedule_id']} non envoyé : {e}")
            return

        item["last_sent_at"] = utc_now().isoformat()
        item["send_count"] = int(item.get("send_count", 0)) + 1

        if item.get("recurring"):
            interval = int(item["interval_seconds"])
            item["next_run"] = (utc_now() + timedelta(seconds=interval)).isoformat()
        else:
            item["active"] = False
            item["done"] = True

        await db_set(COLLECTION, {"schedule_id": item["schedule_id"]}, item)

    @commands.hybrid_group(name="schedule", aliases=["programmation"], description="Gère les messages programmés.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @app_commands.default_permissions(manage_messages=True)
    async def schedule_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @schedule_group.command(name="once", description="Programme un message unique.")
    @app_commands.describe(
        salon="Salon où envoyer le message",
        delai="Délai avant l'envoi (ex: 10m, 2h, 1j)",
        message="Message à envoyer",
    )
    async def schedule_once(self, ctx: commands.Context, salon: discord.TextChannel, delai: str, *, message: str) -> None:
        seconds = parse_duration(delai)
        if not seconds or seconds < MIN_DELAY_SECONDS or seconds > MAX_DELAY_SECONDS:
            await ctx.send("❌ Délai invalide. Utilise entre `1m` et `90j`.", ephemeral=True)
            return
        if len(message) > 1900:
            await ctx.send("❌ Message trop long, maximum 1900 caractères.", ephemeral=True)
            return

        schedule_id = uuid.uuid4().hex[:8]
        next_run = utc_now() + timedelta(seconds=seconds)
        await db_set(COLLECTION, {"schedule_id": schedule_id}, {
            "schedule_id": schedule_id,
            "guild_id": str(ctx.guild.id),
            "channel_id": str(salon.id),
            "message": message,
            "active": True,
            "done": False,
            "recurring": False,
            "interval_seconds": None,
            "next_run": next_run.isoformat(),
            "created_by": str(ctx.author.id),
            "created_at": utc_now().isoformat(),
            "send_count": 0,
        })
        await ctx.send(
            f"✅ Message programmé dans {salon.mention} <t:{int(next_run.timestamp())}:R>. ID : `{schedule_id}`",
            ephemeral=True,
        )

    @schedule_group.command(name="repeat", description="Programme un message récurrent.")
    @app_commands.describe(
        salon="Salon où envoyer le message",
        intervalle="Intervalle entre deux envois (ex: 6h, 1j, 7d)",
        message="Message à envoyer",
    )
    async def schedule_repeat(self, ctx: commands.Context, salon: discord.TextChannel, intervalle: str, *, message: str) -> None:
        seconds = parse_duration(intervalle)
        if not seconds or seconds < 3600 or seconds > MAX_DELAY_SECONDS:
            await ctx.send("❌ Intervalle invalide. Utilise entre `1h` et `90j`.", ephemeral=True)
            return
        if len(message) > 1900:
            await ctx.send("❌ Message trop long, maximum 1900 caractères.", ephemeral=True)
            return

        schedule_id = uuid.uuid4().hex[:8]
        next_run = utc_now() + timedelta(seconds=seconds)
        await db_set(COLLECTION, {"schedule_id": schedule_id}, {
            "schedule_id": schedule_id,
            "guild_id": str(ctx.guild.id),
            "channel_id": str(salon.id),
            "message": message,
            "active": True,
            "done": False,
            "recurring": True,
            "interval_seconds": seconds,
            "next_run": next_run.isoformat(),
            "created_by": str(ctx.author.id),
            "created_at": utc_now().isoformat(),
            "send_count": 0,
        })
        await ctx.send(
            f"✅ Message récurrent programmé dans {salon.mention} toutes les `{format_duration(seconds)}`. ID : `{schedule_id}`",
            ephemeral=True,
        )

    @schedule_group.command(name="list", description="Liste les messages programmés actifs.")
    async def schedule_list(self, ctx: commands.Context) -> None:
        items = await db_get_all(COLLECTION, {"guild_id": str(ctx.guild.id), "active": True})
        items.sort(key=lambda item: item.get("next_run", ""))

        if not items:
            await ctx.send("ℹ️ Aucun message programmé actif.", ephemeral=True)
            return

        embed = discord.Embed(title="⏰ Messages programmés", color=settings.COLOR_INFO, timestamp=utc_now())
        for item in items[:10]:
            next_run = parse_datetime(item["next_run"])
            mode = "Récurrent" if item.get("recurring") else "Unique"
            preview = item["message"][:120] + ("..." if len(item["message"]) > 120 else "")
            embed.add_field(
                name=f"`{item['schedule_id']}` — {mode}",
                value=f"Salon : <#{item['channel_id']}>\nProchain envoi : <t:{int(next_run.timestamp())}:R>\n{preview}",
                inline=False,
            )
        await ctx.send(embed=embed, ephemeral=True)

    @schedule_group.command(name="cancel", description="Annule un message programmé.")
    @app_commands.describe(schedule_id="ID du message programmé")
    async def schedule_cancel(self, ctx: commands.Context, schedule_id: str) -> None:
        item = await db_get(COLLECTION, {"guild_id": str(ctx.guild.id), "schedule_id": schedule_id.lower()})
        if not item or not item.get("active"):
            await ctx.send("❌ Message programmé introuvable.", ephemeral=True)
            return

        item["active"] = False
        item["cancelled_at"] = utc_now().isoformat()
        item["cancelled_by"] = str(ctx.author.id)
        await db_set(COLLECTION, {"schedule_id": item["schedule_id"]}, item)
        await ctx.send(f"✅ Message programmé `{item['schedule_id']}` annulé.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ScheduledMessagesCog(bot))
    logger.info("Cog 'Messages programmés' chargé.")
