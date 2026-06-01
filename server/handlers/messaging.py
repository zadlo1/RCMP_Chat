import uuid
import time

from server.session import Session
from server.managers.message_router import MessageRouter
from server.managers.room_manager import RoomManager
from server.managers.rate_limiter import RateLimiter
from shared.crypto import verify_hmac
from shared.error_codes import ErrorCode
import asyncpg


async def handle_send_message(
    data: dict,
    session: Session,
    router: MessageRouter,
    room_manager: RoomManager,
    rate_limiter: RateLimiter,
    db_pool: asyncpg.Pool,
):
    """
    Obsługuje SEND_MESSAGE od klienta.
    1. Rate limiting.
    2. Weryfikacja HMAC.
    3. Wykrywanie duplikatów.
    4. Routing do pokoju lub użytkownika.
    5. Zapis do bazy.
    6. MESSAGE_ACK do nadawcy.
    """
    msg_id = data.get("msg_id")

    # Rate limit
    if not rate_limiter.check_message_rate(session.user_id):
        await router.send_error(session.writer, ErrorCode.SEND_RATE_LIMIT,
                                ErrorCode.get_message(ErrorCode.SEND_RATE_LIMIT), msg_id)
        return

    payload = data.get("payload") or {}
    target_type = payload.get("target_type")  # "room" lub "user"
    target_id = payload.get("target_id")
    seq_id = payload.get("seq_id", 0)
    body = payload.get("body", "")
    hmac_received = payload.get("hmac", "")

    # Walidacja pól
    if not target_type or target_id is None or not body:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Missing target_type, target_id or body", msg_id)
        return

    # Weryfikacja HMAC
    if not verify_hmac(session.hmac_secret, msg_id, data["ts"], seq_id, body, hmac_received):
        await router.send_error(session.writer, ErrorCode.INVALID_HMAC,
                                ErrorCode.get_message(ErrorCode.INVALID_HMAC), msg_id)
        return

    # Wykrywanie duplikatów
    if session.is_duplicate_msg(msg_id):
        await _send_ack(session, router, msg_id)
        return
    session.store_msg_id(msg_id)
    session.touch()

    # Routing
    if target_type == "room":
        await _route_to_room(
            data, session, router, room_manager, db_pool,
            target_id, seq_id, body, msg_id
        )
    elif target_type == "user":
        await _route_to_user(
            data, session, router, db_pool,
            target_id, seq_id, body, msg_id
        )
    elif target_type == "invite":
        await _route_invite(data, session, router, db_pool, payload)
    else:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Invalid target_type", msg_id)
        return

    # MESSAGE_ACK do nadawcy
    await _send_ack(session, router, msg_id)


async def handle_message_ack(
    data: dict,
    session: Session,
):
    """Odbiera MESSAGE_ACK od klienta — aktualizuje aktywność sesji."""
    session.touch()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

async def _route_to_room(data, session, router, room_manager, db_pool,
                          room_id, seq_id, body, msg_id):
    # Sprawdź czy użytkownik jest w pokoju
    if not room_manager.is_member(room_id, session.user_id):
        await router.send_error(session.writer, ErrorCode.FORBIDDEN,
                                "Not a member of this room", msg_id)
        return

    # Ramka DELIVER_MESSAGE
    deliver = {
        "type": "DELIVER_MESSAGE",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": None,
        "payload": {
            "from_user": session.username,
            "from_user_id": session.user_id,
            "target_type": "room",
            "target_id": room_id,
            "seq_id": seq_id,
            "body": body,
            "original_msg_id": msg_id,
        }
    }

    members = room_manager.get_room_members(room_id)
    await router.send_to_room(members, deliver, exclude_user_id=session.user_id)

    # Zapis do bazy
    await db_pool.execute(
        """
        INSERT INTO messages (msg_id, seq_id, sender_id, room_id, body, hmac)
        VALUES ($1::uuid, $2, $3, $4, $5, $6)
        ON CONFLICT (msg_id) DO NOTHING
        """,
        msg_id, seq_id, session.user_id, room_id, body, ""
    )


async def _route_to_user(data, session, router, db_pool,
                          target_user_id, seq_id, body, msg_id):
    # Sprawdź czy odbiorca istnieje i jest online
    if not router.is_online(target_user_id):
        await router.send_error(session.writer, ErrorCode.USER_NOT_FOUND,
                                ErrorCode.get_message(ErrorCode.USER_NOT_FOUND), msg_id)
        return

    deliver = {
        "type": "DELIVER_MESSAGE",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": None,
        "payload": {
            "from_user": session.username,
            "from_user_id": session.user_id,
            "target_type": "user",
            "target_id": target_user_id,
            "seq_id": seq_id,
            "body": body,
            "original_msg_id": msg_id,
        }
    }
    await router.send_to_user(target_user_id, deliver)

    # Zapis do bazy
    await db_pool.execute(
        """
        INSERT INTO messages (msg_id, seq_id, sender_id, recipient_id, body, hmac)
        VALUES ($1::uuid, $2, $3, $4, $5, $6)
        ON CONFLICT (msg_id) DO NOTHING
        """,
        msg_id, seq_id, session.user_id, target_user_id, body, ""
    )


async def _send_ack(session: Session, router: MessageRouter, ref_msg_id: str):
    ack = {
        "type": "MESSAGE_ACK",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": session.token,
        "payload": {
            "ack_msg_id": ref_msg_id,
        }
    }
    await router._write(session.writer, ack)


async def _route_invite(data, session, router, db_pool, payload):
    """Wysyła ROOM_INVITE do użytkownika (tylko admin)."""
    if session.role != "admin":
        await router.send_error(session.writer, ErrorCode.FORBIDDEN,
                                "Only admin can send invites", data.get("msg_id"))
        return

    invite_to = payload.get("invite_to", "")
    room_id = payload.get("target_id")
    room_name = payload.get("room_name", "")

    # Znajdź user_id odbiorcy
    row = await db_pool.fetchrow(
        "SELECT id FROM users WHERE username = $1", invite_to
    )
    if not row:
        await router.send_error(session.writer, ErrorCode.USER_NOT_FOUND,
                                f"User '{invite_to}' not found", data.get("msg_id"))
        return

    target_user_id = row["id"]

    invite_frame = {
        "type": "ROOM_INVITE",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": None,
        "payload": {
            "room_id": room_id,
            "room_name": room_name,
            "invited_by": session.username,
        }
    }
    sent = await router.send_to_user(target_user_id, invite_frame)
    if not sent:
        await router.send_error(session.writer, ErrorCode.USER_NOT_FOUND,
                                f"User '{invite_to}' is offline", data.get("msg_id"))