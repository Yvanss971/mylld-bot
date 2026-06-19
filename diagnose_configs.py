"""
diagnose_configs.py — Diagnostic des configs serveur
Vérifie où sont stockées les configs et pourquoi elles disparaissent.
"""

import asyncio
import os
import json
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()
MONGODB_URI = os.getenv("MONGODB_URI")

async def diagnose():
    client = AsyncIOMotorClient(MONGODB_URI)
    db = client.get_default_database()

    print("🔍 DIAGNOSTIC CONFIGS SERVEUR")
    print("=" * 50)

    # 1. Vérifier MongoDB
    print("\n📦 MongoDB — Collection guild_configs:")
    configs = await db["guild_configs"].find().to_list(length=None)
    if configs:
        for doc in configs:
            gid = doc.get("guild_id", "?")
            has_welcome = "welcome" in doc
            has_levels = "levels" in doc
            print(f"   Serveur {gid}: welcome={has_welcome}, levels={has_levels}")
            if has_welcome:
                print(f"      → welcome.channel_id = {doc['welcome'].get('channel_id')}")
    else:
        print("   ❌ AUCUNE config trouvée en MongoDB !")

    # 2. Vérifier fichiers locaux
    print("\n📁 Fichiers locaux — dossier data/:")
    data_dir = "data"
    if os.path.exists(data_dir):
        for f in os.listdir(data_dir):
            path = os.path.join(data_dir, f)
            size = os.path.getsize(path)
            print(f"   {f}: {size} bytes")
            if size > 0 and size < 100:
                with open(path) as fh:
                    content = fh.read()
                print(f"      → Contenu: {content[:50]}")
    else:
        print("   ❌ Dossier data/ inexistant")

    # 3. Vérifier si des cogs utilisent encore JSON
    print("\n⚠️  Cogs qui pourraient utiliser JSON au lieu de MongoDB:")
    cogs_dir = "cogs"
    if os.path.exists(cogs_dir):
        for f in os.listdir(cogs_dir):
            if f.endswith('.py'):
                with open(os.path.join(cogs_dir, f), 'r') as fh:
                    content = fh.read()
                if 'json.dump' in content or 'json.load' in content:
                    print(f"   ⚠️  {f} utilise json.dump/load — risque de perte de données!")
                if 'guild_configs' in content and 'json' not in content:
                    print(f"   ✅ {f} semble utiliser MongoDB")

    print("\n" + "=" * 50)
    print("💡 SOLUTION: Toutes les configs doivent être en MongoDB (guild_configs)")
    print("   Les fichiers JSON locaux sont perdus à chaque restart sur NexusHost.")

    client.close()

if __name__ == "__main__":
    asyncio.run(diagnose())
