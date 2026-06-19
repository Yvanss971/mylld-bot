# cogs/advanced_logs.py
# Logs avancés configurables pour les événements importants du serveur.

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from config.settings import settings
from utils.database import db_get, db_set
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.advanced_logs")

COLLECTION = "advanced_logs"


def truncate(value: str | None, limit: int = 900) -> str:
    if not value:
        return "*Aucun contenu texte*"
    clean = value.replace("`", "\\`")
    return clean if len(clean) <= limit else clean[: limit - 3] + "..."


def format_date(dt: datetime) -> str:
    return f"<t:{int(dt.timestamp())}:F> (<t:{int(dt.timestamp())}:R>)"


class AdvancedLogsCog(commands.Cog, name="Logs avancés"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_config(self, guild_id: int) -> dict:
        doc = await db_get(COLLECTION, {"guild_id": str(guild_id)})
        if doc:
            return doc
        return {
            "guild_id": str(guild_id),
            "enabled": bool(settings.LOG_CHANNEL_ID),
            "channel_id": str(settings.LOG_CHANNEL_ID) if settings.LOG_CHANNEL_ID else None,
        }

    async def _send_log(self, guild: discord.Guild, embed: discord.Embed) -> None:
        config = await self._get_config(guild.id)
        if not config.get("enabled"):
            return

        channel_id = config.get("channel_id")
        if not channel_id:
            return

        channel = self.bot.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return

        try:
            await channel.send(embed=embed)
        except discord.HTTPException as e:
            logger.warning(f"Impossible d'envoyer un log avancé : {e}")

    @commands.hybrid_group(name="logs", description="Configure les logs avancés.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def logs_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @logs_group.command(name="set", description="Définit le salon des logs avancés.")
    @app_commands.describe(salon="Salon qui recevra les logs")
    async def logs_set(self, ctx: commands.Context, salon: discord.TextChannel) -> None:
        await db_set(COLLECTION, {"guild_id": str(ctx.guild.id)}, {
            "guild_id": str(ctx.guild.id),
            "enabled": True,
            "channel_id": str(salon.id),
            "updated_by": str(ctx.author.id),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        await ctx.send(f"✅ Logs avancés activés dans {salon.mention}.", ephemeral=True)

    @logs_group.command(name="toggle", description="Active ou désactive les logs avancés.")
    @app_commands.describe(actif="Activer ou désactiver les logs")
    async def logs_toggle(self, ctx: commands.Context, actif: bool) -> None:
        config = await self._get_config(ctx.guild.id)
        config["enabled"] = actif
        config["guild_id"] = str(ctx.guild.id)
        await db_set(COLLECTION, {"guild_id": str(ctx.guild.id)}, config)
        await ctx.send(f"Logs avancés : {'✅ activés' if actif else '❌ désactivés'}.", ephemeral=True)

    @logs_group.command(name="status", description="Affiche la configuration des logs avancés.")
    async def logs_status(self, ctx: commands.Context) -> None:
        config = await self._get_config(ctx.guild.id)
        channel_id = config.get("channel_id")
        channel_text = f"<#{channel_id}>" if channel_id else "Non défini"
        embed = discord.Embed(title="🔔 Logs avancés", color=settings.COLOR_INFO, timestamp=datetime.now(timezone.utc))
        embed.add_field(name="État", value="✅ Activés" if config.get("enabled") else "❌ Désactivés", inline=True)
        embed.add_field(name="Salon", value=channel_text, inline=True)
        await ctx.send(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return

        embed = discord.Embed(
            title="🗑️ Message supprimé",
            color=settings.COLOR_WARNING,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Auteur", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
        embed.add_field(name="Salon", value=message.channel.mention, inline=True)
        embed.add_field(name="Contenu", value=truncate(message.content), inline=False)
        if message.attachments:
            embed.add_field(
                name="Pièces jointes",
                value="\n".join(att.url for att in message.attachments[:5]),
                inline=False,
            )
        embed.set_footer(text="Fantoma • Logs avancés")
        await self._send_log(message.guild, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if not before.guild or before.author.bot or before.content == after.content:
            return

        embed = discord.Embed(
            title="✏️ Message modifié",
            color=settings.COLOR_INFO,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Auteur", value=f"{before.author.mention} (`{before.author.id}`)", inline=False)
        embed.add_field(name="Salon", value=before.channel.mention, inline=True)
        embed.add_field(name="Avant", value=truncate(before.content), inline=False)
        embed.add_field(name="Après", value=truncate(after.content), inline=False)
        embed.add_field(name="Lien", value=f"[Aller au message]({after.jump_url})", inline=False)
        embed.set_footer(text="Fantoma • Logs avancés")
        await self._send_log(before.guild, embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        embed = discord.Embed(
            title="👋 Membre rejoint",
            description=f"{member.mention} (`{member.id}`)",
            color=settings.COLOR_SUCCESS,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Compte créé", value=format_date(member.created_at), inline=False)
        embed.add_field(name="Membres", value=f"`{member.guild.member_count}`", inline=True)
        embed.set_footer(text="Fantoma • Logs avancés")
        await self._send_log(member.guild, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        embed = discord.Embed(
            title="🚪 Membre quitté",
            description=f"{member.mention} (`{member.id}`)",
            color=settings.COLOR_NEUTRAL,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Avait rejoint", value=format_date(member.joined_at) if member.joined_at else "Inconnu", inline=False)
        embed.add_field(name="Rôles", value=f"`{max(0, len(member.roles) - 1)}`", inline=True)
        embed.add_field(name="Membres restants", value=f"`{member.guild.member_count}`", inline=True)
        embed.set_footer(text="Fantoma • Logs avancés")
        await self._send_log(member.guild, embed)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.nick != after.nick:
            embed = discord.Embed(
                title="🏷️ Pseudonyme modifié",
                description=after.mention,
                color=settings.COLOR_INFO,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Avant", value=before.nick or before.name, inline=True)
            embed.add_field(name="Après", value=after.nick or after.name, inline=True)
            embed.set_footer(text="Fantoma • Logs avancés")
            await self._send_log(after.guild, embed)

        before_roles = set(before.roles)
        after_roles = set(after.roles)
        added = [role for role in after_roles - before_roles if role.name != "@everyone"]
        removed = [role for role in before_roles - after_roles if role.name != "@everyone"]

        if not added and not removed:
            return

        embed = discord.Embed(
            title="🎭 Rôles modifiés",
            description=after.mention,
            color=settings.COLOR_PRIMARY,
            timestamp=datetime.now(timezone.utc),
        )
        if added:
            embed.add_field(name="Ajoutés", value=" ".join(role.mention for role in added[:10]), inline=False)
        if removed:
            embed.add_field(name="Retirés", value=" ".join(role.mention for role in removed[:10]), inline=False)
        embed.set_footer(text="Fantoma • Logs avancés")
        await self._send_log(after.guild, embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdvancedLogsCog(bot))
    logger.info("Cog 'Logs avancés' chargé.")
