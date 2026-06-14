import uuid
import time
import asyncpg

from server.session import Session, SessionManager
from server.managers.message_router import MessageRouter
from server.managers.room_manager import RoomManager
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


# ----------------------------------------------------------------------
# Panel administratora — zarządzanie użytkownikami
# ----------------------------------------------------------------------

VALID_ROLES = {"user", "admin"}


async def handle_admin_users_request(
    data: dict,
    session: Session,
    router: MessageRouter,
    db_pool: asyncpg.Pool,
):
    """
    Zwraca listę wszystkich użytkowników (dla panelu administratora).
    Tylko admin.
    """
    msg_id = data.get("msg_id")

    if session.role != "admin":
        await router.send_error(session.writer, ErrorCode.FORBIDDEN,
                                ErrorCode.get_message(ErrorCode.FORBIDDEN), msg_id)
        return

    rows = await db_pool.fetch(
        """
        SELECT id, username, role, status, is_blocked, created_at, last_seen
        FROM users
        ORDER BY username
        """
    )

    users = []
    for row in rows:
        users.append({
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "status": row["status"],
            "is_blocked": row["is_blocked"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "last_seen": row["last_seen"].isoformat() if row["last_seen"] else None,
        })

    response = {
        "type": "ADMIN_USERS_LIST",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": session.token,
        "payload": {
            "ref_msg_id": msg_id,
            "users": users,
        }
    }
    await router._write(session.writer, response)


async def handle_delete_user(
    data: dict,
    session: Session,
    router: MessageRouter,
    db_pool: asyncpg.Pool,
    session_manager: SessionManager,
    room_manager: RoomManager,
):
    """
    Usuwa konto użytkownika. Tylko admin.

    - Admin nie może usunąć samego siebie.
    - Nie można usunąć ostatniego administratora.
    - Jeśli użytkownik jest aktualnie zalogowany, zostaje powiadomiony
      (ACCOUNT_DELETED) i jego sesja jest zamykana.
    - Wszystkie powiązane dane (wiadomości, znajomości, ACL, bany)
      są usuwane wraz z kontem.
    """
    msg_id = data.get("msg_id")
    payload = data.get("payload") or {}
    user_id = payload.get("user_id")

    if session.role != "admin":
        await router.send_error(session.writer, ErrorCode.FORBIDDEN,
                                ErrorCode.get_message(ErrorCode.FORBIDDEN), msg_id)
        return

    if user_id is None:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Missing user_id", msg_id)
        return

    if user_id == session.user_id:
        await router.send_error(session.writer, ErrorCode.SELF_ACTION_FORBIDDEN,
                                ErrorCode.get_message(ErrorCode.SELF_ACTION_FORBIDDEN), msg_id)
        return

    target = await db_pool.fetchrow(
        "SELECT id, username, role FROM users WHERE id = $1", user_id
    )
    if target is None:
        await router.send_error(session.writer, ErrorCode.USER_NOT_FOUND,
                                ErrorCode.get_message(ErrorCode.USER_NOT_FOUND), msg_id)
        return

    # Nie pozwól usunąć ostatniego administratora
    if target["role"] == "admin":
        admin_count = await db_pool.fetchval(
            "SELECT COUNT(*) FROM users WHERE role = 'admin'"
        )
        if admin_count <= 1:
            await router.send_error(session.writer, ErrorCode.LAST_ADMIN,
                                    ErrorCode.get_message(ErrorCode.LAST_ADMIN), msg_id)
            return

    target_username = target["username"]

    # Jeśli użytkownik jest zalogowany — powiadom go i zamknij sesję
    target_session = session_manager.get_by_user_id(user_id)
    if target_session is not None:
        notice = {
            "type": "ACCOUNT_DELETED",
            "msg_id": str(uuid.uuid4()),
            "ts": int(time.time() * 1000),
            "token": None,
            "payload": {
                "message": "Twoje konto zostało usunięte przez administratora.",
                "deleted_by": session.username,
            }
        }
        await router._write(target_session.writer, notice)

        room_manager.leave_all_rooms(user_id)
        router.unregister(user_id)
        session_manager.remove_session(target_session)
        try:
            target_session.writer.close()
        except Exception:
            pass

    # Usuń konto i powiązane dane z bazy
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM messages WHERE sender_id = $1 OR recipient_id = $1",
                user_id
            )
            await conn.execute("DELETE FROM users WHERE id = $1", user_id)

    response = {
        "type": "DELETE_USER_OK",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": session.token,
        "payload": {
            "user_id": user_id,
            "username": target_username,
        }
    }
    await router._write(session.writer, response)


async def handle_set_user_role(
    data: dict,
    session: Session,
    router: MessageRouter,
    db_pool: asyncpg.Pool,
    session_manager: SessionManager,
):
    """
    Zmienia rolę użytkownika (user <-> admin). Tylko admin.

    - Admin nie może zmienić własnej roli (zabezpieczenie przed
      przypadkowym odebraniem sobie uprawnień).
    - Jeśli docelowy użytkownik jest aktualnie zalogowany, jego sesja
      i token są aktualizowane na bieżąco oraz wysyłane jest
      powiadomienie ROLE_CHANGED.
    """
    msg_id = data.get("msg_id")
    payload = data.get("payload") or {}
    user_id = payload.get("user_id")
    new_role = payload.get("role")

    if session.role != "admin":
        await router.send_error(session.writer, ErrorCode.FORBIDDEN,
                                ErrorCode.get_message(ErrorCode.FORBIDDEN), msg_id)
        return

    if user_id is None or new_role is None:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Missing user_id or role", msg_id)
        return

    if new_role not in VALID_ROLES:
        await router.send_error(session.writer, ErrorCode.INVALID_ROLE,
                                ErrorCode.get_message(ErrorCode.INVALID_ROLE), msg_id)
        return

    if user_id == session.user_id:
        await router.send_error(session.writer, ErrorCode.SELF_ACTION_FORBIDDEN,
                                ErrorCode.get_message(ErrorCode.SELF_ACTION_FORBIDDEN), msg_id)
        return

    target = await db_pool.fetchrow(
        "SELECT id, username, role FROM users WHERE id = $1", user_id
    )
    if target is None:
        await router.send_error(session.writer, ErrorCode.USER_NOT_FOUND,
                                ErrorCode.get_message(ErrorCode.USER_NOT_FOUND), msg_id)
        return

    # Nie pozwól odebrać uprawnień ostatniemu administratorowi
    if target["role"] == "admin" and new_role != "admin":
        admin_count = await db_pool.fetchval(
            "SELECT COUNT(*) FROM users WHERE role = 'admin'"
        )
        if admin_count <= 1:
            await router.send_error(session.writer, ErrorCode.LAST_ADMIN,
                                    ErrorCode.get_message(ErrorCode.LAST_ADMIN), msg_id)
            return

    await db_pool.execute(
        "UPDATE users SET role = $1 WHERE id = $2", new_role, user_id
    )

    # Jeśli użytkownik jest zalogowany — zaktualizuj sesję i powiadom go
    target_session = session_manager.get_by_user_id(user_id)
    if target_session is not None:
        target_session.role = new_role
        notice = {
            "type": "ROLE_CHANGED",
            "msg_id": str(uuid.uuid4()),
            "ts": int(time.time() * 1000),
            "token": target_session.token,
            "payload": {
                "role": new_role,
                "changed_by": session.username,
            }
        }
        await router._write(target_session.writer, notice)

    response = {
        "type": "SET_USER_ROLE_OK",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": session.token,
        "payload": {
            "user_id": user_id,
            "username": target["username"],
            "role": new_role,
        }
    }
    await router._write(session.writer, response)
