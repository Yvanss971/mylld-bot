# cogs/backup.py
# Système de backup automatique et manuel de la base de données MongoDB.

import json
import zipfile
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from pathlib import Path
from datetime import datetime, timezone
from io import BytesIO

from config.settings import settings
from utils.database import get_db
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.backup")

# Collections à sauvegarder
COLLECTIONS = [
    "levels",
    "warns",
    "tickets",
    "giveaways",
    "polls",
    "custom_commands",
    "economy",
    "birthdays",
    "bot_instances",
]

# Fichier de config backup
BACKUP_CONFIG_PATH = Path("config/backup_config.json")


# ──────────────────────────────────────────────
# Utilitaires
# ──────────────────────────────────────────────

def load_backup_config() -> dict:
    """Charge la configuration du backup."""
    if not BACKUP_CONFIG_PATH.exists():
        default = {
            "enabled":          True,
            "interval_hours":   12,
            "log_channel_id":   None,
            "max_backups":      10,
            "last_backup":      None
        }
        BACKUP_CONFIG_PATH.write_text(
            json.dumps(default, indent=2),
            encoding="utf-8"
        )
        return default
    with BACKUP_CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def save_backup_config(config: dict) -> None:
    with BACKUP_CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


async def export_database() -> BytesIO:
    """
    Exporte toutes les collections MongoDB dans un fichier ZIP en mémoire.
    Retourne un BytesIO contenant le ZIP.
    """
    db         = await get_db()
    zip_buffer = BytesIO()
    timestamp  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        total_docs = 0

        for collection_name in COLLECTIONS:
            try:
                cursor   = db[collection_name].find({})
                documents = await cursor.to_list(length=None)

                if not documents:
                    continue

                # Convertit les ObjectId en string
                cleaned = []
                for doc in documents:
                    doc.pop("_id", None)
                    cleaned.append(doc)

                json_data = json.dumps(cleaned, indent=2, ensure_ascii=False, default=str)
                zf.writestr(f"{collection_name}.json", json_data)
                total_docs += len(cleaned)
                logger.info(f"Backup {collection_name}: {len(cleaned)} documents")

            except Exception as e:
                logger.error(f"Erreur backup collection {collection_name}: {e}")

        # Ajoute un fichier de métadonnées
        metadata = {
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "collections": COLLECTIONS,
            "total_docs":  total_docs,
            "version":     "1.0"
        }
        zf.writestr("_metadata.json", json.dumps(metadata, indent=2))

    zip_buffer.seek(0)
    return zip_buffer, timestamp, total_docs


def format_size(size_bytes: int) -> str:
    """Formate une taille en bytes en texte lisible."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / 1024 ** 2:.1f} MB"


# ──────────────────────────────────────────────
# Cog principal
# ──────────────────────────────────────────────

class BackupCog(commands.Cog, name="Backup"):
    """Cog gérant les backups automatiques et manuels de MongoDB."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.backup_loop.start()

    def cog_unload(self) -> None:
        self.backup_loop.cancel()

    # ──────────────────────────────────────────────
    # Tâche backup automatique
    # ──────────────────────────────────────────────

    @tasks.loop(minutes=30)
    async def backup_loop(self) -> None:
        """Vérifie toutes les 30 minutes si un backup est nécessaire."""
        config = load_backup_config()

        if not config.get("enabled"):
            return

        if not config.get("log_channel_id"):
            return

        interval_hours = config.get("interval_hours", 12)
        last_backup    = config.get("last_backup")

        # Vérifie si l'intervalle est écoulé
        if last_backup:
            last_dt  = datetime.fromisoformat(last_backup).replace(tzinfo=timezone.utc) if datetime.fromisoformat(last_backup).tzinfo is None else datetime.fromisoformat(last_backup)
            elapsed  = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            if elapsed < interval_hours:
                return

        await self._do_backup(config, auto=True)

    @backup_loop.before_loop
    async def before_backup_loop(self) -> None:
        await self.bot.wait_until_ready()

    # ──────────────────────────────────────────────
    # Logique backup
    # ──────────────────────────────────────────────

    async def _do_backup(
        self,
        config: dict,
        auto:   bool = False,
        ctx:    commands.Context | None = None
    ) -> bool:
        """Effectue un backup et l'envoie dans le salon configuré."""
        channel_id = config.get("log_channel_id")
        channel    = self.bot.get_channel(channel_id)

        if not isinstance(channel, discord.TextChannel):
            logger.warning("Salon de backup introuvable ou non configuré.")
            return False

        try:
            zip_buffer, timestamp, total_docs = await export_database()
            zip_size = len(zip_buffer.getvalue())

            # Crée l'embed de backup
            embed = discord.Embed(
                title="💾 Backup MongoDB",
                description=(
                    f"**{'Automatique' if auto else 'Manuel'}**\n"
                    f"Base de données sauvegardée avec succès."
                ),
                color=settings.COLOR_SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(
                name="📦 Collections",
                value="\n".join(f"• `{c}`" for c in COLLECTIONS),
                inline=True
            )
            embed.add_field(
                name="📊 Statistiques",
                value=(
                    f"Documents : `{total_docs:,}`\n"
                    f"Taille : `{format_size(zip_size)}`\n"
                    f"Fichier : `backup_{timestamp}.zip`"
                ),
                inline=True
            )
            embed.set_footer(text="Fantoma • Backup System")

            # Envoie le zip dans le salon
            file = discord.File(
                zip_buffer,
                filename=f"backup_{timestamp}.zip"
            )
            await channel.send(embed=embed, file=file)

            # Met à jour la config
            config["last_backup"] = datetime.now(timezone.utc).isoformat()
            save_backup_config(config)

            logger.info(
                f"Backup {'auto' if auto else 'manuel'} effectué — "
                f"{total_docs} documents, {format_size(zip_size)}"
            )

            if ctx:
                await ctx.send(
                    embed=discord.Embed(
                        description=f"✅ Backup envoyé dans {channel.mention} !",
                        color=settings.COLOR_SUCCESS
                    ),
                    ephemeral=True
                )

            return True

        except Exception as e:
            logger.error(f"Erreur backup : {e}")
            if ctx:
                await ctx.send(f"❌ Erreur lors du backup : `{e}`", ephemeral=True)
            return False

    async def _restore_from_zip(
        self,
        zip_data: bytes,
        ctx: commands.Context
    ) -> None:
        """Restaure la base de données depuis un fichier ZIP."""
        db = await get_db()

        try:
            zip_buffer = BytesIO(zip_data)

            with zipfile.ZipFile(zip_buffer, "r") as zf:
                files     = zf.namelist()
                restored  = 0
                errors    = 0

                for filename in files:
                    if filename.startswith("_"):
                        continue  # Ignore les métadonnées

                    collection_name = filename.replace(".json", "")

                    if collection_name not in COLLECTIONS:
                        continue

                    try:
                        json_data  = zf.read(filename).decode("utf-8")
                        documents  = json.loads(json_data)

                        if not documents:
                            continue

                        # Vide la collection existante
                        await db[collection_name].delete_many({})

                        # Réinsère les documents
                        await db[collection_name].insert_many(documents)
                        restored += len(documents)
                        logger.info(f"Restauré {collection_name}: {len(documents)} documents")

                    except Exception as e:
                        logger.error(f"Erreur restauration {collection_name}: {e}")
                        errors += 1

            embed = discord.Embed(
                title="✅ Restauration terminée",
                description=f"`{restored}` documents restaurés avec succès.",
                color=settings.COLOR_SUCCESS if errors == 0 else settings.COLOR_WARNING,
                timestamp=datetime.now(timezone.utc)
            )

            if errors > 0:
                embed.add_field(
                    name="⚠️ Erreurs",
                    value=f"`{errors}` collection(s) ont rencontré des erreurs.",
                    inline=False
                )

            embed.set_footer(text="Fantoma • Backup System")
            await ctx.send(embed=embed)

        except zipfile.BadZipFile:
            await ctx.send("❌ Fichier ZIP invalide ou corrompu.", ephemeral=True)
        except Exception as e:
            await ctx.send(f"❌ Erreur lors de la restauration : `{e}`", ephemeral=True)
            logger.error(f"Erreur restauration : {e}")

    # ──────────────────────────────────────────────
    # Groupe /backup & &backup
    # ──────────────────────────────────────────────

    @commands.hybrid_group(
        name="backup",
        description="Gère les backups de la base de données."
    )
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def backup_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    # ── now ──

    @backup_group.command(
        name="now",
        description="Force un backup immédiat."
    )
    async def backup_now(self, ctx: commands.Context) -> None:
        await ctx.defer(ephemeral=True)
        config = load_backup_config()

        if not config.get("log_channel_id"):
            await ctx.send(
                "❌ Aucun salon de backup configuré.\n"
                "Utilise `&backup set-channel #salon` d'abord.",
                ephemeral=True
            )
            return

        await self._do_backup(config, auto=False, ctx=ctx)

    # ── set-channel ──

    @backup_group.command(
        name="set-channel",
        description="Définit le salon où envoyer les backups."
    )
    @app_commands.describe(salon="Salon dédié aux backups")
    async def backup_set_channel(
        self,
        ctx:   commands.Context,
        salon: discord.TextChannel
    ) -> None:
        config = load_backup_config()
        config["log_channel_id"] = salon.id
        save_backup_config(config)

        await ctx.send(embed=discord.Embed(
            title="✅ Salon de backup configuré",
            description=f"Les backups seront envoyés dans {salon.mention}.",
            color=settings.COLOR_SUCCESS
        ))
        logger.info(f"Salon backup défini : #{salon.name} par {ctx.author}")

    # ── interval ──

    @backup_group.command(
        name="interval",
        description="Définit l'intervalle entre les backups automatiques."
    )
    @app_commands.describe(heures="Intervalle en heures (min: 1, max: 168)")
    async def backup_interval(self, ctx: commands.Context, heures: int) -> None:
        if not 1 <= heures <= 168:
            await ctx.send("❌ L'intervalle doit être entre **1 et 168 heures**.", ephemeral=True)
            return

        config = load_backup_config()
        config["interval_hours"] = heures
        save_backup_config(config)

        await ctx.send(embed=discord.Embed(
            description=f"✅ Backup automatique toutes les **{heures} heures**.",
            color=settings.COLOR_SUCCESS
        ))

    # ── toggle ──

    @backup_group.command(
        name="toggle",
        description="Active ou désactive les backups automatiques."
    )
    async def backup_toggle(self, ctx: commands.Context) -> None:
        config  = load_backup_config()
        current = config.get("enabled", True)
        config["enabled"] = not current
        save_backup_config(config)

        state = "✅ Activés" if not current else "❌ Désactivés"
        await ctx.send(embed=discord.Embed(
            title=f"Backups automatiques — {state}",
            color=settings.COLOR_SUCCESS if not current else settings.COLOR_WARNING
        ))

    # ── status ──

    @backup_group.command(
        name="status",
        description="Affiche l'état du système de backup."
    )
    async def backup_status(self, ctx: commands.Context) -> None:
        config     = load_backup_config()
        last_backup = config.get("last_backup")
        channel_id  = config.get("log_channel_id")
        channel     = self.bot.get_channel(channel_id) if channel_id else None

        embed = discord.Embed(
            title="💾 Statut Backup",
            color=settings.COLOR_INFO,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(
            name="⚙️ Système",
            value="✅ Activé" if config.get("enabled") else "❌ Désactivé",
            inline=True
        )
        embed.add_field(
            name="⏰ Intervalle",
            value=f"`{config.get('interval_hours', 12)}h`",
            inline=True
        )
        embed.add_field(
            name="📁 Salon",
            value=channel.mention if channel else "Non configuré",
            inline=True
        )
        embed.add_field(
            name="🕐 Dernier backup",
            value=(
                f"<t:{int(datetime.fromisoformat(last_backup).timestamp())}:R>"
                if last_backup else "Jamais"
            ),
            inline=True
        )
        embed.add_field(
            name="📦 Collections",
            value=f"`{len(COLLECTIONS)}` collections",
            inline=True
        )

        # Calcule le prochain backup
        if last_backup and config.get("enabled"):
            last_dt      = datetime.fromisoformat(last_backup)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            interval_h   = config.get("interval_hours", 12)
            next_backup  = last_dt.timestamp() + (interval_h * 3600)
            embed.add_field(
                name="⏭️ Prochain backup",
                value=f"<t:{int(next_backup)}:R>",
                inline=True
            )

        embed.set_footer(text="Fantoma • Backup System")
        await ctx.send(embed=embed, ephemeral=True)

    # ── restore ──

    @backup_group.command(
        name="restore",
        description="Restaure la base de données depuis un fichier ZIP."
    )
    @app_commands.describe(message_id="ID du message contenant le backup ZIP")
    async def backup_restore(
        self,
        ctx:        commands.Context,
        message_id: str
    ) -> None:
        config     = load_backup_config()
        channel_id = config.get("log_channel_id")
        channel    = self.bot.get_channel(channel_id)

        if not isinstance(channel, discord.TextChannel):
            await ctx.send("❌ Salon de backup non configuré.", ephemeral=True)
            return

        # Confirmation avant restauration
        confirm_embed = discord.Embed(
            title="⚠️ Confirmation de restauration",
            description=(
                "**ATTENTION !** Cette action va :\n"
                "• Supprimer **toutes** les données actuelles\n"
                "• Les remplacer par le contenu du backup\n\n"
                "Cette action est **irréversible**. Confirmes-tu ?"
            ),
            color=settings.COLOR_ERROR
        )

        view = ConfirmRestoreView(ctx.author)
        msg  = await ctx.send(embed=confirm_embed, view=view)
        await view.wait()

        if not view.confirmed:
            await msg.edit(
                embed=discord.Embed(
                    description="❌ Restauration annulée.",
                    color=settings.COLOR_WARNING
                ),
                view=None
            )
            return

        await ctx.defer()

        try:
            backup_msg = await channel.fetch_message(int(message_id))
        except (discord.NotFound, discord.HTTPException):
            await ctx.send("❌ Message introuvable dans le salon de backup.", ephemeral=True)
            return

        if not backup_msg.attachments:
            await ctx.send("❌ Aucun fichier ZIP dans ce message.", ephemeral=True)
            return

        # Télécharge le ZIP
        attachment = backup_msg.attachments[0]
        if not attachment.filename.endswith(".zip"):
            await ctx.send("❌ Le fichier doit être un `.zip`.", ephemeral=True)
            return

        zip_data = await attachment.read()
        await self._restore_from_zip(zip_data, ctx)
        logger.info(f"Restauration effectuée par {ctx.author} depuis message {message_id}")


# ──────────────────────────────────────────────
# Vue de confirmation
# ──────────────────────────────────────────────

class ConfirmRestoreView(discord.ui.View):
    """Vue de confirmation pour la restauration."""

    def __init__(self, author: discord.Member):
        super().__init__(timeout=30)
        self.author    = author
        self.confirmed = False

    @discord.ui.button(label="✅ Confirmer", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user != self.author:
            await interaction.response.send_message(
                "❌ Seul l'auteur peut confirmer.", ephemeral=True
            )
            return
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="❌ Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = False
        self.stop()
        await interaction.response.defer()

    async def on_timeout(self) -> None:
        self.confirmed = False
        self.stop()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BackupCog(bot))
    logger.info("Cog 'Backup' chargé.")