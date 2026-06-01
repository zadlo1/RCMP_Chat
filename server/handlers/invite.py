import uuid
import time

import asyncpg

from server.session import Session
from server.managers.message_router import MessageRouter
from shared.error_codes import ErrorCode


async def handle_room_invite_accept(
    data: dict,
    session: Session,
    router: MessageRouter,
    db_pool: asyncpg.Pool,
):
    """
    Obsługuje ROOM_INVITE_ACCEPT od klienta.
    Dodaje użytkownika do ACL pokoju i potwierdza.
    """
    msg_id = data.get("msg_id")
    payload = data.get("payload") or {}
    room_id = payload.get("room_id")

    if room_id is None:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Missing room_id", msg_id)
        return

    # Sprawdź czy pokój istnieje
    room = await db_pool.fetchrow(
        "SELECT id, name FROM rooms WHERE id = $1 AND is_private = TRUE", room_id
    )
    if not room:
        await router.send_error(session.writer, ErrorCode.ROOM_NOT_FOUND,
                                ErrorCode.get_message(ErrorCode.ROOM_NOT_FOUND), msg_id)
        return

    # Dodaj do ACL
    await db_pool.execute(
        """
        INSERT INTO room_acl (room_id, user_id)
        VALUES ($1, $2)
        ON CONFLICT DO NOTHING
        """,
        room_id, session.user_id
    )

    # Potwierdź akceptację — ROOM_EVENT joined
    response = {
        "type": "ROOM_EVENT",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": None,
        "payload": {
            "event": "invite_accepted",
            "room_id": room_id,
            "room_name": room["name"],
            "username": session.username,
        }
    }
    await router._write(session.writer, response)


async def handle_room_invite_decline(
    data: dict,
    session: Session,
    router: MessageRouter,
):
    """Obsługuje ROOM_INVITE_DECLINE — nic nie robi po stronie serwera."""
    session.touch()


async def send_invite(
    router: MessageRouter,
    target_user_id: int,
    room_id: int,
    room_name: str,
    invited_by: str,
):
    """
    Wysyła ROOM_INVITE do konkretnego użytkownika.
    Wywoływane przez admina z handlera SEND_MESSAGE z flagą invite.
    """
    frame = {
        "type": "ROOM_INVITE",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": None,
        "payload": {
            "room_id": room_id,
            "room_name": room_name,
            "invited_by": invited_by,
        }
    }
    await router.send_to_user(target_user_id, frame)