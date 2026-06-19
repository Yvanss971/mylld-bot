# main.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from pathlib import Path
from datetime import datetime, timezone

from config.settings import settings
from utils.logger import setup_console_logger, DiscordLogger
from utils.database import get_db, close_db

logger = setup_console_logger("main")

intents = discord.Intents.default()
intents.message_content = True
intents.members         = True

Fantoma_GUILD_ID = settings.GUILD_ID


async def get_prefix(bot, message):
    return commands.when_mentioned_or("&")(bot, message)


def cleanup_empty_json_files() -> None:
    """
    Supprime les fichiers JSON vides pour éviter qu'ils n'écrasent les configs MongoDB.
    Les fichiers JSON locaux sont perdus à chaque restart sur NexusHost.
    Les configs serveur doivent être en MongoDB (guild_configs).
    """
    data_dir = Path("data")
    if not data_dir.exists():
        return

    removed = 0
    for fpath in data_dir.glob("*.json"):
        size = fpath.stat().st_size
        if size <= 2:  # 2 bytes = "{}" ou "[]" (fichier vide)
            fpath.unlink()
            removed += 1
            logger.info(f"🗑️ Fichier JSON vide supprimé : {fpath.name}")

    if removed > 0:
        logger.info(f"🗑️ {removed} fichier(s) JSON vide(s) supprimé(s) — configs en MongoDB uniquement")


def ensure_data_files() -> None:
    """
    S'assure que les dossiers nécessaires existent.
    NE PLUS créer de fichiers JSON vides — tout est en MongoDB.
    """
    Path("data").mkdir(exist_ok=True)
    Path("data/transcripts").mkdir(exist_ok=True)

    # Supprimer les anciens fichiers JSON vides (migration)
    cleanup_empty_json_files()

    # Les données sont maintenant en MongoDB — pas de fichiers JSON locaux
    logger.info("✅ Dossiers data/ vérifiés — configs en MongoDB")


async def migrate_levels_to_guild() -> None:
    db     = await get_db()
    result = await db["levels"].count_documents({"guild_id": {"$exists": False}})
    if result == 0:
        logger.info("Migration levels : déjà à jour.")
        return
    logger.info(f"Migration levels : {result} documents à migrer...")
    await db["levels"].update_many(
        {"guild_id": {"$exists": False}},
        {"$set": {"guild_id": Fantoma_GUILD_ID}}
    )
    logger.info(f"✅ Migration levels terminée — {result} documents mis à jour.")


async def migrate_warns_to_guild() -> None:
    db     = await get_db()
    result = await db["warns"].count_documents({"guild_id": {"$exists": False}})
    if result == 0:
        logger.info("Migration warns : déjà à jour.")
        return
    logger.info(f"Migration warns : {result} documents à migrer...")
    await db["warns"].update_many(
        {"guild_id": {"$exists": False}},
        {"$set": {"guild_id": Fantoma_GUILD_ID}}
    )
    logger.info(f"✅ Migration warns terminée — {result} documents mis à jour.")


class BotClient(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix=get_prefix,
            intents=intents,
            help_command=None,
            case_insensitive=True
        )
        self.discord_logger: DiscordLogger | None = None

    async def setup_hook(self) -> None:
        await self._load_cogs()
        guild  = discord.Object(id=settings.GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        logger.info(f"{len(synced)} commande(s) slash synchronisée(s).")

    async def _load_cogs(self) -> None:
        cogs_dir = Path("cogs")

        # Charger startup_config EN PREMIER pour initialiser les configs
        startup_file = cogs_dir / "startup_config.py"
        if startup_file.exists():
            try:
                await self.load_extension("cogs.startup_config")
                logger.info("✅ Cog chargé : cogs.startup_config (prioritaire)")
            except Exception as e:
                logger.error(f"❌ Échec chargement cogs.startup_config : {e}")

        # Charger les autres cogs
        for cog_file in cogs_dir.glob("*.py"):
            if cog_file.stem.startswith("_") or cog_file.stem == "startup_config":
                continue
            module = f"cogs.{cog_file.stem}"
            try:
                await self.load_extension(module)
                logger.info(f"✅ Cog chargé : {module}")
            except Exception as e:
                logger.error(f"❌ Échec chargement {module} : {e}")

    async def on_ready(self) -> None:
        await get_db()
        await migrate_levels_to_guild()
        await migrate_warns_to_guild()

        self.discord_logger = DiscordLogger(self)
        logger.info("=" * 50)
        logger.info(f"  Bot connecté : {self.user} (ID: {self.user.id})")
        logger.info(f"  Serveurs     : {len(self.guilds)}")
        logger.info(f"  Latence      : {round(self.latency * 1000)}ms")
        logger.info("=" * 50)

        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Fantoma | &help"
            )
        )

        await self.discord_logger.log_success(
            title="✅ Bot en ligne",
            description=f"**{self.user}** connecté sur `{len(self.guilds)}` serveur(s).",
            fields=[
                {"name": "Serveurs", "value": str(len(self.guilds)), "inline": True},
                {"name": "Latence",  "value": f"{round(self.latency * 1000)}ms", "inline": True},
            ]
        )

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            msg = "❌ Tu n'as pas les permissions nécessaires."
        elif isinstance(error, app_commands.CommandOnCooldown):
            msg = f"⏳ Réessaie dans `{error.retry_after:.1f}s`."
        else:
            msg = "❌ Une erreur inattendue s'est produite."
            logger.error(f"Erreur slash [{interaction.command}] : {error}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Tu n'as pas les permissions nécessaires.")
        elif isinstance(error, commands.CommandNotFound):
            pass
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Argument manquant : `{error.param.name}`")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ Argument invalide.")
        else:
            logger.error(f"Erreur commande [{ctx.command}] : {error}")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        from utils.guild_config import ensure_guild_config_exists
        # Utiliser ensure_guild_config_exists qui crée si manquant, ne modifie jamais
        await ensure_guild_config_exists(guild.id)
        logger.info(f"✅ Config initialisée pour nouveau serveur '{guild.name}' ({guild.id})")


async def main() -> None:
    ensure_data_files()
    retry_delay = 60
    max_retries = 10

    for attempt in range(max_retries):
        try:
            async with BotClient() as bot:
                await bot.start(settings.TOKEN)
            break
        except discord.errors.HTTPException as e:
            if e.status == 429:
                wait_time = retry_delay * (attempt + 1)
                logger.warning(f"Rate limit 429 — attente {wait_time}s (tentative {attempt + 1}/{max_retries})")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"Erreur HTTP Discord : {e}")
                raise
        except Exception as e:
            logger.error(f"Erreur démarrage : {e}")
            raise
    else:
        logger.error("Impossible de démarrer après plusieurs tentatives.")


if __name__ == "__main__":
    asyncio.run(main())
