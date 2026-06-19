# cogs/blacklist.py
# Système de blacklist : ban automatique dès qu'un utilisateur rejoint le serveur.

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

from config.settings import settings
from utils.database import db_get, db_set, db_delete, db_get_all
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.blacklist")

COLLECTION = "blacklist"


# ──────────────────────────────────────────────
# Cog principal
# ──────────────────────────────────────────────

class BlacklistCog(commands.Cog, name="Blacklist"):
    """Cog gérant la blacklist — ban automatique à l'arrivée."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ──────────────────────────────────────────────
    # Listener — Ban automatique à l'arrivée
    # ──────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Ban automatiquement un membre blacklisté dès son arrivée."""
        entry = await db_get(COLLECTION, {
            "guild_id": member.guild.id,
            "user_id":  member.id
        })

        if not entry:
            return

        raison   = entry.get("raison", "Blacklisté")
        added_by = entry.get("added_by_name", "Inconnu")

        try:
            # Essaie de prévenir le membre en DM avant le ban
            try:
                embed_dm = discord.Embed(
                    title="🚫 Tu as été banni de Fantoma",
                    description=(
                        f"Tu es sur la blacklist de ce serveur.\n"
                        f"**Raison :** {raison}"
                    ),
                    color=settings.COLOR_ERROR
                )
                await member.send(embed=embed_dm)
            except discord.Forbidden:
                pass

            # Ban immédiat
            await member.ban(
                reason=f"[BLACKLIST] {raison} — Ajouté par {added_by}",
                delete_message_days=1
            )

            # Incrémente le compteur de tentatives
            entry["attempts"] = entry.get("attempts", 0) + 1
            entry["last_attempt"] = datetime.now(timezone.utc).isoformat()
            await db_set(COLLECTION, {"guild_id": member.guild.id, "user_id": member.id}, entry)

            logger.info(f"[BLACKLIST] {member} ({member.id}) banni automatiquement.")

            # Log dans le salon configuré
            if hasattr(self.bot, "discord_logger"):
                await self.bot.discord_logger.log_error(
                    title="🚫 Membre blacklisté banni",
                    description=f"**{member}** (`{member.id}`) a tenté de rejoindre.",
                    fields=[
                        {"name": "Raison",       "value": raison,    "inline": True},
                        {"name": "Ajouté par",   "value": added_by,  "inline": True},
                        {"name": "Tentatives",   "value": str(entry["attempts"]), "inline": True},
                    ],
                    user=member
                )

        except discord.Forbidden:
            logger.error(f"Permission refusée pour bannir {member} (blacklist).")

    # ──────────────────────────────────────────────
    # Groupe /blacklist & &blacklist
    # ──────────────────────────────────────────────

    @commands.hybrid_group(
        name="blacklist",
        aliases=["bl"],
        description="Gère la blacklist du serveur."
    )
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def blacklist_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    # ── add ──

    @blacklist_group.command(
        name="add",
        description="Ajoute un utilisateur à la blacklist."
    )
    @app_commands.describe(
        user_id="ID Discord de l'utilisateur",
        raison="Raison du blacklist"
    )
    async def blacklist_add(
        self,
        ctx:     commands.Context,
        user_id: str,
        *,
        raison:  str = "Aucune raison précisée"
    ) -> None:
        # Vérifie que l'ID est valide
        try:
            uid = int(user_id)
        except ValueError:
            await ctx.send("❌ ID invalide. Donne un ID Discord valide.", ephemeral=True)
            return

        # Vérifie si déjà blacklisté
        existing = await db_get(COLLECTION, {"guild_id": ctx.guild.id, "user_id": uid})
        if existing:
            await ctx.send(
                f"❌ L'utilisateur `{uid}` est déjà dans la blacklist.",
                ephemeral=True
            )
            return

        # Essaie de récupérer le nom de l'utilisateur
        try:
            user = await self.bot.fetch_user(uid)
            username = str(user)
        except discord.NotFound:
            username = f"Inconnu ({uid})"

        # Sauvegarde dans MongoDB
        await db_set(COLLECTION, {"guild_id": ctx.guild.id, "user_id": uid}, {
            "guild_id":     ctx.guild.id,
            "user_id":      uid,
            "username":     username,
            "raison":       raison,
            "added_by":     ctx.author.id,
            "added_by_name": str(ctx.author),
            "added_at":     datetime.now(timezone.utc).isoformat(),
            "attempts":     0,
            "last_attempt": None
        })

        # Ban si le membre est sur le serveur
        member = ctx.guild.get_member(uid)
        if member:
            try:
                await member.ban(
                    reason=f"[BLACKLIST] {raison}",
                    delete_message_days=1
                )
                ban_status = "✅ Banni immédiatement"
            except discord.Forbidden:
                ban_status = "⚠️ Déjà banni ou permission refusée"
        else:
            ban_status = "ℹ️ Pas sur le serveur — sera banni à son arrivée"

        embed = discord.Embed(
            title="🚫 Utilisateur blacklisté",
            color=settings.COLOR_ERROR,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="👤 Utilisateur", value=f"`{username}`",  inline=True)
        embed.add_field(name="🆔 ID",          value=f"`{uid}`",       inline=True)
        embed.add_field(name="📋 Raison",       value=raison,          inline=False)
        embed.add_field(name="⚡ Statut",       value=ban_status,      inline=False)
        embed.set_footer(text=f"Ajouté par {ctx.author}")

        await ctx.send(embed=embed)
        logger.info(f"[BLACKLIST] {username} ({uid}) ajouté par {ctx.author} — {raison}")

    # ── remove ──

    @blacklist_group.command(
        name="remove",
        aliases=["del", "delete"],
        description="Retire un utilisateur de la blacklist."
    )
    @app_commands.describe(user_id="ID Discord de l'utilisateur")
    async def blacklist_remove(self, ctx: commands.Context, user_id: str) -> None:
        try:
            uid = int(user_id)
        except ValueError:
            await ctx.send("❌ ID invalide.", ephemeral=True)
            return

        entry = await db_get(COLLECTION, {"guild_id": ctx.guild.id, "user_id": uid})
        if not entry:
            await ctx.send(
                f"❌ L'utilisateur `{uid}` n'est pas dans la blacklist.",
                ephemeral=True
            )
            return

        await db_delete(COLLECTION, {"guild_id": ctx.guild.id, "user_id": uid})

        # Unban si nécessaire
        try:
            await ctx.guild.unban(
                discord.Object(id=uid),
                reason=f"Retiré de la blacklist par {ctx.author}"
            )
            unban_status = "✅ Débanni du serveur"
        except discord.NotFound:
            unban_status = "ℹ️ Pas dans les bans du serveur"
        except discord.Forbidden:
            unban_status = "⚠️ Permission refusée pour débannir"

        embed = discord.Embed(
            title="✅ Utilisateur retiré de la blacklist",
            color=settings.COLOR_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="🆔 ID",       value=f"`{uid}`",           inline=True)
        embed.add_field(name="👤 Nom",       value=entry.get("username", "Inconnu"), inline=True)
        embed.add_field(name="⚡ Statut",    value=unban_status,         inline=False)
        embed.set_footer(text=f"Retiré par {ctx.author}")

        await ctx.send(embed=embed)
        logger.info(f"[BLACKLIST] {uid} retiré par {ctx.author}")

    # ── list ──

    @blacklist_group.command(
        name="list",
        description="Affiche tous les utilisateurs blacklistés."
    )
    async def blacklist_list(self, ctx: commands.Context) -> None:
        entries = await db_get_all(COLLECTION, {"guild_id": ctx.guild.id})

        if not entries:
            await ctx.send("ℹ️ La blacklist est vide.", ephemeral=True)
            return

        # Trie par date d'ajout
        entries.sort(key=lambda x: x.get("added_at", ""), reverse=True)

        embed = discord.Embed(
            title=f"🚫 Blacklist ({len(entries)} utilisateur(s))",
            color=settings.COLOR_ERROR,
            timestamp=datetime.now(timezone.utc)
        )

        for entry in entries[:20]:
            attempts = entry.get("attempts", 0)
            added_at = entry.get("added_at", "")
            date_str = f"<t:{int(datetime.fromisoformat(added_at).timestamp())}:R>" if added_at else "Inconnu"

            embed.add_field(
                name=f"🔴 {entry.get('username', 'Inconnu')}",
                value=(
                    f"ID : `{entry['user_id']}`\n"
                    f"Raison : {entry.get('raison', 'Aucune')}\n"
                    f"Ajouté : {date_str}\n"
                    f"Tentatives : `{attempts}`"
                ),
                inline=True
            )

        if len(entries) > 20:
            embed.set_footer(text=f"Fantoma • Affichage limité à 20 / {len(entries)} total")
        else:
            embed.set_footer(text="Fantoma • Blacklist")

        await ctx.send(embed=embed)

    # ── check ──

    @blacklist_group.command(
        name="check",
        description="Vérifie si un utilisateur est dans la blacklist."
    )
    @app_commands.describe(user_id="ID Discord à vérifier")
    async def blacklist_check(self, ctx: commands.Context, user_id: str) -> None:
        try:
            uid = int(user_id)
        except ValueError:
            await ctx.send("❌ ID invalide.", ephemeral=True)
            return

        entry = await db_get(COLLECTION, {"guild_id": ctx.guild.id, "user_id": uid})

        if not entry:
            embed = discord.Embed(
                title="✅ Utilisateur non blacklisté",
                description=f"`{uid}` n'est pas dans la blacklist.",
                color=settings.COLOR_SUCCESS
            )
        else:
            added_at    = entry.get("added_at", "")
            last_attempt = entry.get("last_attempt")
            date_str    = f"<t:{int(datetime.fromisoformat(added_at).timestamp())}:F>" if added_at else "Inconnu"
            last_str    = f"<t:{int(datetime.fromisoformat(last_attempt).timestamp())}:R>" if last_attempt else "Jamais"

            embed = discord.Embed(
                title="🚫 Utilisateur blacklisté",
                color=settings.COLOR_ERROR,
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="👤 Nom",          value=entry.get("username", "Inconnu"), inline=True)
            embed.add_field(name="🆔 ID",            value=f"`{uid}`",                      inline=True)
            embed.add_field(name="📋 Raison",        value=entry.get("raison", "Aucune"),   inline=False)
            embed.add_field(name="📅 Ajouté le",     value=date_str,                        inline=True)
            embed.add_field(name="👤 Ajouté par",    value=entry.get("added_by_name", "Inconnu"), inline=True)
            embed.add_field(name="🔄 Tentatives",    value=f"`{entry.get('attempts', 0)}`", inline=True)
            embed.add_field(name="⏰ Dernière tentative", value=last_str,                   inline=True)

        embed.set_footer(text="Fantoma • Blacklist")
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BlacklistCog(bot))
    logger.info("Cog 'Blacklist' chargé.")