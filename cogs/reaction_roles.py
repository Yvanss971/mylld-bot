# cogs/reaction_roles.py
# Rôles réactifs basés sur les réactions emoji.

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from config.settings import settings
from utils.database import db_delete, db_get, db_get_all, db_set
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.reaction_roles")

COLLECTION = "reaction_roles"


def emoji_key(emoji: str | discord.PartialEmoji) -> str:
    return str(emoji).strip()


def build_panel_embed(panel: dict) -> discord.Embed:
    options = panel.get("options", [])
    if options:
        lines = [
            f"{option['emoji']} → <@&{option['role_id']}>"
            + (f" — {option['label']}" if option.get("label") else "")
            for option in options
        ]
        roles_text = "\n".join(lines)
    else:
        roles_text = "Aucun rôle configuré pour le moment."

    embed = discord.Embed(
        title=panel.get("title") or "🎭 Rôles réactifs",
        description=(panel.get("description") or "Réagis avec un emoji pour obtenir ou retirer un rôle.")
        + "\n\n"
        + roles_text,
        color=settings.COLOR_PRIMARY,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Fantoma • Rôles réactifs")
    return embed


async def fetch_panel_message(bot: commands.Bot, panel: dict) -> discord.Message | None:
    channel = bot.get_channel(int(panel["channel_id"]))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(panel["channel_id"]))
        except discord.HTTPException:
            return None

    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return None

    try:
        return await channel.fetch_message(int(panel["message_id"]))
    except discord.HTTPException:
        return None


class ReactionRolesCog(commands.Cog, name="Rôles réactifs"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _can_manage_role(self, guild: discord.Guild, role: discord.Role) -> tuple[bool, str]:
        if role.is_default():
            return False, "❌ Le rôle @everyone ne peut pas être utilisé."
        if guild.me and role >= guild.me.top_role:
            return False, "❌ Mon rôle doit être au-dessus du rôle à attribuer."
        if role.managed:
            return False, "❌ Ce rôle est géré par une intégration et ne peut pas être attribué."
        return True, ""

    async def _save_panel(self, panel: dict) -> None:
        await db_set(COLLECTION, {
            "guild_id": panel["guild_id"],
            "message_id": panel["message_id"],
        }, panel)

    async def _update_panel_message(self, panel: dict) -> bool:
        message = await fetch_panel_message(self.bot, panel)
        if not message:
            return False

        try:
            await message.edit(embed=build_panel_embed(panel))
            return True
        except discord.HTTPException as e:
            logger.warning(f"Impossible de mettre à jour le panel {panel['message_id']} : {e}")
            return False

    @commands.hybrid_group(name="reaction-role", aliases=["rr"], description="Gère les rôles réactifs par emoji.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @app_commands.default_permissions(manage_roles=True)
    async def reaction_role_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @reaction_role_group.command(name="create", description="Crée un panel de rôles réactifs.")
    @app_commands.describe(
        salon="Salon où envoyer le panel",
        titre="Titre du panel",
        description="Description du panel",
    )
    async def rr_create(
        self,
        ctx: commands.Context,
        salon: discord.TextChannel,
        titre: str = "🎭 Rôles réactifs",
        *,
        description: str = "Réagis avec un emoji pour obtenir ou retirer un rôle."
    ) -> None:
        panel = {
            "guild_id": str(ctx.guild.id),
            "channel_id": str(salon.id),
            "message_id": "pending",
            "title": titre,
            "description": description,
            "options": [],
            "created_by": str(ctx.author.id),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        message = await salon.send(embed=build_panel_embed(panel))
        panel["message_id"] = str(message.id)
        await self._save_panel(panel)
        await ctx.send(f"✅ Panel créé dans {salon.mention}. ID message : `{message.id}`", ephemeral=True)

    @reaction_role_group.command(name="add", description="Ajoute une option emoji → rôle à un panel.")
    @app_commands.describe(
        message_id="ID du message panel",
        emoji="Emoji à utiliser",
        role="Rôle à attribuer",
        label="Texte optionnel affiché dans le panel",
    )
    async def rr_add(
        self,
        ctx: commands.Context,
        message_id: str,
        emoji: str,
        role: discord.Role,
        *,
        label: str | None = None,
    ) -> None:
        ok, error = self._can_manage_role(ctx.guild, role)
        if not ok:
            await ctx.send(error, ephemeral=True)
            return

        panel = await db_get(COLLECTION, {"guild_id": str(ctx.guild.id), "message_id": message_id})
        if not panel:
            await ctx.send("❌ Panel introuvable.", ephemeral=True)
            return

        key = emoji_key(emoji)
        options = panel.get("options", [])
        if any(option["emoji"] == key for option in options):
            await ctx.send("❌ Cet emoji est déjà utilisé sur ce panel.", ephemeral=True)
            return
        if any(option["role_id"] == str(role.id) for option in options):
            await ctx.send("❌ Ce rôle est déjà présent sur ce panel.", ephemeral=True)
            return
        if len(options) >= 20:
            await ctx.send("❌ Maximum 20 rôles par panel.", ephemeral=True)
            return

        message = await fetch_panel_message(self.bot, panel)
        if not message:
            await ctx.send("❌ Message panel introuvable ou inaccessible.", ephemeral=True)
            return

        try:
            await message.add_reaction(key)
        except discord.HTTPException:
            await ctx.send("❌ Emoji invalide ou inaccessible par le bot.", ephemeral=True)
            return

        options.append({
            "emoji": key,
            "role_id": str(role.id),
            "label": label or role.name,
        })
        panel["options"] = options
        panel["updated_by"] = str(ctx.author.id)
        panel["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self._save_panel(panel)
        await self._update_panel_message(panel)
        await ctx.send(f"✅ {key} donnera/retirera {role.mention}.", ephemeral=True)

    @reaction_role_group.command(name="remove", description="Retire une option emoji d'un panel.")
    @app_commands.describe(message_id="ID du message panel", emoji="Emoji à retirer")
    async def rr_remove(self, ctx: commands.Context, message_id: str, emoji: str) -> None:
        panel = await db_get(COLLECTION, {"guild_id": str(ctx.guild.id), "message_id": message_id})
        if not panel:
            await ctx.send("❌ Panel introuvable.", ephemeral=True)
            return

        key = emoji_key(emoji)
        options = panel.get("options", [])
        new_options = [option for option in options if option["emoji"] != key]
        if len(new_options) == len(options):
            await ctx.send("❌ Emoji introuvable sur ce panel.", ephemeral=True)
            return

        panel["options"] = new_options
        panel["updated_by"] = str(ctx.author.id)
        panel["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self._save_panel(panel)
        await self._update_panel_message(panel)
        await ctx.send(f"✅ Option {key} retirée.", ephemeral=True)

    @reaction_role_group.command(name="list", description="Liste les panels de rôles réactifs.")
    async def rr_list(self, ctx: commands.Context) -> None:
        panels = await db_get_all(COLLECTION, {"guild_id": str(ctx.guild.id)})
        if not panels:
            await ctx.send("ℹ️ Aucun panel de rôles réactifs configuré.", ephemeral=True)
            return

        embed = discord.Embed(title="🎭 Panels de rôles réactifs", color=settings.COLOR_INFO, timestamp=datetime.now(timezone.utc))
        for panel in panels[:10]:
            embed.add_field(
                name=f"`{panel['message_id']}` — {panel.get('title', 'Panel')}",
                value=f"Salon : <#{panel['channel_id']}>\nOptions : `{len(panel.get('options', []))}`",
                inline=False,
            )
        await ctx.send(embed=embed, ephemeral=True)

    @reaction_role_group.command(name="refresh", description="Met à jour l'embed d'un panel.")
    @app_commands.describe(message_id="ID du message panel")
    async def rr_refresh(self, ctx: commands.Context, message_id: str) -> None:
        panel = await db_get(COLLECTION, {"guild_id": str(ctx.guild.id), "message_id": message_id})
        if not panel:
            await ctx.send("❌ Panel introuvable.", ephemeral=True)
            return
        ok = await self._update_panel_message(panel)
        await ctx.send("✅ Panel mis à jour." if ok else "❌ Message panel introuvable.", ephemeral=True)

    @reaction_role_group.command(name="delete", description="Supprime la configuration d'un panel.")
    @app_commands.describe(message_id="ID du message panel")
    async def rr_delete(self, ctx: commands.Context, message_id: str) -> None:
        await db_delete(COLLECTION, {"guild_id": str(ctx.guild.id), "message_id": message_id})
        await ctx.send(f"✅ Configuration du panel `{message_id}` supprimée.", ephemeral=True)

    async def _handle_reaction(self, payload: discord.RawReactionActionEvent, add: bool) -> None:
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return

        panel = await db_get(COLLECTION, {
            "guild_id": str(payload.guild_id),
            "message_id": str(payload.message_id),
        })
        if not panel:
            return

        key = emoji_key(payload.emoji)
        option = next((item for item in panel.get("options", []) if item["emoji"] == key), None)
        if not option:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        role = guild.get_role(int(option["role_id"]))
        if not role:
            return

        member = guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                return
        if member.bot:
            return

        try:
            if add:
                await member.add_roles(role, reason="Rôle réactif Fantoma")
            else:
                await member.remove_roles(role, reason="Rôle réactif Fantoma")
        except discord.HTTPException as e:
            logger.warning(f"Impossible de modifier {role} pour {member} : {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle_reaction(payload, add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle_reaction(payload, add=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRolesCog(bot))
    logger.info("Cog 'Rôles réactifs' chargé.")
