# utils/bot_control.py
# Arrêt coordonné, pause persistante, détection des doublons.

import uuid
from datetime import datetime, timezone
from pathlib import Path

from utils.database import db_get, db_set
from utils.logger import setup_console_logger

logger = setup_console_logger("utils.bot_control")

LEADER_ID = "leader"
GLOBAL_ID = "global"
STATE_ID = "state"
STOP_FILE = Path("data/.bot_stopped")
HEARTBEAT_SECONDS = 5
STALE_SECONDS = 20


def new_instance_id() -> str:
    return str(uuid.uuid4())[:8]


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_paused_locally() -> bool:
    return STOP_FILE.exists()


def set_paused_local(paused: bool, reason: str = "") -> None:
    STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    if paused:
        STOP_FILE.write_text(
            f"{_now().isoformat()}\n{reason}\n",
            encoding="utf-8",
        )
        logger.info("Pause locale activée (data/.bot_stopped).")
    elif STOP_FILE.exists():
        STOP_FILE.unlink()
        logger.info("Pause locale levée.")


async def is_bot_paused() -> bool:
    """True si le bot doit rester arrêté (fichier ou MongoDB)."""
    if is_paused_locally():
        return True
    doc = await db_get("bot_control", {"_id": STATE_ID})
    return bool(doc and doc.get("paused"))


async def set_bot_paused(paused: bool, user_id: int | None = None, username: str = "") -> None:
    set_paused_local(paused, reason=f"par {username} ({user_id})" if username else "")
    await db_set("bot_control", {"_id": STATE_ID}, {
        "_id": STATE_ID,
        "paused": paused,
        "paused_by_id": str(user_id) if user_id else None,
        "paused_by_name": username or None,
        "paused_at": _now().isoformat() if paused else None,
    })


async def resume_bot(user_id: int, username: str) -> None:
    await set_bot_paused(False)
    await clear_global_shutdown()
    logger.info(f"Bot réactivé par {username} ({user_id})")


async def claim_leader(instance_id: str, allow_paused: bool = False) -> bool:
    if not allow_paused and await is_bot_paused():
        logger.warning("Bot en pause — cette instance ne démarre pas.")
        return False

    doc = await db_get("bot_control", {"_id": LEADER_ID})
    now  = _now()

    if doc and doc.get("instance_id") != instance_id:
        last = _parse_iso(doc.get("heartbeat"))
        if last and (now - last).total_seconds() < STALE_SECONDS:
            logger.warning(
                f"Instance {doc.get('instance_id')} déjà active — "
                f"cette copie ({instance_id}) va s'arrêter."
            )
            return False

    await db_set("bot_control", {"_id": LEADER_ID}, {
        "_id": LEADER_ID,
        "instance_id": instance_id,
        "heartbeat": now.isoformat(),
    })
    return True


async def heartbeat(instance_id: str) -> None:
    await db_set("bot_control", {"_id": LEADER_ID}, {
        "_id": LEADER_ID,
        "instance_id": instance_id,
        "heartbeat": _now().isoformat(),
    })


async def release_leader(instance_id: str | None = None) -> None:
    """Libère le slot leader (toute instance si instance_id est None)."""
    doc = await db_get("bot_control", {"_id": LEADER_ID})
    if doc and (instance_id is None or doc.get("instance_id") == instance_id):
        await db_set("bot_control", {"_id": LEADER_ID}, {
            "_id": LEADER_ID,
            "instance_id": None,
            "heartbeat": None,
        })


async def request_global_shutdown(user_id: int, username: str) -> None:
    await db_set("bot_control", {"_id": GLOBAL_ID}, {
        "_id": GLOBAL_ID,
        "shutdown_requested": True,
        "requested_by_id": str(user_id),
        "requested_by_name": username,
        "requested_at": _now().isoformat(),
    })
    logger.info(f"Arrêt global demandé par {username} ({user_id})")


async def clear_global_shutdown() -> None:
    await db_set("bot_control", {"_id": GLOBAL_ID}, {
        "_id": GLOBAL_ID,
        "shutdown_requested": False,
        "requested_by_id": None,
        "requested_by_name": None,
        "requested_at": None,
    })


async def is_shutdown_requested() -> bool:
    doc = await db_get("bot_control", {"_id": GLOBAL_ID})
    return bool(doc and doc.get("shutdown_requested"))


async def get_leader_info() -> dict | None:
    return await db_get("bot_control", {"_id": LEADER_ID})
