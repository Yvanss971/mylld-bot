# cogs/startup_config.py
"""
Cog de démarrage : vérifie que tous les serveurs ont une config persistante.
À charger en PREMIER dans main.py pour être sûr que tout est prêt.
"""

import discord
from discord.ext import commands
from utils.database import get_db
from utils.guild_config import DEFAULT_GUILD_CONFIG, deep_merge_defaults
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.startup_config")


class StartupConfigCog(commands.Cog, name="Startup Config"):
    """
    Ce cog s'assure que :
    1. Chaque serveur a une config en DB
    2. Les configs existantes ne sont JAMAIS écrasées
    3. Les nouveaux champs (updates) sont ajoutés sans toucher aux existants
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        """Au démarrage : vérifie tous les serveurs."""
        logger.info("🔧 Vérification des configs serveur au démarrage...")

        db = await get_db()
        checked = 0
        created = 0
        updated = 0

        for guild in self.bot.guilds:
            guild_id = guild.id

            # Chercher la config existante
            doc = await db["guild_configs"].find_one({"guild_id": guild_id})

            if not doc:
                # Pas de config → créer avec les defaults
                await db["guild_configs"].insert_one({
                    "guild_id": guild_id,
                    **DEFAULT_GUILD_CONFIG.copy()
                })
                logger.info(f"   ✅ Nouvelle config créée pour {guild.name} ({guild_id})")
                created += 1
            else:
                # Config existe → merge avec les nouveaux defaults (sans écraser)
                existing = {k: v for k, v in doc.items() if not k.startswith("_") and k != "guild_id"}
                merged = deep_merge_defaults(existing, DEFAULT_GUILD_CONFIG.copy())

                # Vérifier si des champs ont été ajoutés
                needs_update = False
                for key in DEFAULT_GUILD_CONFIG:
                    if key not in existing:
                        needs_update = True
                        break
                    # Vérifier aussi les sous-champs
                    if isinstance(DEFAULT_GUILD_CONFIG[key], dict):
                        for sub_key in DEFAULT_GUILD_CONFIG[key]:
                            if sub_key not in existing.get(key, {}):
                                needs_update = True
                                break

                if needs_update:
                    await db["guild_configs"].update_one(
                        {"guild_id": guild_id},
                        {"$set": merged}
                    )
                    logger.info(f"   🔄 Config mise à jour pour {guild.name} ({guild_id}) — nouveaux champs ajoutés")
                    updated += 1
                else:
                    logger.debug(f"   ⏭️ Config OK pour {guild.name} ({guild_id})")

            checked += 1

        logger.info(f"🔧 Configs vérifiées : {checked} serveurs | {created} créés | {updated} mis à jour")

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Quand le bot rejoint un nouveau serveur → créer la config."""
        db = await get_db()

        existing = await db["guild_configs"].find_one({"guild_id": guild.id})
        if not existing:
            await db["guild_configs"].insert_one({
                "guild_id": guild.id,
                **DEFAULT_GUILD_CONFIG.copy()
            })
            logger.info(f"✅ Config créée pour nouveau serveur : {guild.name} ({guild.id})")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StartupConfigCog(bot))
    logger.info("Cog 'Startup Config' chargé.")
