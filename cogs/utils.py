# cogs/utils.py
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

from config.settings import settings
from utils.logger import setup_console_logger
from utils.bot_control import (
    request_global_shutdown,
    set_bot_paused,
    resume_bot,
    release_leader,
    get_leader_info,
    is_bot_paused,
    is_paused_locally,
)

logger = setup_console_logger("cogs.utils")


def format_date(dt: datetime) -> str:
    return f"<t:{int(dt.timestamp())}:F> (<t:{int(dt.timestamp())}:R>)"


def format_seconds(total_seconds: int) -> str:
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    parts = []
    if days:
        parts.append(f"{days}j")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)


def get_member_badges(user: discord.User) -> str:
    flags  = user.public_flags
    badges = []
    if flags.staff:                badges.append("👨‍💼 Staff Discord")
    if flags.partner:              badges.append("🤝 Partenaire")
    if flags.hypesquad:            badges.append("🏠 HypeSquad Events")
    if flags.bug_hunter:           badges.append("🐛 Bug Hunter")
    if flags.hypesquad_bravery:    badges.append("🟣 Bravery")
    if flags.hypesquad_brilliance: badges.append("🔴 Brilliance")
    if flags.hypesquad_balance:    badges.append("🟡 Balance")
    if flags.early_supporter:      badges.append("💜 Early Supporter")
    if flags.active_developer:     badges.append("🛠️ Développeur Actif")
    return "\n".join(badges) if badges else "Aucun badge"

class EmbedModal(discord.ui.Modal, title="Créer un embed"):
    embed_title = discord.ui.TextInput(
        label="Titre", placeholder="Titre...", max_length=256
    )
    embed_description = discord.ui.TextInput(
        label="Description", style=discord.TextStyle.paragraph,
        placeholder="Contenu...", max_length=2048
    )
    embed_color = discord.ui.TextInput(
        label="Couleur (hex)", placeholder="Ex: 5865F2",
        max_length=6, required=False, default="5865F2"
    )
    embed_footer = discord.ui.TextInput(
        label="Footer (optionnel)", max_length=256, required=False
    )
    embed_image = discord.ui.TextInput(
        label="URL image (optionnel)", max_length=512, required=False
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            color = int(self.embed_color.value or "5865F2", 16)
        except ValueError:
            color = settings.COLOR_PRIMARY

        embed = discord.Embed(
            title=self.embed_title.value,
            description=self.embed_description.value,
            color=color,
            timestamp=datetime.now(timezone.utc)
        )
        if self.embed_footer.value:
            embed.set_footer(text=self.embed_footer.value)
        if self.embed_image.value:
            embed.set_image(url=self.embed_image.value)

        await interaction.response.send_message(embed=embed)


# ──────────────────────────────────────────────
# Cog
# ──────────────────────────────────────────────

def _can_control_bot(user: discord.User, guild: discord.Guild | None) -> bool:
    if settings.BOT_OWNER_ID and user.id == settings.BOT_OWNER_ID:
        return True
    if guild and user.id == guild.owner_id:
        return True
    return False


class UtilsCog(commands.Cog, name="Utilitaires"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.started_at = datetime.now(timezone.utc)

    # ── /ping & &ping ──

    @commands.hybrid_command(name="ping", description="Affiche la latence du bot.")
    @commands.guild_only()
    async def ping(self, ctx: commands.Context) -> None:
        latency = round(self.bot.latency * 1000)
        color   = (
            settings.COLOR_SUCCESS if latency < 100
            else settings.COLOR_WARNING if latency < 200
            else settings.COLOR_ERROR
        )
        embed = discord.Embed(title="🏓 Pong !", color=color, timestamp=datetime.now(timezone.utc))
        embed.add_field(name="🌐 Latence API", value=f"`{latency}ms`", inline=True)
        embed.set_footer(text="Fantoma • Bot Status")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="uptime", aliases=["up"], description="Affiche l'état rapide du bot.")
    @commands.guild_only()
    async def uptime(self, ctx: commands.Context) -> None:
        now = datetime.now(timezone.utc)
        elapsed = format_seconds(int((now - self.started_at).total_seconds()))
        latency = round(self.bot.latency * 1000)
        command_count = len([command for command in self.bot.commands if not command.hidden])

        embed = discord.Embed(
            title="📡 Uptime",
            color=settings.COLOR_SUCCESS,
            timestamp=now,
        )
        embed.add_field(name="En ligne depuis", value=format_date(self.started_at), inline=False)
        embed.add_field(name="Durée", value=f"`{elapsed}`", inline=True)
        embed.add_field(name="Latence", value=f"`{latency}ms`", inline=True)
        embed.add_field(name="Cogs", value=f"`{len(self.bot.cogs)}`", inline=True)
        embed.add_field(name="Commandes", value=f"`{command_count}`", inline=True)
        embed.set_footer(text=f"Instance {getattr(self.bot, 'instance_id', '?')} • Fantoma")
        await ctx.send(embed=embed)

    # ── /shutdown & &shutdown ──

    @commands.hybrid_command(
        name="shutdown",
        aliases=["stopbot", "arreter"],
        description="Arrête toutes les instances du bot (NexusHost, doublons, etc.).",
    )
    @commands.guild_only()
    async def shutdown(self, ctx: commands.Context) -> None:
        if not _can_control_bot(ctx.author, ctx.guild):
            await ctx.send(
                "❌ Réservé au **propriétaire du bot** (`BOT_OWNER_ID`) ou au **owner du serveur**.",
                ephemeral=True,
            )
            return

        instance_id = getattr(self.bot, "instance_id", "?")
        await set_bot_paused(True, ctx.author.id, str(ctx.author))
        await release_leader(None)
        await request_global_shutdown(ctx.author.id, str(ctx.author))

        embed = discord.Embed(
            title="🛑 Bot arrêté",
            description=(
                "Toutes les instances vont se fermer.\n\n"
                "**Sur NexusHost (obligatoire)** : va sur le panel et clique **Stop**, "
                "sinon le bot redémarre tout seul en ~5 secondes.\n\n"
                "Pour le relancer plus tard :\n"
                "1. Panel NexusHost → **Start**\n"
                "2. Puis `&startbot` sur Discord\n\n"
                f"Instance : `{instance_id}`"
            ),
            color=settings.COLOR_WARNING,
            timestamp=datetime.now(timezone.utc),
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)

        await asyncio.sleep(1)
        await self.bot.close()

    @commands.hybrid_command(
        name="startbot",
        aliases=["resumebot", "relancer"],
        description="Réactive le bot après un shutdown.",
    )
    @commands.guild_only()
    async def startbot(self, ctx: commands.Context) -> None:
        if not _can_control_bot(ctx.author, ctx.guild):
            await ctx.send("❌ Commande réservée au propriétaire.", ephemeral=True)
            return

        await resume_bot(ctx.author.id, str(ctx.author))
        activated_now = False
        activate_from_pause = getattr(self.bot, "activate_from_pause", None)
        if callable(activate_from_pause):
            activated_now = await activate_from_pause()

        embed = discord.Embed(
            title="✅ Bot réactivé",
            description=(
                "La pause est levée.\n\n" +
                (
                    "Les modules ont été rechargés sur cette instance."
                    if activated_now
                    else (
                        "Si le bot ne répond pas encore : sur NexusHost clique **Start** "
                        "(ou attends le redémarrage auto)."
                    )
                )
            ),
            color=settings.COLOR_SUCCESS,
            timestamp=datetime.now(timezone.utc),
        )
        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="botstatus",
        aliases=["instance"],
        description="Affiche quelle instance du bot est active.",
    )
    @commands.guild_only()
    async def botstatus(self, ctx: commands.Context) -> None:
        if not _can_control_bot(ctx.author, ctx.guild):
            await ctx.send("❌ Commande réservée au propriétaire.", ephemeral=True)
            return

        leader    = await get_leader_info()
        instance  = getattr(self.bot, "instance_id", "?")
        leader_id = leader.get("instance_id") if leader else None
        heartbeat = leader.get("heartbeat") if leader else None
        is_leader = leader_id == instance
        paused    = await is_bot_paused()

        embed = discord.Embed(
            title="🤖 Statut des instances",
            color=settings.COLOR_ERROR if paused else (
                settings.COLOR_SUCCESS if is_leader else settings.COLOR_WARNING
            ),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Pause",
            value="🛑 Oui (.bot_stopped)" if is_paused_locally() else (
                "🛑 Oui (MongoDB)" if paused else "✅ Non"
            ),
            inline=True,
        )
        embed.add_field(name="Cette instance", value=f"`{instance}`", inline=True)
        embed.add_field(name="Leader actif", value=f"`{leader_id or 'aucun'}`", inline=True)
        embed.add_field(
            name="Rôle",
            value="✅ Leader" if is_leader else "⚠️ Doublon",
            inline=True,
        )
        if heartbeat:
            embed.add_field(name="Dernier heartbeat", value=f"`{heartbeat}`", inline=False)
        embed.set_footer(text="Fantoma • &shutdown puis Stop sur NexusHost")
        await ctx.send(embed=embed, ephemeral=True)

    # ── /userinfo & &userinfo ──

    @commands.hybrid_command(name="userinfo", description="Affiche les informations d'un membre.")
    @commands.guild_only()
    @app_commands.describe(membre="Membre ciblé (optionnel)")
    async def userinfo(self, ctx: commands.Context, membre: discord.Member = None) -> None:
        target = membre or ctx.author

        status_map = {
            discord.Status.online:  "🟢 En ligne",
            discord.Status.idle:    "🌙 Absent",
            discord.Status.dnd:     "🔴 Ne pas déranger",
            discord.Status.offline: "⚫ Hors ligne"
        }
        status = status_map.get(target.status, "⚫ Hors ligne")
        roles  = [r.mention for r in reversed(target.roles) if r.name != "@everyone"]
        roles_text = " ".join(roles[:10]) if roles else "Aucun rôle"
        if len(roles) > 10:
            roles_text += f" *+{len(roles) - 10} autres*"

        embed = discord.Embed(
            title=f"👤 {target.display_name}",
            color=target.color if target.color.value else settings.COLOR_PRIMARY,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="🏷️ Tag",        value=f"`{target}`",       inline=True)
        embed.add_field(name="🆔 ID",          value=f"`{target.id}`",   inline=True)
        embed.add_field(name="🤖 Bot",         value="Oui" if target.bot else "Non", inline=True)
        embed.add_field(name="📶 Statut",      value=status,             inline=True)
        embed.add_field(name="🎨 Couleur",     value=f"`{target.color}`",inline=True)
        embed.add_field(name="📅 Compte créé", value=format_date(target.created_at), inline=False)
        embed.add_field(name="📥 A rejoint",   value=format_date(target.joined_at) if target.joined_at else "Inconnu", inline=False)
        embed.add_field(name="🏅 Badges",      value=get_member_badges(target), inline=False)
        embed.add_field(name=f"🎭 Rôles ({len(roles)})", value=roles_text, inline=False)
        embed.set_footer(text="Fantoma • Informations membre")
        await ctx.send(embed=embed)

    # ── /serverinfo & &serverinfo ──

    @commands.hybrid_command(name="serverinfo", description="Affiche les informations du serveur.")
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        embed = discord.Embed(
            title=f"🏰 {guild.name}",
            color=settings.COLOR_PRIMARY,
            timestamp=datetime.now(timezone.utc)
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if guild.banner:
            embed.set_image(url=guild.banner.url)

        bots   = sum(1 for m in guild.members if m.bot)
        humans = guild.member_count - bots

        embed.add_field(name="🆔 ID",        value=f"`{guild.id}`",    inline=True)
        embed.add_field(name="👑 Owner",      value=guild.owner.mention,inline=True)
        embed.add_field(name="👥 Membres",    value=f"`{humans}` humains • `{bots}` bots", inline=True)
        embed.add_field(name="💬 Salons",     value=f"`{len(guild.text_channels)}` texte • `{len(guild.voice_channels)}` vocal", inline=True)
        embed.add_field(name="🎭 Rôles",      value=f"`{len(guild.roles)}`",  inline=True)
        embed.add_field(name="😀 Emojis",     value=f"`{len(guild.emojis)}`", inline=True)
        embed.add_field(name="🚀 Boosts",     value=f"`{guild.premium_subscription_count}` (Niveau `{guild.premium_tier}`)", inline=True)
        embed.add_field(name="📅 Créé le",    value=format_date(guild.created_at), inline=False)
        embed.set_footer(text="Fantoma • Informations serveur")
        await ctx.send(embed=embed)

    # ── /avatar & &avatar ──

    @commands.hybrid_command(name="avatar", description="Affiche l'avatar d'un membre.")
    @commands.guild_only()
    @app_commands.describe(membre="Membre ciblé (optionnel)")
    async def avatar(self, ctx: commands.Context, membre: discord.Member = None) -> None:
        target = membre or ctx.author
        embed  = discord.Embed(title=f"🖼️ Avatar de {target.display_name}", color=settings.COLOR_PRIMARY)
        embed.set_image(url=target.display_avatar.url)
        embed.add_field(
            name="🔗 Liens",
            value=(
                f"[PNG]({target.display_avatar.replace(format='png').url}) • "
                f"[JPG]({target.display_avatar.replace(format='jpg').url}) • "
                f"[WEBP]({target.display_avatar.replace(format='webp').url})"
            )
        )
        await ctx.send(embed=embed)

    # ── /clear & &clear ──

    @commands.hybrid_command(name="clear", description="Supprime des messages dans ce salon.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(nombre="Nombre de messages (1-100)", membre="Filtrer par membre (optionnel)")
    async def clear(
        self, ctx: commands.Context,
        nombre: int,
        membre: discord.Member = None
    ) -> None:
        if not 1 <= nombre <= 100:
            await ctx.send("❌ Le nombre doit être entre 1 et 100.", ephemeral=True)
            return
        await ctx.defer(ephemeral=True)
        check   = (lambda m: m.author == membre) if membre else None
        deleted = await ctx.channel.purge(limit=nombre, check=check)
        await ctx.send(embed=discord.Embed(
            description=f"🗑️ `{len(deleted)}` message(s) supprimé(s).",
            color=settings.COLOR_SUCCESS
        ), ephemeral=True)
        logger.info(f"{len(deleted)} messages supprimés par {ctx.author}")

    # ── /slowmode & &slowmode ──

    @commands.hybrid_command(name="slowmode", description="Définit le slowmode du salon.")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.describe(secondes="Délai en secondes (0 = désactivé)")
    async def slowmode(self, ctx: commands.Context, secondes: int) -> None:
        if not 0 <= secondes <= 21600:
            await ctx.send("❌ Entre 0 et 21600 secondes.", ephemeral=True)
            return
        await ctx.channel.edit(slowmode_delay=secondes)
        description = "✅ Slowmode **désactivé**." if secondes == 0 else f"✅ Slowmode défini à **{secondes}s**."
        await ctx.send(embed=discord.Embed(description=description, color=settings.COLOR_SUCCESS))

    # ── /lock & &lock ──

    @commands.hybrid_command(name="lock", description="Verrouille le salon.")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.describe(raison="Raison du verrouillage (optionnel)")
    async def lock(self, ctx: commands.Context, *, raison: str = None) -> None:
        await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
        embed = discord.Embed(
            title="🔒 Salon verrouillé",
            description=raison or "Aucune raison précisée.",
            color=settings.COLOR_ERROR,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=f"Verrouillé par {ctx.author}")
        await ctx.send(embed=embed)

    # ── /unlock & &unlock ──

    @commands.hybrid_command(name="unlock", description="Déverrouille le salon.")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @app_commands.default_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context) -> None:
        await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=True)
        embed = discord.Embed(
            title="🔓 Salon déverrouillé",
            description="Le salon est de nouveau accessible.",
            color=settings.COLOR_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=f"Déverrouillé par {ctx.author}")
        await ctx.send(embed=embed)

    # ── /say & &say ──

    @commands.hybrid_command(name="say", description="Fait envoyer un message par le bot.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(message="Message à envoyer", salon="Salon cible (optionnel)")
    async def say(
        self, ctx: commands.Context, salon: discord.TextChannel = None, *, message: str
    ) -> None:
        target = salon or ctx.channel
        await target.send(message)
        await ctx.send(f"✅ Message envoyé dans {target.mention}.", ephemeral=True)

    # ── /embed & &embed ──

    @commands.hybrid_command(name="embed", description="Crée et envoie un embed personnalisé.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @app_commands.default_permissions(manage_messages=True)
    async def embed_cmd(self, ctx: commands.Context) -> None:
        if ctx.interaction:
            await ctx.interaction.response.send_modal(EmbedModal())
        else:
            await ctx.send("❌ Cette commande nécessite d'être utilisée en tant que commande slash (`/embed`).", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UtilsCog(bot))
    logger.info("Cog 'Utilitaires' chargé.")
