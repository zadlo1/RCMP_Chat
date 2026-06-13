import uuid
import time
import asyncpg

from server.session import Session
from server.managers.message_router import MessageRouter
from shared.error_codes import ErrorCode


async def handle_create_room(
    data: dict,
    session: Session,
    router: MessageRouter,
    db_pool: asyncpg.Pool,
):
    """
    Tworzy nowy pokój. Tylko admin.
    """
    msg_id = data.get("msg_id")
    payload = data.get("payload") or {}
    room_name = payload.get("name", "").strip()
    is_private = payload.get("is_private", False)

    if session.role != "admin":
        await router.send_error(session.writer, ErrorCode.FORBIDDEN,
                                "Only admin can create rooms", msg_id)
        return

    if not room_name:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Missing room name", msg_id)
        return

    from server.config import Config
    if len(room_name) > Config.MAX_NAME_LENGTH:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                f"Room name too long (max {Config.MAX_NAME_LENGTH})", msg_id)
        return

    # Sprawdź czy pokój już istnieje
    existing = await db_pool.fetchrow(
        "SELECT id FROM rooms WHERE name = $1", room_name
    )
    if existing:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                f"Room '{room_name}' already exists", msg_id)
        return

    # Utwórz pokój
    row = await db_pool.fetchrow(
        "INSERT INTO rooms (name, is_private) VALUES ($1, $2) RETURNING id",
        room_name, is_private
    )
    room_id = row["id"]

    # Jeśli prywatny — dodaj admina do ACL
    if is_private:
        await db_pool.execute(
            "INSERT INTO room_acl (room_id, user_id) VALUES ($1, $2)",
            room_id, session.user_id
        )

    # Odpowiedź CREATE_ROOM_OK
    response = {
        "type": "CREATE_ROOM_OK",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": session.token,
        "payload": {
            "id": room_id,
            "name": room_name,
            "is_private": is_private,
        }
    }
    await router._write(session.writer, response)