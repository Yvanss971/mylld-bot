# cogs/roles.py
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

from config.settings import settings
from utils.guild_config import get_guild_config, set_guild_config
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.roles")


class RoleButton(discord.ui.Button):
    def __init__(self, role_id: int, label: str, emoji: str):
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, custom_id=f"role:{role_id}")
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction) -> None:
        role = interaction.guild.get_role(self.role_id)
        if not role:
            await interaction.response.send_message("❌ Rôle introuvable.", ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(f"✅ Rôle **{role.name}** retiré.", ephemeral=True)
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"✅ Rôle **{role.name}** attribué !", ephemeral=True)


class RolePanelView(discord.ui.View):
    def __init__(self, group: dict):
        super().__init__(timeout=None)
        for r in group["roles"]:
            if r["role_id"] is not None:
                self.add_item(RoleButton(r["role_id"], r["label"], r["emoji"]))


class RolesCog(commands.Cog, name="Rôles"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        config      = await get_guild_config(member.guild.id, "roles")
        autorole_id = config.get("autorole_id")
        if not autorole_id:
            return
        role = member.guild.get_role(autorole_id)
        if not role:
            return
        try:
            await member.add_roles(role, reason="Autorole automatique")
            logger.info(f"Autorole '{role.name}' → {member} sur {member.guild.name}")
        except discord.Forbidden:
            logger.error(f"Permission refusée pour autorole → {member}")

    @commands.Cog.listener()
    async def on_level_up(self, member: discord.Member, new_level: int) -> None:
        config = await get_guild_config(member.guild.id, "roles")
        for entry in config.get("level_roles", []):
            if entry["role_id"] and entry["level"] == new_level:
                role = member.guild.get_role(entry["role_id"])
                if role:
                    try:
                        await member.add_roles(role, reason=f"Niveau {new_level}")
                        logger.info(f"Rôle niveau '{role.name}' → {member}")
                    except discord.Forbidden:
                        pass

    @commands.hybrid_command(name="roles-panel", description="Envoie le panneau de rôles au choix.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(salon="Salon cible (optionnel)")
    async def roles_panel(self, ctx: commands.Context, salon: discord.TextChannel = None) -> None:
        await ctx.defer(ephemeral=True)
        config = await get_guild_config(ctx.guild.id, "roles")
        target = salon or ctx.channel

        for group in config.get("choice_roles", []):
            valid = [r for r in group["roles"] if r["role_id"] is not None]
            if not valid:
                continue
            roles_list = "\n".join(f"{r['emoji']} **{r['label']}**" for r in valid)
            embed = discord.Embed(
                title=f"🎭 {group['label']}",
                description=f"{group['description']}\n\n{roles_list}\n\n*Clique pour obtenir ou retirer un rôle.*",
                color=settings.COLOR_PRIMARY,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text=f"{ctx.guild.name} • Rôles au choix")
            await target.send(embed=embed, view=RolePanelView(group))

        await ctx.send(f"✅ Panneau envoyé dans {target.mention}.", ephemeral=True)

    @commands.hybrid_group(name="autorole", description="Configure le rôle automatique à l'arrivée.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def autorole_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @autorole_group.command(name="set", description="Définit le rôle automatique.")
    @app_commands.describe(role="Le rôle à attribuer automatiquement")
    async def autorole_set(self, ctx: commands.Context, role: discord.Role) -> None:
        config = await get_guild_config(ctx.guild.id, "roles")
        config["autorole_id"] = role.id
        await set_guild_config(ctx.guild.id, "roles", config)
        await ctx.send(embed=discord.Embed(
            title="✅ Autorole configuré",
            description=f"{role.mention} sera attribué aux nouveaux membres.",
            color=settings.COLOR_SUCCESS
        ))

    @autorole_group.command(name="remove", description="Supprime le rôle automatique.")
    async def autorole_remove(self, ctx: commands.Context) -> None:
        config = await get_guild_config(ctx.guild.id, "roles")
        config["autorole_id"] = None
        await set_guild_config(ctx.guild.id, "roles", config)
        await ctx.send(embed=discord.Embed(
            title="🗑️ Autorole supprimé",
            description="Aucun rôle ne sera plus attribué automatiquement.",
            color=settings.COLOR_WARNING
        ))

    @autorole_group.command(name="info", description="Affiche l'autorole actuel.")
    async def autorole_info(self, ctx: commands.Context) -> None:
        config      = await get_guild_config(ctx.guild.id, "roles")
        autorole_id = config.get("autorole_id")
        if not autorole_id:
            await ctx.send("ℹ️ Aucun autorole configuré.", ephemeral=True)
            return
        role = ctx.guild.get_role(autorole_id)
        name = role.mention if role else f"Rôle introuvable (`{autorole_id}`)"
        await ctx.send(embed=discord.Embed(
            title="ℹ️ Autorole actuel",
            description=f"Rôle attribué à l'arrivée : {name}",
            color=settings.COLOR_INFO
        ), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RolesCog(bot))
    logger.info("Cog 'Rôles' chargé.")