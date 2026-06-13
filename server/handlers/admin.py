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

async def handle_delete_room(
    data: dict,
    session: Session,
    router: MessageRouter,
    db_pool: asyncpg.Pool,
    room_manager,
):
    """
    Usuwa pokój. Tylko admin.
    Wyrzuca wszystkich obecnych członków pokoju (ROOM_EVENT: deleted),
    usuwa rekordy z bazy (rooms, room_acl, messages dla pokoju).
    """
    msg_id = data.get("msg_id")
    payload = data.get("payload") or {}
    room_id = payload.get("room_id")

    if session.role != "admin":
        await router.send_error(session.writer, ErrorCode.FORBIDDEN,
                                "Only admin can delete rooms", msg_id)
        return

    if room_id is None:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Missing room_id", msg_id)
        return

    # Sprawdź czy pokój istnieje
    existing = await db_pool.fetchrow(
        "SELECT id, name FROM rooms WHERE id = $1", room_id
    )
    if not existing:
        await router.send_error(session.writer, ErrorCode.ROOM_NOT_FOUND,
                                ErrorCode.get_message(ErrorCode.ROOM_NOT_FOUND), msg_id)
        return

    room_name = existing["name"]

    # Powiadom wszystkich aktualnie w pokoju
    members = room_manager.get_room_members(room_id)
    delete_event = {
        "type": "ROOM_EVENT",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": None,
        "payload": {
            "event": "deleted",
            "room_id": room_id,
            "room_name": room_name,
            "deleted_by": session.username,
        }
    }
    await router.send_to_room(members, delete_event, exclude_user_id=None)

    # Wyczyść stan in-memory pokoju
    room_manager.remove_room(room_id)

    # Usuń z bazy (kaskadowo: room_acl, messages jeśli ON DELETE CASCADE,
    # albo ręcznie jeśli brak kaskady)
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM room_acl WHERE room_id = $1", room_id)
            await conn.execute("DELETE FROM messages WHERE room_id = $1", room_id)
            await conn.execute("DELETE FROM rooms WHERE id = $1", room_id)

    # Odpowiedź do admina
    response = {
        "type": "DELETE_ROOM_OK",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": session.token,
        "payload": {
            "room_id": room_id,
            "room_name": room_name,
        }
    }
    await router._write(session.writer, response)
