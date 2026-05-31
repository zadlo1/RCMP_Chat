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