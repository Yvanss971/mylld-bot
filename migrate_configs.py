"""
migrate_configs.py — Script de migration pour réparer les configs corrompues
À exécuter UNE FOIS avant le redémarrage du bot.

Problème : Les configs existantes ont été écrasées par les defaults à chaque restart.
Solution : Ce script merge les defaults avec les données existantes sans écraser.
"""

import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")

# Les mêmes defaults que dans guild_config.py
DEFAULT_GUILD_CONFIG = {
    "levels": {
        "enabled": True,
        "xp_per_message": 15,
        "xp_randomness": 10,
        "xp_cooldown_seconds": 60,
        "level_up_channel_id": None,
        "level_up_dm": False,
        "levels": [
            {"level": 1, "xp_required": 0},
            {"level": 2, "xp_required": 100},
            {"level": 3, "xp_required": 250},
            {"level": 4, "xp_required": 500},
            {"level": 5, "xp_required": 1000},
            {"level": 10, "xp_required": 5000},
            {"level": 20, "xp_required": 20000},
            {"level": 50, "xp_required": 100000},
        ],
        "voice_xp": {
            "enabled": True,
            "interval_seconds": 120,
            "xp_per_interval": 10,
            "ignore_afk": True,
            "ignore_muted": True,
            "ignore_deafened": True,
            "ignore_alone": True
        }
    },
    "welcome": {
        "enabled": False,
        "channel_id": None,
        "message": "Bienvenue {member} sur {server} !",
        "embed": True,
        "dm_message": None,
        "banner_url": None,
        "return_channel_id": None,
        "return_message": None,
        "leave_channel_id": None,
        "leave_message": None,
    },
    "moderation": {
        "log_channel_id": None,
        "mute_role_id": None
    },
    "economy": {
        "enabled": True,
        "daily_amount": 100,
        "work_cooldown": 3600
    },
    "automod": {
        "enabled": False,
        "rules": {},
        "exempt_channels": [],
        "exempt_roles": [],
        "log_channel_id": None,
    },
    "antiraid": {
        "enabled": False,
        "log_channel_id": None,
    },
    "backup": {
        "enabled": False,
        "channel_id": None,
        "interval_hours": 12,
    },
    "giveaway": {
        "default_duration": "1h",
    },
    "tickets": {
        "categories": {},
        "log_channel_id": None,
    },
}


def deep_merge_defaults(base: dict, defaults: dict) -> dict:
    """Merge qui ajoute les champs manquants sans écraser les existants."""
    result = dict(base)
    for key, default_value in defaults.items():
        if key not in result:
            result[key] = default_value
        elif isinstance(default_value, dict) and isinstance(result[key], dict):
            result[key] = deep_merge_defaults(result[key], default_value)
    return result


async def migrate():
    client = AsyncIOMotorClient(MONGODB_URI)
    db = client.get_default_database()

    print("🔧 Migration des configs serveur...")
    print("   Règle : ajouter les champs manquants, JAMAIS écraser les existants.\n")

    configs = await db["guild_configs"].find().to_list(length=None)

    if not configs:
        print("   ℹ️ Aucune config existante. Rien à migrer.")
        client.close()
        return

    updated = 0
    skipped = 0

    for doc in configs:
        guild_id = doc.get("guild_id", "?")

        # Retirer les métadonnées MongoDB
        existing = {k: v for k, v in doc.items() if not k.startswith("_") and k != "guild_id"}

        # Merge avec defaults
        merged = deep_merge_defaults(existing, DEFAULT_GUILD_CONFIG.copy())

        # Vérifier si des champs ont été ajoutés
        added_fields = []
        for top_key in DEFAULT_GUILD_CONFIG:
            if top_key not in existing:
                added_fields.append(top_key)

        if added_fields:
            # Mettre à jour en DB
            await db["guild_configs"].update_one(
                {"guild_id": guild_id},
                {"$set": merged}
            )
            print(f"   ✅ Serveur {guild_id} : ajouté {', '.join(added_fields)}")
            updated += 1
        else:
            print(f"   ⏭️ Serveur {guild_id} : déjà à jour")
            skipped += 1

    print(f"\n🎉 Migration terminée : {updated} mis à jour, {skipped} déjà OK")
    client.close()


if __name__ == "__main__":
    asyncio.run(migrate())
