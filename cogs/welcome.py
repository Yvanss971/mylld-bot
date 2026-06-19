# cogs/welcome.py
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

from config.settings import settings
from utils.database import get_db
from utils.guild_config import get_guild_config, set_guild_config
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.welcome")

COLOR_MAP = {
    "PRIMARY": settings.COLOR_PRIMARY,
    "SUCCESS": settings.COLOR_SUCCESS,
    "WARNING": settings.COLOR_WARNING,
    "ERROR":   settings.COLOR_ERROR,
    "INFO":    settings.COLOR_INFO,
    "NEUTRAL": settings.COLOR_NEUTRAL,
}

COLLECTION = "members_seen"


async def is_returning_member(user_id: str, guild_id: int) -> bool:
    db  = await get_db()
    doc = await db[COLLECTION].find_one({"key": f"{guild_id}:{user_id}"})
    return doc is not None


async def get_join_count(user_id: str, guild_id: int) -> int:
    db  = await get_db()
    doc = await db[COLLECTION].find_one({"key": f"{guild_id}:{user_id}"})
    return (doc or {}).get("join_count", 0) + 1


async def mark_member_seen(user_id: str, guild_id: int, username: str) -> None:
    db        = await get_db()
    key       = f"{guild_id}:{user_id}"
    existing  = await db[COLLECTION].find_one({"key": key})
    join_count = (existing or {}).get("join_count", 0) + 1
    await db[COLLECTION].update_one(
        {"key": key},
        {"$set": {
            "username":   username,
            "last_join":  datetime.now(timezone.utc).isoformat(),
            "join_count": join_count,
        }},
        upsert=True,
    )


# ── {inviter} supporté dans les textes ──
def format_text(text: str, member: discord.Member, inviter: discord.Member | None = None) -> str:
    inviter_str = inviter.mention if inviter else "Invitation inconnue"
    return (
        text
        .replace("{mention}",  member.mention)
        .replace("{username}", str(member))
        .replace("{server}",   member.guild.name)
        .replace("{count}",    str(member.guild.member_count))
        .replace("{inviter}",  inviter_str)
    )


def build_welcome_embed(
    member: discord.Member,
    config: dict,
    inviter: discord.Member | None = None,
) -> discord.Embed:
    color = COLOR_MAP.get(config.get("color", "PRIMARY"), settings.COLOR_PRIMARY)
    embed = discord.Embed(
        title=format_text(config.get("title", "Bienvenue !"), member, inviter),
        description=format_text(config.get("description", ""), member, inviter),
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    if config.get("thumbnail") == "avatar":
        embed.set_thumbnail(url=member.display_avatar.url)
    elif config.get("thumbnail"):
        embed.set_thumbnail(url=config["thumbnail"])
    banner_url = config.get("banner_url") or config.get("image_url")
    if banner_url:
        embed.set_image(url=banner_url)
    if config.get("show_member_count"):
        embed.add_field(name="👥 Membres", value=f"`{member.guild.member_count}`", inline=True)
    if member.joined_at:
        embed.add_field(name="📅 Arrivée", value=f"<t:{int(member.joined_at.timestamp())}:R>", inline=True)
    embed.add_field(name="🗓️ Compte créé", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)

    # Inviteur — toujours affiché
    if inviter:
        embed.add_field(name="📨 Invité par", value=inviter.mention, inline=True)

    embed.set_footer(text=config.get("footer", member.guild.name))
    return embed


def build_return_embed(
    member: discord.Member,
    config: dict,
    join_count: int,
    welcome_config: dict,
    inviter: discord.Member | None = None,
) -> discord.Embed:
    color = COLOR_MAP.get(config.get("color", "SUCCESS"), settings.COLOR_SUCCESS)
    embed = discord.Embed(
        title=format_text(config.get("title", "Bon retour !"), member, inviter),
        description=format_text(config.get("description", ""), member, inviter),
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    if config.get("thumbnail") == "avatar":
        embed.set_thumbnail(url=member.display_avatar.url)
    banner_url = config.get("banner_url") or config.get("image_url") or welcome_config.get("banner_url")
    if banner_url:
        embed.set_image(url=banner_url)
    if config.get("show_member_count"):
        embed.add_field(name="👥 Membres", value=f"`{member.guild.member_count}`", inline=True)
    embed.add_field(name="🔄 Visites",     value=f"`{join_count}` fois",           inline=True)
    embed.add_field(name="🗓️ Compte créé", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
    if inviter:
        embed.add_field(name="📨 Invité par", value=inviter.mention, inline=True)
    embed.set_footer(text=config.get("footer", member.guild.name))
    return embed


def build_goodbye_embed(member: discord.Member, config: dict) -> discord.Embed:
    color = COLOR_MAP.get(config.get("color", "NEUTRAL"), settings.COLOR_NEUTRAL)
    embed = discord.Embed(
        title=format_text(config.get("title", "Au revoir !"), member),
        description=format_text(config.get("description", ""), member),
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if config.get("banner_url"):
        embed.set_image(url=config["banner_url"])
    if config.get("show_member_count"):
        embed.add_field(name="👥 Membres restants", value=f"`{member.guild.member_count}`", inline=True)
    embed.set_footer(text=config.get("footer", member.guild.name))
    return embed


class WelcomeCog(commands.Cog, name="Bienvenue"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ──────────────────────────────────────────────────────────────────────────
    # LISTENERS
    # ──────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_invited(
        self, member: discord.Member, inviter: discord.Member | None
    ) -> None:
        """
        Déclenché par cogs/invites.py après détection de l'inviteur.
        Remplace on_member_join pour le welcome → on a toujours l'info inviteur.
        """
        config     = await get_guild_config(member.guild.id, "welcome")
        user_id    = str(member.id)
        guild_id   = member.guild.id
        returning  = await is_returning_member(user_id, guild_id)
        join_count = await get_join_count(user_id, guild_id)
        await mark_member_seen(user_id, guild_id, str(member))

        if returning and config.get("return", {}).get("enabled"):
            await self._send_return(member, config, join_count, inviter)
        else:
            await self._send_welcome(member, config, inviter)

        # Logs avancés
        if hasattr(self.bot, "discord_logger"):
            label        = "🔄 Membre de retour" if returning else "👋 Nouveau membre"
            inviter_info = inviter.mention if inviter else "Inconnu"
            await self.bot.discord_logger.log(
                title=label,
                description=f"{member.mention} a rejoint **{member.guild.name}**.",
                color=settings.COLOR_INFO if returning else settings.COLOR_SUCCESS,
                fields=[
                    {"name": "Membres",     "value": str(member.guild.member_count), "inline": True},
                    {"name": "Visites",     "value": str(join_count),                "inline": True},
                    {"name": "📨 Invité par", "value": inviter_info,                 "inline": True},
                ],
                user=member,
            )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        config  = await get_guild_config(member.guild.id, "welcome")
        goodbye = config.get("goodbye", {})
        if not goodbye.get("enabled"):
            return
        channel = self.bot.get_channel(goodbye.get("channel_id"))
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await channel.send(embed=build_goodbye_embed(member, goodbye))
        except discord.Forbidden:
            pass

    # ──────────────────────────────────────────────────────────────────────────
    # HELPERS INTERNES
    # ──────────────────────────────────────────────────────────────────────────

    async def _send_welcome(
        self,
        member: discord.Member,
        config: dict,
        inviter: discord.Member | None = None,
    ) -> None:
        welcome = config.get("welcome", {})
        if not welcome.get("enabled"):
            return
        channel = self.bot.get_channel(welcome.get("channel_id"))
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await channel.send(embed=build_welcome_embed(member, welcome, inviter))
        except discord.Forbidden:
            pass

    async def _send_return(
        self,
        member: discord.Member,
        config: dict,
        join_count: int,
        inviter: discord.Member | None = None,
    ) -> None:
        return_cfg = config.get("return", {})
        channel_id = return_cfg.get("channel_id") or config.get("welcome", {}).get("channel_id")
        channel    = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await channel.send(
                embed=build_return_embed(member, return_cfg, join_count, config.get("welcome", {}), inviter)
            )
        except discord.Forbidden:
            pass

    # ──────────────────────────────────────────────────────────────────────────
    # COMMANDES
    # ──────────────────────────────────────────────────────────────────────────

    @commands.hybrid_group(name="bienvenue", description="Configure les messages de bienvenue.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def bienvenue_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @bienvenue_group.command(name="set", description="Définit le salon de bienvenue.")
    @app_commands.describe(salon="Salon de bienvenue")
    async def bienvenue_set(self, ctx: commands.Context, salon: discord.TextChannel) -> None:
        config = await get_guild_config(ctx.guild.id, "welcome")
        config.setdefault("welcome", {})["channel_id"] = salon.id
        await set_guild_config(ctx.guild.id, "welcome", config)
        await ctx.send(f"✅ Salon de bienvenue défini : {salon.mention}")

    @bienvenue_group.command(name="banniere", description="Définit la bannière de bienvenue.")
    @app_commands.describe(url="URL de l'image")
    async def bienvenue_banniere(self, ctx: commands.Context, url: str) -> None:
        config = await get_guild_config(ctx.guild.id, "welcome")
        config.setdefault("welcome", {})["banner_url"] = url
        await set_guild_config(ctx.guild.id, "welcome", config)
        embed = discord.Embed(title="✅ Bannière définie", color=settings.COLOR_SUCCESS)
        embed.set_image(url=url)
        await ctx.send(embed=embed)

    @bienvenue_group.command(name="titre", description="Définit le titre de l'embed de bienvenue.")
    @app_commands.describe(titre="Titre (supporte {server}, {count})")
    async def bienvenue_titre(self, ctx: commands.Context, *, titre: str) -> None:
        config = await get_guild_config(ctx.guild.id, "welcome")
        config.setdefault("welcome", {})["title"] = titre
        await set_guild_config(ctx.guild.id, "welcome", config)
        await ctx.send(f"✅ Titre défini : **{titre}**")

    @bienvenue_group.command(name="description", description="Définit la description de l'embed.")
    @app_commands.describe(description="Texte (supporte {mention}, {username}, {server}, {count}, {inviter})")
    async def bienvenue_description(self, ctx: commands.Context, *, description: str) -> None:
        config = await get_guild_config(ctx.guild.id, "welcome")
        config.setdefault("welcome", {})["description"] = description
        await set_guild_config(ctx.guild.id, "welcome", config)
        await ctx.send("✅ Description définie.")

    @bienvenue_group.command(name="thumbnail", description="Active/désactive l'avatar en thumbnail.")
    @app_commands.describe(valeur="'avatar' pour l'avatar du membre, URL ou 'off' pour désactiver")
    async def bienvenue_thumbnail(self, ctx: commands.Context, valeur: str) -> None:
        config = await get_guild_config(ctx.guild.id, "welcome")
        config.setdefault("welcome", {})["thumbnail"] = None if valeur.lower() == "off" else valeur
        await set_guild_config(ctx.guild.id, "welcome", config)
        await ctx.send(f"✅ Thumbnail : **{valeur}**")

    @bienvenue_group.command(name="membres", description="Affiche/masque le compteur de membres.")
    @app_commands.describe(valeur="on ou off")
    async def bienvenue_membres(self, ctx: commands.Context, valeur: str) -> None:
        config = await get_guild_config(ctx.guild.id, "welcome")
        config.setdefault("welcome", {})["show_member_count"] = valeur.lower() == "on"
        await set_guild_config(ctx.guild.id, "welcome", config)
        await ctx.send(f"Compteur membres — {'✅ Activé' if valeur.lower() == 'on' else '❌ Désactivé'}")

    @bienvenue_group.command(name="footer", description="Définit le footer de l'embed.")
    @app_commands.describe(footer="Texte du footer")
    async def bienvenue_footer(self, ctx: commands.Context, *, footer: str) -> None:
        config = await get_guild_config(ctx.guild.id, "welcome")
        config.setdefault("welcome", {})["footer"] = footer
        await set_guild_config(ctx.guild.id, "welcome", config)
        await ctx.send(f"✅ Footer défini : **{footer}**")

    @bienvenue_group.command(name="toggle", description="Active/désactive les messages de bienvenue.")
    async def bienvenue_toggle(self, ctx: commands.Context) -> None:
        config  = await get_guild_config(ctx.guild.id, "welcome")
        current = config.get("welcome", {}).get("enabled", True)
        config.setdefault("welcome", {})["enabled"] = not current
        await set_guild_config(ctx.guild.id, "welcome", config)
        await ctx.send(f"Messages de bienvenue — {'✅ Activés' if not current else '❌ Désactivés'}")

    @bienvenue_group.command(name="test", description="Simule un message de bienvenue.")
    async def bienvenue_test(self, ctx: commands.Context) -> None:
        config = await get_guild_config(ctx.guild.id, "welcome")
        embed  = build_welcome_embed(ctx.author, config.get("welcome", {}), ctx.author)
        await ctx.send(content="📋 **Aperçu bienvenue :**", embed=embed, ephemeral=True)

    @bienvenue_group.command(name="test-retour", description="Simule un message de retour.")
    async def bienvenue_test_retour(self, ctx: commands.Context) -> None:
        config = await get_guild_config(ctx.guild.id, "welcome")
        embed  = build_return_embed(ctx.author, config.get("return", {}), 2, config.get("welcome", {}), ctx.author)
        await ctx.send(content="📋 **Aperçu retour :**", embed=embed, ephemeral=True)

    # ── Groupe /aurevoir ──

    @commands.hybrid_group(name="aurevoir", description="Configure les messages d'au revoir.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def aurevoir_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @aurevoir_group.command(name="set", description="Définit le salon d'au revoir.")
    @app_commands.describe(salon="Salon d'au revoir")
    async def aurevoir_set(self, ctx: commands.Context, salon: discord.TextChannel) -> None:
        config = await get_guild_config(ctx.guild.id, "welcome")
        config.setdefault("goodbye", {})["channel_id"] = salon.id
        await set_guild_config(ctx.guild.id, "welcome", config)
        await ctx.send(f"✅ Salon d'au revoir défini : {salon.mention}")

    @aurevoir_group.command(name="banniere", description="Définit la bannière d'au revoir.")
    @app_commands.describe(url="URL de l'image")
    async def aurevoir_banniere(self, ctx: commands.Context, url: str) -> None:
        config = await get_guild_config(ctx.guild.id, "welcome")
        config.setdefault("goodbye", {})["banner_url"] = url
        await set_guild_config(ctx.guild.id, "welcome", config)
        embed = discord.Embed(title="✅ Bannière d'au revoir définie", color=settings.COLOR_SUCCESS)
        embed.set_image(url=url)
        await ctx.send(embed=embed)

    @aurevoir_group.command(name="toggle", description="Active/désactive les messages d'au revoir.")
    async def aurevoir_toggle(self, ctx: commands.Context) -> None:
        config  = await get_guild_config(ctx.guild.id, "welcome")
        current = config.get("goodbye", {}).get("enabled", True)
        config.setdefault("goodbye", {})["enabled"] = not current
        await set_guild_config(ctx.guild.id, "welcome", config)
        await ctx.send(f"Messages d'au revoir — {'✅ Activés' if not current else '❌ Désactivés'}")

    # ── Groupe /retour ──

    @commands.hybrid_group(name="retour", description="Configure les messages de retour.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def retour_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @retour_group.command(name="set", description="Définit le salon de retour.")
    @app_commands.describe(salon="Salon de retour")
    async def retour_set(self, ctx: commands.Context, salon: discord.TextChannel) -> None:
        config = await get_guild_config(ctx.guild.id, "welcome")
        config.setdefault("return", {})["channel_id"] = salon.id
        await set_guild_config(ctx.guild.id, "welcome", config)
        await ctx.send(f"✅ Salon de retour défini : {salon.mention}")

    @retour_group.command(name="toggle", description="Active/désactive les messages de retour.")
    async def retour_toggle(self, ctx: commands.Context) -> None:
        config  = await get_guild_config(ctx.guild.id, "welcome")
        current = config.get("return", {}).get("enabled", True)
        config.setdefault("return", {})["enabled"] = not current
        await set_guild_config(ctx.guild.id, "welcome", config)
        await ctx.send(f"Messages de retour — {'✅ Activés' if not current else '❌ Désactivés'}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WelcomeCog(bot))
    logger.info("Cog 'Bienvenue' chargé.")