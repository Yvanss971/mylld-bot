"""
Script à exécuter UNE FOIS pour créer les index MongoDB nécessaires.
À lancer avec : python create_indexes.py
"""

import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")


async def create_indexes():
    client = AsyncIOMotorClient(MONGODB_URI)
    db = client.get_default_database()

    print("🔧 Création des index MongoDB...")

    # Index pour levels — ESSENTIEL pour get_rank() et leaderboard()
    await db["levels"].create_index([("guild_id", 1), ("xp", -1)])
    print("  ✅ levels: (guild_id, xp DESC)")

    await db["levels"].create_index([("guild_id", 1), ("user_id", 1)], unique=True)
    print("  ✅ levels: (guild_id, user_id) UNIQUE")

    # Index pour guild_configs
    await db["guild_configs"].create_index("guild_id", unique=True)
    print("  ✅ guild_configs: guild_id UNIQUE")

    # Index pour economy (si pas déjà fait)
    await db["economy"].create_index([("guild_id", 1), ("user_id", 1)], unique=True)
    print("  ✅ economy: (guild_id, user_id) UNIQUE")

    # Index pour invites
    await db["invite_counts"].create_index([("guild_id", 1), ("user_id", 1)], unique=True)
    print("  ✅ invite_counts: (guild_id, user_id) UNIQUE")

    print("\n🎉 Tous les index sont créés !")
    client.close()


if __name__ == "__main__":
    asyncio.run(create_indexes())
