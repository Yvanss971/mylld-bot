# utils/database.py
import motor.motor_asyncio
from config.settings import settings
from utils.logger import setup_console_logger

logger = setup_console_logger("utils.database")

_client = None
_db     = None


async def get_db():
    global _client, _db
    if _db is None:
        _client = motor.motor_asyncio.AsyncIOMotorClient(settings.MONGODB_URI)
        _db = _client.mylld_bot
        logger.info("✅ Connexion MongoDB établie.")
    return _db


async def close_db():
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db     = None
        logger.info("MongoDB déconnecté.")


async def db_get(collection: str, query: dict) -> dict | None:
    db  = await get_db()
    doc = await db[collection].find_one(query)
    return doc


async def db_set(collection: str, query: dict, data: dict) -> None:
    db = await get_db()
    await db[collection].update_one(query, {"$set": data}, upsert=True)


async def db_delete(collection: str, query: dict) -> None:
    db = await get_db()
    await db[collection].delete_one(query)


async def db_get_all(collection: str, query: dict = {}) -> list:
    db      = await get_db()
    cursor  = db[collection].find(query)
    results = await cursor.to_list(length=None)
    return results


async def db_get_guild(collection: str, guild_id: int, query: dict = {}) -> dict | None:
    full_query = {"guild_id": guild_id, **query}
    return await db_get(collection, full_query)


async def db_set_guild(collection: str, guild_id: int, query: dict, data: dict) -> None:
    full_query       = {"guild_id": guild_id, **query}
    data["guild_id"] = guild_id
    await db_set(collection, full_query, data)


async def db_get_all_guild(collection: str, guild_id: int, query: dict = {}) -> list:
    full_query = {"guild_id": guild_id, **query}
    return await db_get_all(collection, full_query)