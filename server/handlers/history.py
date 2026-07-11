import uuid
import time

from server.config import Config
from server.session import Session
from server.managers.message_router import MessageRouter
from server.managers.room_manager import RoomManager
from shared.error_codes import ErrorCode
import asyncpg


async def handle_history_request(
    data: dict,
    session: Session,
    router: MessageRouter,
    room_manager: RoomManager,
    db_pool: asyncpg.Pool,
):
    """
    Obsługuje HISTORY_REQUEST od klienta.

    Zwraca trwałą historię wiadomości pobraną z tabeli `messages`, dzięki czemu
    wiadomości (zarówno w pokojach, jak i DM-y) są widoczne w GUI po ponownym
    zalogowaniu, a nie tylko przechowywane tymczasowo w pamięci klienta.

    payload:
      - history_type: "room" | "dm"
      - room_id:  wymagane dla "room"
      - username: wymagane dla "dm" (nazwa drugiej strony konwersacji)
      - limit:    opcjonalne, domyślnie HISTORY_DEFAULT_LIMIT (maks. HISTORY_MAX_LIMIT)
    """
    msg_id = data.get("msg_id")
    payload = data.get("payload") or {}
    history_type = payload.get("history_type")

    limit = payload.get("limit") or Config.HISTORY_DEFAULT_LIMIT
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = Config.HISTORY_DEFAULT_LIMIT
    limit = max(1, min(limit, Config.HISTORY_MAX_LIMIT))

    if history_type == "room":
        await _handle_room_history(data, session, router, room_manager, db_pool, payload, limit, msg_id)
    elif history_type == "dm":
        await _handle_dm_history(data, session, router, db_pool, payload, limit, msg_id)
    else:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Invalid or missing history_type", msg_id)


async def _handle_room_history(data, session, router, room_manager, db_pool,
                                payload, limit, msg_id):
    room_id = payload.get("room_id")
    if room_id is None:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Missing room_id", msg_id)
        return

    room = await room_manager.get_room(room_id)
    if room is None:
        await router.send_error(session.writer, ErrorCode.ROOM_NOT_FOUND,
                                ErrorCode.get_message(ErrorCode.ROOM_NOT_FOUND), msg_id)
        return

    # Dostęp: aktualny (in-memory) członek pokoju LUB uprawniony wg ACL/publiczny/bez wyrzucenia.
    # Administratorzy mają zawsze dostęp do historii.
    has_access = (session.role == "admin") \
        or room_manager.is_member(room_id, session.user_id) \
        or await room_manager.check_access(room_id, session.user_id)
    if not has_access:
        await router.send_error(session.writer, ErrorCode.FORBIDDEN,
                                "Not a member of this room", msg_id)
        return

    rows = await db_pool.fetch(
        """
        SELECT m.msg_id, m.body, m.sent_at, u.id AS from_user_id, u.username AS from_user
        FROM messages m
        JOIN users u ON u.id = m.sender_id
        WHERE m.room_id = $1
        ORDER BY m.sent_at DESC
        LIMIT $2
        """,
        room_id, limit
    )

    messages = [_row_to_message(r) for r in reversed(rows)]

    import logging
    logger = logging.getLogger("rcmp.server")
    logger.info("HISTORY_REQUEST room=%s: znaleziono %d wiadomości dla użytkownika %s",
                room_id, len(messages), session.username)

    response = {
        "type": "HISTORY_RESPONSE",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": session.token,
        "payload": {
            "history_type": "room",
            "room_id": room_id,
            "room_name": room["name"],
            "messages": messages,
        }
    }
    await router._write(session.writer, response)


async def _handle_dm_history(data, session, router, db_pool, payload, limit, msg_id):
    target_username = payload.get("username", "")
    if not target_username:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Missing username", msg_id)
        return

    row = await db_pool.fetchrow(
        "SELECT id FROM users WHERE username = $1", target_username
    )
    if not row:
        await router.send_error(session.writer, ErrorCode.USER_NOT_FOUND,
                                f"User '{target_username}' not found", msg_id)
        return

    target_id = row["id"]

    rows = await db_pool.fetch(
        """
        SELECT m.msg_id, m.body, m.sent_at, u.id AS from_user_id, u.username AS from_user
        FROM messages m
        JOIN users u ON u.id = m.sender_id
        WHERE (m.sender_id = $1 AND m.recipient_id = $2)
           OR (m.sender_id = $2 AND m.recipient_id = $1)
        ORDER BY m.sent_at DESC
        LIMIT $3
        """,
        session.user_id, target_id, limit
    )

    messages = [_row_to_message(r) for r in reversed(rows)]

    response = {
        "type": "HISTORY_RESPONSE",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": session.token,
        "payload": {
            "history_type": "dm",
            "username": target_username,
            "messages": messages,
        }
    }
    await router._write(session.writer, response)


def _row_to_message(row) -> dict:
    return {
        "msg_id": str(row["msg_id"]),
        "from_user": row["from_user"],
        "from_user_id": row["from_user_id"],
        "body": row["body"],
        "ts": int(row["sent_at"].timestamp() * 1000),
    }
