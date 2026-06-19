# cogs/tickets.py
import json
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from pathlib import Path
from datetime import datetime, timezone

from config.settings import settings
from utils.database import db_get, db_set, db_get_all
from utils.guild_config import get_guild_config, set_guild_config
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.tickets")

TRANSCRIPTS_DIR = Path("data/transcripts")
DATA_COLLECTION = "tickets"

COLOR_MAP = {
    "PRIMARY": settings.COLOR_PRIMARY,
    "SUCCESS": settings.COLOR_SUCCESS,
    "WARNING": settings.COLOR_WARNING,
    "ERROR":   settings.COLOR_ERROR,
    "INFO":    settings.COLOR_INFO,
    "NEUTRAL": settings.COLOR_NEUTRAL,
}
STYLE_MAP = {
    "danger":    discord.ButtonStyle.danger,
    "success":   discord.ButtonStyle.success,
    "primary":   discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
}


async def load_config(guild_id: int) -> dict:
    return await get_guild_config(guild_id, "tickets")


async def save_config(guild_id: int, config: dict) -> None:
    await set_guild_config(guild_id, "tickets", config)


def get_user_open_tickets(user_id: int, guild_id: int, data: list) -> list:
    return [
        t["message_id"] for t in data
        if t.get("user_id") == user_id
        and t.get("guild_id") == guild_id
        and t.get("open")
    ]


async def generate_transcript(channel: discord.TextChannel) -> Path:
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    messages = []
    async for msg in channel.history(limit=500, oldest_first=True):
        messages.append(msg)

    rows = ""
    for msg in messages:
        content = discord.utils.escape_markdown(msg.content) if msg.content else ""
        rows += f"""
        <div class="message">
            <img class="avatar" src="{msg.author.display_avatar.url}">
            <div class="content">
                <span class="author">{msg.author.display_name}</span>
                <span class="time">{msg.created_at.strftime("%d/%m/%Y %H:%M")}</span>
                <div class="text">{content}</div>
            </div>
        </div>"""

    html = f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
    <title>Transcript — {channel.name}</title>
    <style>body{{font-family:'Segoe UI',sans-serif;background:#1e1f22;color:#dcddde;padding:20px}}
    h1{{color:#5865f2}}.message{{display:flex;gap:12px;padding:8px 0;border-bottom:1px solid #2b2d31}}
    .avatar{{width:40px;height:40px;border-radius:50%}}.author{{font-weight:bold;color:#fff;margin-right:8px}}
    .time{{font-size:11px;color:#72767d}}.text{{margin-top:4px;white-space:pre-wrap}}</style></head>
    <body><h1>🎫 #{channel.name}</h1><p>{len(messages)} message(s)</p>{rows}</body></html>"""

    path = TRANSCRIPTS_DIR / f"{channel.name}.html"
    path.write_text(html, encoding="utf-8")
    return path


class TicketTypeButton(discord.ui.Button):
    def __init__(self, ticket_type: dict):
        super().__init__(
            label=ticket_type["label"],
            emoji=ticket_type["emoji"],
            style=STYLE_MAP.get(ticket_type.get("style", "secondary"), discord.ButtonStyle.secondary),
            custom_id=f"ticket:open:{ticket_type['id']}"
        )
        self.ticket_type = ticket_type

    async def callback(self, interaction: discord.Interaction) -> None:
        await TicketsCog.handle_open_ticket(interaction, self.ticket_type)


class TicketPanelView(discord.ui.View):
    def __init__(self, ticket_types: list):
        super().__init__(timeout=None)
        for t in ticket_types:
            self.add_item(TicketTypeButton(t))


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fermer le ticket", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="ticket:close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await TicketsCog.handle_close_ticket(interaction, with_transcript=True)

    @discord.ui.button(label="Fermer sans transcript", emoji="🗑️", style=discord.ButtonStyle.secondary, custom_id="ticket:close_no_transcript")
    async def close_no_transcript(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await TicketsCog.handle_close_ticket(interaction, with_transcript=False)


class TicketsCog(commands.Cog, name="Tickets"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(TicketControlView())
        asyncio.create_task(self._restore_panel_views())

    async def _restore_panel_views(self) -> None:
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            config = await load_config(guild.id)
            types  = config.get("types", [])
            if types:
                self.bot.add_view(TicketPanelView(types))

    @staticmethod
    async def handle_open_ticket(interaction: discord.Interaction, ticket_type: dict) -> None:
        await interaction.response.defer(ephemeral=True)
        config   = await load_config(interaction.guild.id)
        guild    = interaction.guild
        member   = interaction.user

        if not config.get("enabled"):
            await interaction.followup.send("❌ Le système de tickets est désactivé.", ephemeral=True)
            return

        all_tickets  = await db_get_all(DATA_COLLECTION, {"guild_id": guild.id, "open": True})
        open_tickets = [t for t in all_tickets if t.get("user_id") == member.id]
        if len(open_tickets) >= config.get("max_tickets_per_user", 1):
            channel = guild.get_channel(int(open_tickets[0].get("channel_id", 0)))
            await interaction.followup.send(
                f"❌ Tu as déjà un ticket ouvert : {channel.mention if channel else 'ticket existant'}",
                ephemeral=True
            )
            return

        category   = guild.get_channel(config["category_id"]) if config.get("category_id") else None
        staff_role = guild.get_role(ticket_type["staff_role_id"]) if ticket_type.get("staff_role_id") else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member:             discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
        }
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        ticket_name = config["ticket"]["name_format"].replace("{type}", ticket_type["id"]).replace("{username}", member.name.lower())

        try:
            channel = await guild.create_text_channel(name=ticket_name, category=category, overwrites=overwrites)
        except discord.Forbidden:
            await interaction.followup.send("❌ Permission insuffisante.", ephemeral=True)
            return

        await db_set(DATA_COLLECTION, {"channel_id": channel.id}, {
            "guild_id":    guild.id,
            "channel_id":  channel.id,
            "user_id":     member.id,
            "username":    str(member),
            "type":        ticket_type["id"],
            "type_label":  ticket_type["label"],
            "open":        True,
            "opened_at":   datetime.now(timezone.utc).isoformat()
        })

        color = COLOR_MAP.get(ticket_type.get("color", "PRIMARY"), settings.COLOR_PRIMARY)
        embed = discord.Embed(
            title=f"{ticket_type['emoji']} Ticket — {ticket_type['label']}",
            description=ticket_type["welcome_message"].replace("{mention}", member.mention),
            color=color,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="👤 Membre", value=member.mention,       inline=True)
        embed.add_field(name="📋 Type",   value=ticket_type["label"], inline=True)
        embed.add_field(name="📅 Ouvert", value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>", inline=True)
        embed.set_footer(text=f"{guild.name} • Support")

        mention_text = member.mention + (f" | {staff_role.mention}" if staff_role else "")
        await channel.send(content=mention_text, embed=embed, view=TicketControlView())
        await interaction.followup.send(f"✅ Ticket **{ticket_type['label']}** créé : {channel.mention}", ephemeral=True)
        logger.info(f"Ticket '{ticket_name}' ouvert par {member} sur {guild.name}")

    @staticmethod
    async def handle_close_ticket(ctx_or_interaction, with_transcript: bool = True) -> None:
        is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
        channel = ctx_or_interaction.channel
        user    = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
        guild   = channel.guild

        config  = await load_config(guild.id)
        ticket  = await db_get(DATA_COLLECTION, {"channel_id": channel.id})

        if not ticket:
            msg = "❌ Ce salon n'est pas un ticket."
            if is_interaction:
                await ctx_or_interaction.response.send_message(msg, ephemeral=True)
            else:
                await ctx_or_interaction.send(msg)
            return

        if is_interaction:
            await ctx_or_interaction.response.defer()
        else:
            await ctx_or_interaction.defer()

        embed = discord.Embed(
            title="🔒 Fermeture du ticket...",
            description="Ce salon sera supprimé dans quelques secondes.",
            color=settings.COLOR_WARNING,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Fermé par", value=user.mention,                    inline=True)
        embed.add_field(name="Type",      value=ticket.get("type_label", "?"),   inline=True)

        if is_interaction:
            await ctx_or_interaction.followup.send(embed=embed)
        else:
            await ctx_or_interaction.send(embed=embed)

        if with_transcript and config.get("ticket", {}).get("save_transcript"):
            try:
                path = await generate_transcript(channel)
                file = discord.File(path, filename=f"{channel.name}.html")
                log_channel = guild.get_channel(config.get("log_channel_id"))
                if isinstance(log_channel, discord.TextChannel):
                    log_embed = discord.Embed(
                        title="📄 Transcript",
                        description=f"Ticket **{channel.name}** fermé.",
                        color=settings.COLOR_INFO,
                        timestamp=datetime.now(timezone.utc)
                    )
                    log_embed.add_field(name="Ouvert par", value=f"<@{ticket['user_id']}>", inline=True)
                    log_embed.add_field(name="Fermé par",  value=user.mention,              inline=True)
                    log_embed.add_field(name="Type",       value=ticket.get("type_label", "?"), inline=True)
                    await log_channel.send(embed=log_embed, file=file)
            except Exception as e:
                logger.error(f"Erreur transcript : {e}")

        await db_set(DATA_COLLECTION, {"channel_id": channel.id}, {
            **ticket,
            "open":      False,
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "closed_by": str(user)
        })

        await asyncio.sleep(3)
        try:
            await channel.delete(reason=f"Ticket fermé par {user}")
        except discord.Forbidden:
            pass

    @commands.hybrid_command(name="ticket-panel", description="Envoie le panneau de tickets.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(salon="Salon cible (optionnel)")
    async def ticket_panel(self, ctx: commands.Context, salon: discord.TextChannel = None) -> None:
        await ctx.defer(ephemeral=True)
        config = await load_config(ctx.guild.id)
        panel  = config.get("panel", {})
        types  = config.get("types", [])
        target = salon or ctx.channel
        color  = COLOR_MAP.get(panel.get("color", "PRIMARY"), settings.COLOR_PRIMARY)

        embed = discord.Embed(title=panel.get("title", "🎫 Support"), description=panel.get("description", ""), color=color, timestamp=datetime.now(timezone.utc))
        for t in types:
            embed.add_field(name=f"{t['emoji']} **{t['label']}**", value=f"» {t['description']}", inline=False)
        embed.set_footer(text=f"{ctx.guild.name} • Support")

        await target.send(embed=embed, view=TicketPanelView(types))
        await ctx.send(f"✅ Panneau envoyé dans {target.mention}.", ephemeral=True)

    @commands.hybrid_group(name="ticket", description="Gère le ticket courant.")
    @commands.guild_only()
    async def ticket_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @ticket_group.command(name="fermer", description="Ferme ce ticket.")
    async def ticket_fermer(self, ctx: commands.Context) -> None:
        await TicketsCog.handle_close_ticket(ctx, with_transcript=True)

    @ticket_group.command(name="ajouter", description="Ajoute un membre au ticket.")
    @app_commands.describe(membre="Membre à ajouter")
    async def ticket_ajouter(self, ctx: commands.Context, membre: discord.Member) -> None:
        ticket = await db_get(DATA_COLLECTION, {"channel_id": ctx.channel.id})
        if not ticket:
            await ctx.send("❌ Ce salon n'est pas un ticket.", ephemeral=True)
            return
        await ctx.channel.set_permissions(membre, view_channel=True, send_messages=True, read_message_history=True)
        await ctx.send(embed=discord.Embed(description=f"✅ {membre.mention} ajouté.", color=settings.COLOR_SUCCESS))

    @ticket_group.command(name="retirer", description="Retire un membre du ticket.")
    @app_commands.describe(membre="Membre à retirer")
    async def ticket_retirer(self, ctx: commands.Context, membre: discord.Member) -> None:
        ticket = await db_get(DATA_COLLECTION, {"channel_id": ctx.channel.id})
        if not ticket:
            await ctx.send("❌ Ce salon n'est pas un ticket.", ephemeral=True)
            return
        await ctx.channel.set_permissions(membre, view_channel=False)
        await ctx.send(embed=discord.Embed(description=f"✅ {membre.mention} retiré.", color=settings.COLOR_WARNING))

    @commands.hybrid_group(name="ticket-config", description="Configure le système de tickets.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def ticket_config_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @ticket_config_group.command(name="categorie", description="Définit la catégorie des tickets.")
    @app_commands.describe(categorie="Catégorie Discord")
    async def config_categorie(self, ctx: commands.Context, categorie: discord.CategoryChannel) -> None:
        config = await load_config(ctx.guild.id)
        config["category_id"] = categorie.id
        await save_config(ctx.guild.id, config)
        await ctx.send(f"✅ Catégorie définie : **{categorie.name}**", ephemeral=True)

    @ticket_config_group.command(name="logs", description="Définit le salon de logs.")
    @app_commands.describe(salon="Salon de logs")
    async def config_logs(self, ctx: commands.Context, salon: discord.TextChannel) -> None:
        config = await load_config(ctx.guild.id)
        config["log_channel_id"] = salon.id
        await save_config(ctx.guild.id, config)
        await ctx.send(f"✅ Logs définis dans {salon.mention}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketsCog(bot))
    logger.info("Cog 'Tickets' chargé.")