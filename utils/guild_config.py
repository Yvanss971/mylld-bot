"""
Gestion des configurations serveur avec cache TTL.
Évite les requêtes MongoDB répétées à chaque message.
NE JAMAIS écraser les configs existantes — merge uniquement.
"""

from utils.database import get_db
from utils.cache import guild_config_cache

# ══════════════════════════════════════════════════════════════════════════════
# DEFAULTS — utilisés UNIQUEMENT pour les nouveaux serveurs ou champs manquants
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_GUILD_CONFIG = {
    "levels": {
        "enabled": True,
        "xp_per_message": 15,
        "xp_randomness": 10,
        "xp_cooldown_seconds": 60,
        "level_up_channel_id": None,
        "level_up_dm": False,
        "levels": [
            {"level": 1,  "xp_required": 0},
            {"level": 2,  "xp_required": 100},
            {"level": 3,  "xp_required": 250},
            {"level": 4,  "xp_required": 500},
            {"level": 5,  "xp_required": 1000},
            {"level": 6,  "xp_required": 1750},
            {"level": 7,  "xp_required": 2750},
            {"level": 8,  "xp_required": 4000},
            {"level": 9,  "xp_required": 5500},
            {"level": 10, "xp_required": 7500},
            {"level": 11, "xp_required": 10000},
            {"level": 12, "xp_required": 13000},
            {"level": 13, "xp_required": 16500},
            {"level": 14, "xp_required": 20500},
            {"level": 15, "xp_required": 25000},
            {"level": 16, "xp_required": 30000},
            {"level": 17, "xp_required": 36000},
            {"level": 18, "xp_required": 43000},
            {"level": 19, "xp_required": 51000},
            {"level": 20, "xp_required": 60000},
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


def deep_merge_defaults(base: dict, defaults: dict, path: str = "") -> dict:
    """
    Merge récursif qui AJOUTE les champs manquants mais NE MODIFIE JAMAIS
    les valeurs existantes. C'est le cœur de la persistance.

    Args:
        base: Config existante du serveur (peut être vide {})
        defaults: Valeurs par défaut
        path: Chemin pour le debug

    Returns:
        dict: Config fusionnée (base + defaults pour les champs manquants)
    """
    result = dict(base)  # Copie superficielle du niveau actuel

    for key, default_value in defaults.items():
        current_path = f"{path}.{key}" if path else key

        if key not in result:
            # Champ manquant → ajouter la valeur par défaut
            result[key] = default_value
        elif isinstance(default_value, dict) and isinstance(result[key], dict):
            # Les deux sont des dicts → merge récursif
            result[key] = deep_merge_defaults(result[key], default_value, current_path)
        # Sinon : result[key] existe et n'est pas un dict → on garde la valeur existante

    return result


def _strip_mongodb_meta(doc: dict) -> dict:
    """Retire les métadonnées MongoDB (_id, etc.) d'un document."""
    return {k: v for k, v in doc.items() if not k.startswith("_") and k != "guild_id"}


# ══════════════════════════════════════════════════════════════════════════════
# API Publique
# ══════════════════════════════════════════════════════════════════════════════

async def get_guild_config(guild_id: int, module: str = None) -> dict:
    """
    Récupère la config d'un serveur avec cache.

    CRITIQUE : Ne JAMAIS écraser les configs existantes.
    Merge les defaults UNIQUEMENT pour les champs manquants.

    Args:
        guild_id: ID du serveur Discord
        module: Clé spécifique à retourner (ex: "levels", "welcome"). 
                Si None, retourne toute la config.

    Returns:
        dict: Configuration fusionnée (existante + defaults pour les champs manquants)
    """
    cache_key = f"guild_config:{guild_id}"

    # 1. Vérifier le cache
    cached = await guild_config_cache.get(cache_key)
    if cached is not None:
        if module:
            result = cached.get(module, DEFAULT_GUILD_CONFIG.get(module, {}))
            if not isinstance(result, dict):
                return DEFAULT_GUILD_CONFIG.get(module, {})
            return result
        return cached

    # 2. Requête MongoDB (seulement si cache miss)
    db = await get_db()
    doc = await db["guild_configs"].find_one({"guild_id": guild_id})

    # 3. Merge avec defaults (SANS écraser les existantes)
    if doc:
        existing_config = _strip_mongodb_meta(doc)
        # Merge : existing + defaults pour les champs manquants uniquement
        full_config = deep_merge_defaults(existing_config, DEFAULT_GUILD_CONFIG.copy())
    else:
        # Nouveau serveur → defaults complets
        full_config = DEFAULT_GUILD_CONFIG.copy()
        # Créer le document en DB immédiatement
        await db["guild_configs"].insert_one({
            "guild_id": guild_id,
            **full_config
        })

    # 4. Stocker en cache
    await guild_config_cache.set(cache_key, full_config)

    if module:
        result = full_config.get(module, DEFAULT_GUILD_CONFIG.get(module, {}))
        if not isinstance(result, dict):
            return DEFAULT_GUILD_CONFIG.get(module, {})
        return result
    return full_config


async def set_guild_config(guild_id: int, module: str, data: dict) -> None:
    """
    Met à jour une section de config et invalide le cache.

    Utilise $set pour ne modifier QUE les champs fournis,
    sans toucher au reste de la config.
    """
    db = await get_db()

    # Update atomique avec $set — ne touche qu'au module spécifié
    await db["guild_configs"].update_one(
        {"guild_id": guild_id},
        {"$set": {module: data}},
        upsert=True
    )

    # Invalider le cache pour ce serveur
    await guild_config_cache.delete(f"guild_config:{guild_id}")


async def update_guild_config_field(guild_id: int, module: str, field: str, value) -> None:
    """
    Met à jour UN SEUL champ dans un module.
    Plus sûr que set_guild_config pour les updates partiels.
    """
    db = await get_db()

    await db["guild_configs"].update_one(
        {"guild_id": guild_id},
        {"$set": {f"{module}.{field}": value}},
        upsert=True
    )

    # Invalider le cache
    await guild_config_cache.delete(f"guild_config:{guild_id}")


async def reset_guild_config(guild_id: int, module: str = None) -> None:
    """
    Reset une config (ou tout) aux defaults.
    ⚠️ DESTRUCTIF — demande confirmation avant usage.
    """
    db = await get_db()

    if module:
        # Reset d'un module spécifique
        default_data = DEFAULT_GUILD_CONFIG.get(module, {})
        await db["guild_configs"].update_one(
            {"guild_id": guild_id},
            {"$set": {module: default_data}}
        )
    else:
        # Reset TOTAL — supprime et recrée
        await db["guild_configs"].delete_one({"guild_id": guild_id})
        await db["guild_configs"].insert_one({
            "guild_id": guild_id,
            **DEFAULT_GUILD_CONFIG.copy()
        })

    # Invalider le cache
    await guild_config_cache.delete(f"guild_config:{guild_id}")


async def ensure_guild_config_exists(guild_id: int) -> dict:
    """
    Vérifie qu'un serveur a une config en DB. Si non, la crée avec les defaults.
    Retourne la config complète.
    Utile à appeler dans on_guild_join ou au démarrage.
    """
    db = await get_db()

    existing = await db["guild_configs"].find_one({"guild_id": guild_id})
    if existing:
        # Merge avec defaults pour les nouveaux champs
        existing_config = _strip_mongodb_meta(existing)
        return deep_merge_defaults(existing_config, DEFAULT_GUILD_CONFIG.copy())

    # Créer la config
    config = DEFAULT_GUILD_CONFIG.copy()
    await db["guild_configs"].insert_one({
        "guild_id": guild_id,
        **config
    })
    return config
