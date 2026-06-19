"""
Cache TTL générique pour le bot Fantoma.
Évite les requêtes MongoDB répétées sur les configs serveur.
"""

import time
import asyncio
from typing import Any, Optional
from collections import OrderedDict


class TTLCache:
    """
    Cache en mémoire avec expiration automatique.
    Thread-safe grâce à asyncio.Lock.
    """

    def __init__(self, ttl_seconds: float = 60.0, max_size: int = 1000):
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        """Récupère une valeur du cache si elle n'est pas expirée."""
        async with self._lock:
            if key not in self._cache:
                return None

            value, expiry = self._cache[key]
            if time.monotonic() > expiry:
                del self._cache[key]
                return None

            # Move to end (LRU)
            self._cache.move_to_end(key)
            return value

    async def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Stocke une valeur dans le cache avec TTL."""
        async with self._lock:
            expiry = time.monotonic() + (ttl or self._ttl)

            if key in self._cache:
                self._cache.move_to_end(key)

            self._cache[key] = (value, expiry)

            # Eviction LRU si dépassement
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    async def delete(self, key: str) -> bool:
        """Supprime une clé du cache. Retourne True si existait."""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    async def invalidate_pattern(self, pattern: str) -> int:
        """Invalide toutes les clés contenant un pattern. Retourne le nombre supprimé."""
        async with self._lock:
            keys_to_remove = [k for k in self._cache if pattern in k]
            for k in keys_to_remove:
                del self._cache[k]
            return len(keys_to_remove)

    async def clear(self) -> None:
        """Vide complètement le cache."""
        async with self._lock:
            self._cache.clear()

    async def stats(self) -> dict:
        """Retourne les stats du cache."""
        async with self._lock:
            now = time.monotonic()
            expired = sum(1 for _, expiry in self._cache.values() if now > expiry)
            return {
                "size": len(self._cache),
                "expired": expired,
                "max_size": self._max_size,
                "ttl_seconds": self._ttl
            }


# ── Instances globales du bot ──

# Cache des configs serveur (TTL 60s — configs changent peu)
guild_config_cache = TTLCache(ttl_seconds=60.0, max_size=500)

# Cache des données utilisateur levels (TTL 30s — données plus volatiles)
levels_cache = TTLCache(ttl_seconds=30.0, max_size=2000)

# Cache des multiplicateurs XP (TTL 120s — configs admin, très stables)
xp_multiplier_cache = TTLCache(ttl_seconds=120.0, max_size=500)
