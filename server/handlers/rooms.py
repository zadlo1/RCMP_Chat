import uuid
import time

from server.session import Session, SessionManager
from server.managers.message_router import MessageRouter
from server.managers.room_manager import RoomManager
from shared.error_codes import ErrorCode


async def handle_join_room(
    data: dict,
    session: Session,
    router: MessageRouter,
    room_manager: RoomManager,
):
    """
    Obsługuje JOIN_ROOM od klienta.
    1. Sprawdza czy pokój istnieje.
    2. Sprawdza uprawnienia (ACL).
    3. Dodaje użytkownika do pokoju.
    4. Rozsyła ROOM_EVENT do pozostałych członków.
    """
    msg_id = data.get("msg_id")
    payload = data.get("payload") or {}
    room_id = payload.get("room_id")

    if room_id is None:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Missing room_id", msg_id)
        return

    # Sprawdź czy pokój istnieje
    room = await room_manager.get_room(room_id)
    if room is None:
        await router.send_error(session.writer, ErrorCode.ROOM_NOT_FOUND,
                                ErrorCode.get_message(ErrorCode.ROOM_NOT_FOUND), msg_id)
        return

    # Sprawdź czy użytkownik nie jest zbanowany w tym pokoju
    if await room_manager.is_banned(room_id, session.user_id):
        await router.send_error(session.writer, ErrorCode.ROOM_BANNED,
                                ErrorCode.get_message(ErrorCode.ROOM_BANNED), msg_id)
        return

    # Sprawdź uprawnienia i dołącz
    success = await room_manager.join_room(room_id, session.user_id)
    if not success:
        await router.send_error(session.writer, ErrorCode.FORBIDDEN,
                                ErrorCode.get_message(ErrorCode.FORBIDDEN), msg_id)
        return

    session.touch()

    # Powiadomienie pozostałych członków pokoju
    members = room_manager.get_room_members(room_id)
    room_event = {
        "type": "ROOM_EVENT",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": None,
        "payload": {
            "event": "joined",
            "room_id": room_id,
            "room_name": room["name"],
            "username": session.username,
            "user_id": session.user_id,
        }
    }
    await router.send_to_room(members, room_event, exclude_user_id=None)


async def handle_leave_room(
    data: dict,
    session: Session,
    router: MessageRouter,
    room_manager: RoomManager,
):
    """
    Obsługuje LEAVE_ROOM od klienta.
    1. Usuwa użytkownika z pokoju.
    2. Rozsyła ROOM_EVENT do pozostałych członków.
    """
    msg_id = data.get("msg_id")
    payload = data.get("payload") or {}
    room_id = payload.get("room_id")

    if room_id is None:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Missing room_id", msg_id)
        return

    # Pobierz członków przed opuszczeniem (żeby wysłać event)
    members = room_manager.get_room_members(room_id)

    room_manager.leave_room(room_id, session.user_id)
    session.touch()

    # Powiadomienie pozostałych
    room = await room_manager.get_room(room_id)
    room_name = room["name"] if room else str(room_id)

    room_event = {
        "type": "ROOM_EVENT",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": None,
        "payload": {
            "event": "left",
            "room_id": room_id,
            "room_name": room_name,
            "username": session.username,
            "user_id": session.user_id,
        }
    }
    await router.send_to_room(members, room_event, exclude_user_id=session.user_id)


# ----------------------------------------------------------------------
# Lista uczestników pokoju
# ----------------------------------------------------------------------

async def handle_room_members(
    data: dict,
    session: Session,
    router: MessageRouter,
    room_manager: RoomManager,
    session_manager: SessionManager,
    db_pool=None,
):
    """
    Obsługuje ROOM_MEMBERS_REQUEST.
    Zwraca liste wszystkich uzytkownikow, ktorzy moga wejsc do pokoju
    (zaproszeni lub z dostepem), wraz z ich aktualnym statusem online/offline.
    Dostepne dla kazdego czlonka pokoju.
    Admin dodatkowo otrzymuje liste zbanowanych uzytkownikow.
    """
    msg_id = data.get("msg_id")
    payload = data.get("payload") or {}
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

    if not room_manager.is_member(room_id, session.user_id):
        await router.send_error(session.writer, ErrorCode.FORBIDDEN,
                                "Not a member of this room", msg_id)
        return

    members = []
    if db_pool is not None:
        if room["is_private"]:
            # Pokój prywatny — zaproszeni (room_acl) + adminowie, bez zbanowanych, bez siebie
            rows = await db_pool.fetch(
                """
                SELECT DISTINCT u.id AS user_id, u.username, u.role
                FROM users u
                LEFT JOIN room_acl a ON a.room_id = $1 AND a.user_id = u.id
                LEFT JOIN room_bans b ON b.room_id = $1 AND b.user_id = u.id
                WHERE u.id != $2
                  AND b.user_id IS NULL
                  AND (a.user_id IS NOT NULL OR u.role = 'admin')
                ORDER BY u.username
                """,
                room_id, session.user_id
            )
        else:
            # Pokój publiczny — wszyscy użytkownicy poza zbanowanymi i bieżącym
            rows = await db_pool.fetch(
                """
                SELECT u.id AS user_id, u.username, u.role
                FROM users u
                LEFT JOIN room_bans b ON b.room_id = $1 AND b.user_id = u.id
                WHERE u.id != $2
                  AND b.user_id IS NULL
                ORDER BY u.username
                """,
                room_id, session.user_id
            )

        online_ids = room_manager.get_room_members(room_id)
        for row in rows:
            uid = row["user_id"]
            members.append({
                "user_id": uid,
                "username": row["username"],
                "role": row["role"],
                "status": "online" if uid in online_ids else "offline",
            })

        # Dodaj samego siebie do listy (zawsze online, bo wysyła zapytanie)
        members.append({
            "user_id": session.user_id,
            "username": session.username,
            "role": session.role,
            "status": "online",
        })
        members.sort(key=lambda m: m["username"].lower())
    else:
        # Fallback: tylko aktywni online (stare zachowanie bez db_pool)
        online_ids = room_manager.get_room_members(room_id)
        for user_id in online_ids:
            member_session = session_manager.get_by_user_id(user_id)
            if member_session is None:
                continue
            members.append({
                "user_id": user_id,
                "username": member_session.username,
                "role": member_session.role,
                "status": "online",
            })
        members.sort(key=lambda m: m["username"].lower())

    response_payload = {
        "room_id": room_id,
        "room_name": room["name"],
        "is_private": room["is_private"],
        "members": members,
    }

    # Admin dodatkowo widzi listę zbanowanych — przydatne do odbanowania
    if session.role == "admin":
        response_payload["banned"] = await room_manager.list_banned(room_id)

    response = {
        "type": "ROOM_MEMBERS_LIST",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": session.token,
        "payload": response_payload,
    }
    await router._write(session.writer, response)


# ----------------------------------------------------------------------
# Moderacja pokoju — kick i ban (tylko admin)
# ----------------------------------------------------------------------

async def _moderation_precheck(
    data: dict,
    session: Session,
    router: MessageRouter,
    room_manager: RoomManager,
    session_manager: SessionManager,
):
    """
    Wspólna walidacja dla ROOM_KICK / ROOM_BAN / ROOM_UNBAN.
    Zwraca (room, target_session_or_None, target_user_id, room_id, msg_id)
    lub None jeśli wysłano już błąd.
    """
    msg_id = data.get("msg_id")
    payload = data.get("payload") or {}
    room_id = payload.get("room_id")
    target_user_id = payload.get("user_id")

    if session.role != "admin":
        await router.send_error(session.writer, ErrorCode.FORBIDDEN,
                                "Only admin can manage room members", msg_id)
        return None

    if room_id is None or target_user_id is None:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Missing room_id or user_id", msg_id)
        return None

    room = await room_manager.get_room(room_id)
    if room is None:
        await router.send_error(session.writer, ErrorCode.ROOM_NOT_FOUND,
                                ErrorCode.get_message(ErrorCode.ROOM_NOT_FOUND), msg_id)
        return None

    if target_user_id == session.user_id:
        await router.send_error(session.writer, ErrorCode.FORBIDDEN,
                                "Cannot perform this action on yourself", msg_id)
        return None

    target_session = session_manager.get_by_user_id(target_user_id)
    return room, target_session, target_user_id, room_id, msg_id


async def handle_room_kick(
    data: dict,
    session: Session,
    router: MessageRouter,
    room_manager: RoomManager,
    session_manager: SessionManager,
):
    """
    Wyrzuca użytkownika z pokoju (bez bana — może wrócić, jeśli ma dostęp).
    Tylko admin.
    """
    result = await _moderation_precheck(data, session, router, room_manager, session_manager)
    if result is None:
        return
    room, target_session, target_user_id, room_id, msg_id = result

    if not room_manager.is_member(room_id, target_user_id):
        await router.send_error(session.writer, ErrorCode.USER_NOT_FOUND,
                                "User is not in this room", msg_id)
        return

    target_username = target_session.username if target_session else str(target_user_id)

    # Usuń ze stanu in-memory pokoju
    room_manager.leave_room(room_id, target_user_id)

    # Powiadom resztę pokoju
    members = room_manager.get_room_members(room_id)
    notify_event = {
        "type": "ROOM_EVENT",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": None,
        "payload": {
            "event": "kicked",
            "room_id": room_id,
            "room_name": room["name"],
            "username": target_username,
            "user_id": target_user_id,
            "by": session.username,
        }
    }
    await router.send_to_room(members, notify_event, exclude_user_id=None)

    # Powiadom wyrzuconego (mógł nie być w `members` jeśli już offline)
    if target_session:
        await router.send_to_user(target_user_id, notify_event)

    # Potwierdzenie dla admina
    response = {
        "type": "ROOM_KICK_OK",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": session.token,
        "payload": {
            "room_id": room_id,
            "room_name": room["name"],
            "user_id": target_user_id,
            "username": target_username,
        }
    }
    await router._write(session.writer, response)


async def handle_room_ban(
    data: dict,
    session: Session,
    router: MessageRouter,
    room_manager: RoomManager,
    session_manager: SessionManager,
):
    """
    Banuje użytkownika w pokoju — wyrzuca go i blokuje ponowne dołączenie.
    Tylko admin.
    """
    result = await _moderation_precheck(data, session, router, room_manager, session_manager)
    if result is None:
        return
    room, target_session, target_user_id, room_id, msg_id = result

    target_username = target_session.username if target_session else str(target_user_id)

    was_member = room_manager.is_member(room_id, target_user_id)

    # Zapisz bana, usuń z ACL i z aktualnych członków pokoju
    await room_manager.ban_user(room_id, target_user_id, session.user_id)

    if was_member:
        members = room_manager.get_room_members(room_id)
        notify_event = {
            "type": "ROOM_EVENT",
            "msg_id": str(uuid.uuid4()),
            "ts": int(time.time() * 1000),
            "token": None,
            "payload": {
                "event": "banned",
                "room_id": room_id,
                "room_name": room["name"],
                "username": target_username,
                "user_id": target_user_id,
                "by": session.username,
            }
        }
        await router.send_to_room(members, notify_event, exclude_user_id=None)
        if target_session:
            await router.send_to_user(target_user_id, notify_event)
    elif target_session:
        # Niezalogowany w pokoju, ale online — poinformuj że został zbanowany
        notify_event = {
            "type": "ROOM_EVENT",
            "msg_id": str(uuid.uuid4()),
            "ts": int(time.time() * 1000),
            "token": None,
            "payload": {
                "event": "banned",
                "room_id": room_id,
                "room_name": room["name"],
                "username": target_username,
                "user_id": target_user_id,
                "by": session.username,
            }
        }
        await router.send_to_user(target_user_id, notify_event)

    response = {
        "type": "ROOM_BAN_OK",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": session.token,
        "payload": {
            "room_id": room_id,
            "room_name": room["name"],
            "user_id": target_user_id,
            "username": target_username,
        }
    }
    await router._write(session.writer, response)


async def handle_room_unban(
    data: dict,
    session: Session,
    router: MessageRouter,
    room_manager: RoomManager,
    session_manager: SessionManager,
):
    """Usuwa bana użytkownika w pokoju. Tylko admin."""
    result = await _moderation_precheck(data, session, router, room_manager, session_manager)
    if result is None:
        return
    room, target_session, target_user_id, room_id, msg_id = result

    target_username = target_session.username if target_session else str(target_user_id)

    await room_manager.unban_user(room_id, target_user_id)

    response = {
        "type": "ROOM_UNBAN_OK",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": session.token,
        "payload": {
            "room_id": room_id,
            "room_name": room["name"],
            "user_id": target_user_id,
            "username": target_username,
        }
    }
    await router._write(session.writer, response)