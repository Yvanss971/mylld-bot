"""
Importe config/tickets_config.json dans MongoDB pour le serveur configuré.
Usage : python import_tickets_config.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.database import get_db, close_db
from utils.cache import guild_config_cache


async def main():
    guild_id = int(os.getenv("GUILD_ID", 0))
    if not guild_id:
        print("❌ GUILD_ID non défini.")
        return

    config_path = Path("config/tickets_config.json")
    if not config_path.exists():
        print("❌ config/tickets_config.json introuvable.")
        return

    with open(config_path, encoding="utf-8") as f:
        tickets_config = json.load(f)

    print(f"📂 Config chargée : {len(tickets_config.get('types', []))} type(s) de ticket")

    db = await get_db()

    existing = await db["guild_configs"].find_one({"guild_id": guild_id})
    if not existing:
        print(f"⚠️  Aucune config trouvée pour guild {guild_id} — création...")
        await db["guild_configs"].insert_one({"guild_id": guild_id})

    result = await db["guild_configs"].update_one(
        {"guild_id": guild_id},
        {"$set": {"tickets": tickets_config}},
        upsert=True
    )

    await guild_config_cache.delete(f"guild_config:{guild_id}")

    if result.modified_count or result.upserted_id:
        print(f"✅ Config tickets importée dans MongoDB pour guild {guild_id}")
    else:
        print(f"ℹ️  Aucune modification (config déjà identique ?)")

    types = tickets_config.get("types", [])
    for t in types:
        print(f"   • {t['emoji']} {t['label']} (id={t['id']}, style={t.get('style','?')})")

    print("\n✅ Import terminé. Relance le bot et utilise /ticket-panel pour envoyer le panel.")
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
