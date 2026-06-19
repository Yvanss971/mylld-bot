# cogs/reglement.py
import json
import discord
from discord import app_commands
from discord.ext import commands
from pathlib import Path
from datetime import datetime, timezone

from config.settings import settings
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.reglement")

COLOR_MAP = {
    "PRIMARY": settings.COLOR_PRIMARY,
    "SUCCESS": settings.COLOR_SUCCESS,
    "WARNING": settings.COLOR_WARNING,
    "ERROR":   settings.COLOR_ERROR,
    "INFO":    settings.COLOR_INFO,
    "NEUTRAL": settings.COLOR_NEUTRAL,
}


def load_reglement() -> dict:
    path = Path("data/reglement.json")
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def build_section_embed(section: dict, server_name: str, footer_text: str) -> discord.Embed:
    color      = COLOR_MAP.get(section.get("color", "NEUTRAL"), settings.COLOR_NEUTRAL)
    rules_text = "\n".join(f"**{i+1}.** {rule}" for i, rule in enumerate(section["rules"]))
    embed = discord.Embed(title=section["title"], description=rules_text, color=color, timestamp=datetime.now(timezone.utc))
    if section.get("thumbnail"):
        embed.set_thumbnail(url=section["thumbnail"])
    embed.set_footer(text=f"{server_name} • {footer_text}")
    return embed


def build_header_embed(data: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"📋 Règlement — {data['server_name']}",
        description=(
            "Veuillez lire attentivement l'intégralité de ce règlement.\n"
            "Le respect de ces règles est **obligatoire** pour tous les membres.\n\n"
            f"*{data['footer_text']}*"
        ),
        color=settings.COLOR_PRIMARY,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"{data['server_name']} • Règlement Officiel")
    return embed


class SectionSelect(discord.ui.Select):
    def __init__(self, sections, server_name, footer_text):
        self.sections    = sections
        self.server_name = server_name
        self.footer_text = footer_text
        options = [
            discord.SelectOption(
                label=s["title"].split(" ", 1)[-1][:100],
                value=s["id"],
                emoji=s["title"].split(" ")[0],
                description=f"{len(s['rules'])} règle(s)"
            )
            for s in sections
        ]
        super().__init__(placeholder="📂 Consulter une section...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        section = next((s for s in self.sections if s["id"] == self.values[0]), None)
        if not section:
            await interaction.response.send_message("❌ Section introuvable.", ephemeral=True)
            return
        embed = build_section_embed(section, self.server_name, self.footer_text)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ReglementView(discord.ui.View):
    def __init__(self, data: dict):
        super().__init__(timeout=None)
        self.add_item(SectionSelect(data["sections"], data["server_name"], data["footer_text"]))

    @discord.ui.button(label="✅ J'ai lu et j'accepte", style=discord.ButtonStyle.success, custom_id="reglement:accept")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message("✅ Merci ! Tu peux maintenant profiter du serveur. 🎉", ephemeral=True)


class ReglementCog(commands.Cog, name="Règlement"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="reglement", description="Envoie le règlement complet du serveur.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(salon="Salon cible (optionnel)")
    async def reglement(self, ctx: commands.Context, salon: discord.TextChannel = None) -> None:
        await ctx.defer(ephemeral=True)
        target = salon or ctx.channel

        try:
            data = load_reglement()
        except FileNotFoundError as e:
            await ctx.send(f"❌ Erreur : `{e}`", ephemeral=True)
            return

        await target.send(embed=build_header_embed(data))
        for section in data["sections"]:
            await target.send(embed=build_section_embed(section, data["server_name"], data["footer_text"]))

        nav_embed = discord.Embed(
            title="🗂️ Navigation Rapide",
            description="Utilise le menu pour **consulter une section** en privé.\nClique sur le bouton vert pour confirmer.",
            color=settings.COLOR_NEUTRAL
        )
        await target.send(embed=nav_embed, view=ReglementView(data))
        await ctx.send(f"✅ Règlement envoyé dans {target.mention}.", ephemeral=True)
        logger.info(f"Règlement envoyé dans #{target.name} par {ctx.author}")

        if hasattr(self.bot, "discord_logger"):
            await self.bot.discord_logger.log_success(
                title="📋 Règlement envoyé",
                description=f"Le règlement a été posté dans {target.mention}.",
                user=ctx.author
            )

    @commands.hybrid_command(name="reglement-preview", description="Prévisualise une section du règlement.")
    @commands.guild_only()
    @app_commands.describe(section="ID de la section à prévisualiser")
    async def reglement_preview(self, ctx: commands.Context, section: str) -> None:
        try:
            data = load_reglement()
        except FileNotFoundError as e:
            await ctx.send(f"❌ `{e}`", ephemeral=True)
            return

        found = next((s for s in data["sections"] if s["id"] == section), None)
        if not found:
            ids = ", ".join(f"`{s['id']}`" for s in data["sections"])
            await ctx.send(f"❌ Section `{section}` introuvable.\n📂 Disponibles : {ids}", ephemeral=True)
            return

        embed = build_section_embed(found, data["server_name"], data["footer_text"])
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReglementCog(bot))
    logger.info("Cog 'Règlement' chargé.")