# cogs/stats_counters.py
# Compteurs de statistiques en temps réel dans des salons vocaux.

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config.settings import settings
from utils.database import db_get, db_set
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.stats_counters")

COLLECTION = "stats_counters"
COUNTER_LABELS = {
    "members": "👥 Membres",
    "humans": "🙋 Humains",
    "bots": "🤖 Bots",
    "roles": "🎭 Rôles",
    "channels": "💬 Salons",
    "boosts": "🚀 Boosts",
}
DEFAULT_COUNTERS = ("members", "humans", "bots")


async def get_config(guild_id: int) -> dict:
    doc = await db_get(COLLECTION, {"guild_id": str(guild_id)})
    if doc:
        return doc
    return {
        "guild_id": str(guild_id),
        "enabled": False,
        "category_id": None,
        "counters": {},
    }


def count_value(guild: discord.Guild, counter_type: str) -> int:
    if counter_type == "members":
        return guild.member_count or len(guild.members)
    if counter_type == "humans":
        return sum(1 for member in guild.members if not member.bot)
    if counter_type == "bots":
        return sum(1 for member in guild.members if member.bot)
    if counter_type == "roles":
        return max(0, len(guild.roles) - 1)
    if counter_type == "channels":
        return len(guild.text_channels) + len(guild.voice_channels)
    if counter_type == "boosts":
        return guild.premium_subscription_count or 0
    return 0


def counter_name(guild: discord.Guild, counter_type: str) -> str:
    label = COUNTER_LABELS.get(counter_type, counter_type)
    return f"{label}: {count_value(guild, counter_type)}"


class StatsCountersCog(commands.Cog, name="Compteurs stats"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.refresh_counters_loop.start()

    def cog_unload(self) -> None:
        self.refresh_counters_loop.cancel()

    @tasks.loop(minutes=10)
    async def refresh_counters_loop(self) -> None:
        for guild in self.bot.guilds:
            await self.refresh_guild(guild)

    @refresh_counters_loop.before_loop
    async def before_refresh_counters_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def refresh_guild(self, guild: discord.Guild, force: bool = False) -> int:
        config = await get_config(guild.id)
        if not config.get("enabled") and not force:
            return 0

        updated = 0
        counters = config.get("counters", {})
        for counter_type, channel_id in counters.items():
            channel = guild.get_channel(int(channel_id))
            if not isinstance(channel, discord.VoiceChannel):
                continue

            name = counter_name(guild, counter_type)
            if channel.name == name:
                continue

            try:
                await channel.edit(name=name, reason="Mise à jour compteur stats Fantoma")
                updated += 1
            except discord.HTTPException as e:
                logger.warning(f"Impossible de mettre à jour {channel_id} ({counter_type}) : {e}")

        if updated:
            config["last_refresh"] = datetime.now(timezone.utc).isoformat()
            await db_set(COLLECTION, {"guild_id": str(guild.id)}, config)
        return updated

    async def refresh_later(self, guild: discord.Guild) -> None:
        await self.refresh_guild(guild)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self.refresh_later(member.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self.refresh_later(member.guild)

    @commands.hybrid_group(name="stats-counter", aliases=["compteurs"], description="Gère les compteurs stats en salons vocaux.")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @app_commands.default_permissions(manage_channels=True)
    async def stats_counter_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @stats_counter_group.command(name="setup", description="Crée les compteurs de base.")
    async def stats_counter_setup(self, ctx: commands.Context) -> None:
        await ctx.defer(ephemeral=True)
        guild = ctx.guild
        config = await get_config(guild.id)

        category = None
        if config.get("category_id"):
            category = guild.get_channel(int(config["category_id"]))
        if not isinstance(category, discord.CategoryChannel):
            category = await guild.create_category("📊 Statistiques", reason=f"Compteurs créés par {ctx.author}")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True)
        }

        counters = config.get("counters", {})
        for counter_type in DEFAULT_COUNTERS:
            existing = guild.get_channel(int(counters[counter_type])) if counters.get(counter_type) else None
            if isinstance(existing, discord.VoiceChannel):
                await existing.edit(name=counter_name(guild, counter_type), category=category, overwrites=overwrites)
                continue

            channel = await guild.create_voice_channel(
                name=counter_name(guild, counter_type),
                category=category,
                overwrites=overwrites,
                reason=f"Compteur stats créé par {ctx.author}",
            )
            counters[counter_type] = str(channel.id)

        config.update({
            "guild_id": str(guild.id),
            "enabled": True,
            "category_id": str(category.id),
            "counters": counters,
            "updated_by": str(ctx.author.id),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        await db_set(COLLECTION, {"guild_id": str(guild.id)}, config)
        await self.refresh_guild(guild, force=True)
        await ctx.send(f"✅ Compteurs créés dans la catégorie **{category.name}**.", ephemeral=True)

    @stats_counter_group.command(name="set", description="Associe un salon vocal existant à un compteur.")
    @app_commands.describe(
        type_compteur="Type de compteur",
        salon="Salon vocal à renommer automatiquement",
    )
    @app_commands.choices(type_compteur=[
        app_commands.Choice(name="Membres", value="members"),
        app_commands.Choice(name="Humains", value="humans"),
        app_commands.Choice(name="Bots", value="bots"),
        app_commands.Choice(name="Rôles", value="roles"),
        app_commands.Choice(name="Salons", value="channels"),
        app_commands.Choice(name="Boosts", value="boosts"),
    ])
    async def stats_counter_set(self, ctx: commands.Context, type_compteur: str, salon: discord.VoiceChannel) -> None:
        config = await get_config(ctx.guild.id)
        counters = config.get("counters", {})
        counters[type_compteur] = str(salon.id)
        config.update({
            "guild_id": str(ctx.guild.id),
            "enabled": True,
            "counters": counters,
            "updated_by": str(ctx.author.id),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        await db_set(COLLECTION, {"guild_id": str(ctx.guild.id)}, config)
        await self.refresh_guild(ctx.guild, force=True)
        await ctx.send(f"✅ `{COUNTER_LABELS[type_compteur]}` lié à {salon.mention}.", ephemeral=True)

    @stats_counter_group.command(name="toggle", description="Active ou désactive les compteurs.")
    @app_commands.describe(actif="Activer ou désactiver")
    async def stats_counter_toggle(self, ctx: commands.Context, actif: bool) -> None:
        config = await get_config(ctx.guild.id)
        config["enabled"] = actif
        config["guild_id"] = str(ctx.guild.id)
        await db_set(COLLECTION, {"guild_id": str(ctx.guild.id)}, config)
        await ctx.send(f"Compteurs stats : {'✅ activés' if actif else '❌ désactivés'}.", ephemeral=True)

    @stats_counter_group.command(name="refresh", description="Force la mise à jour des compteurs.")
    async def stats_counter_refresh(self, ctx: commands.Context) -> None:
        updated = await self.refresh_guild(ctx.guild, force=True)
        await ctx.send(f"✅ `{updated}` compteur(s) mis à jour.", ephemeral=True)

    @stats_counter_group.command(name="status", description="Affiche la configuration des compteurs.")
    async def stats_counter_status(self, ctx: commands.Context) -> None:
        config = await get_config(ctx.guild.id)
        counters = config.get("counters", {})
        description = "\n".join(
            f"**{COUNTER_LABELS.get(counter_type, counter_type)}** → <#{channel_id}>"
            for counter_type, channel_id in counters.items()
        ) or "Aucun compteur configuré."

        embed = discord.Embed(
            title="📊 Compteurs stats",
            description=description,
            color=settings.COLOR_INFO,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="État", value="✅ Activés" if config.get("enabled") else "❌ Désactivés", inline=True)
        if config.get("last_refresh"):
            embed.add_field(name="Dernière mise à jour", value=f"`{config['last_refresh']}`", inline=False)
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatsCountersCog(bot))
    logger.info("Cog 'Compteurs stats' chargé.")
