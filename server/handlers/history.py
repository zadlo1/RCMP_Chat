import uuid
import time
from datetime import datetime, timezone

from server.config import Config
from server.session import Session
from server.managers.message_router import MessageRouter
from server.managers.room_manager import RoomManager
from shared.error_codes import ErrorCode
import asyncpg


def _parse_before_ts(payload: dict) -> datetime | None:
    """Konwertuje opcjonalny kursor paginacji `before_ts` (ms epoch, tak jak `ts`
    w kopercie protokołu) na obiekt datetime używany w zapytaniu SQL.
    Niepoprawna lub brakująca wartość oznacza brak filtrowania (pierwsza strona)."""
    before_ts = payload.get("before_ts")
    if before_ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(before_ts) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


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

    Obsługuje paginację historii ("załaduj starsze wiadomości") przez kursor
    `before_ts`: klient przesyła znacznik czasu najstarszej aktualnie posiadanej
    wiadomości, a serwer zwraca kolejną porcję starszych wiadomości sprzed tego
    momentu wraz z flagą `has_more` informującą, czy istnieją jeszcze starsze.

    payload:
      - history_type: "room" | "dm"
      - room_id:   wymagane dla "room"
      - username:  wymagane dla "dm" (nazwa drugiej strony konwersacji)
      - limit:     opcjonalne, domyślnie HISTORY_DEFAULT_LIMIT (maks. HISTORY_MAX_LIMIT)
      - before_ts: opcjonalne, unix timestamp w ms — zwraca wiadomości starsze
                   niż podany moment (paginacja "załaduj starsze wiadomości")
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

    before_dt = _parse_before_ts(payload)
    before_ts_raw = payload.get("before_ts") if before_dt is not None else None

    if history_type == "room":
        await _handle_room_history(data, session, router, room_manager, db_pool, payload,
                                    limit, msg_id, before_dt, before_ts_raw)
    elif history_type == "dm":
        await _handle_dm_history(data, session, router, db_pool, payload,
                                  limit, msg_id, before_dt, before_ts_raw)
    else:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Invalid or missing history_type", msg_id)


async def _handle_room_history(data, session, router, room_manager, db_pool,
                                payload, limit, msg_id, before_dt, before_ts_raw):
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
          AND ($2::timestamptz IS NULL OR m.sent_at < $2)
        ORDER BY m.sent_at DESC
        LIMIT $3
        """,
        room_id, before_dt, limit
    )

    messages = [_row_to_message(r) for r in reversed(rows)]
    has_more = False
    if rows:
        oldest_sent_at = rows[-1]["sent_at"]
        has_more = bool(await db_pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM messages WHERE room_id = $1 AND sent_at < $2)",
            room_id, oldest_sent_at
        ))

    import logging
    logger = logging.getLogger("rcmp.server")
    logger.info("HISTORY_REQUEST room=%s: znaleziono %d wiadomości dla użytkownika %s (before_ts=%s, has_more=%s)",
                room_id, len(messages), session.username, before_ts_raw, has_more)

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
            "has_more": has_more,
            "before_ts": before_ts_raw,
        }
    }
    await router._write(session.writer, response)


async def _handle_dm_history(data, session, router, db_pool, payload, limit, msg_id,
                              before_dt, before_ts_raw):
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
        WHERE ((m.sender_id = $1 AND m.recipient_id = $2)
           OR (m.sender_id = $2 AND m.recipient_id = $1))
          AND ($3::timestamptz IS NULL OR m.sent_at < $3)
        ORDER BY m.sent_at DESC
        LIMIT $4
        """,
        session.user_id, target_id, before_dt, limit
    )

    messages = [_row_to_message(r) for r in reversed(rows)]
    has_more = False
    if rows:
        oldest_sent_at = rows[-1]["sent_at"]
        has_more = bool(await db_pool.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM messages
                WHERE ((sender_id = $1 AND recipient_id = $2)
                   OR (sender_id = $2 AND recipient_id = $1))
                  AND sent_at < $3
            )
            """,
            session.user_id, target_id, oldest_sent_at
        ))

    response = {
        "type": "HISTORY_RESPONSE",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": session.token,
        "payload": {
            "history_type": "dm",
            "username": target_username,
            "messages": messages,
            "has_more": has_more,
            "before_ts": before_ts_raw,
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
