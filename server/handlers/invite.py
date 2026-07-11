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
    room_manager=None,
    push_rooms_list=None,
):
    """
    Obsługuje ROOM_INVITE_ACCEPT od klienta.
    - Dla pokoju prywatnego: dodaje użytkownika do ACL pokoju.
    - W obu przypadkach: usuwa ewentualny wpis o wyrzuceniu z pokoju
      (room_kicks), przywracając dostęp do pokoju publicznego.
    Potwierdza akceptację i odświeża listę pokojów (kanał wraca na pasek).
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
        "SELECT id, name, is_private FROM rooms WHERE id = $1", room_id
    )
    if not room:
        await router.send_error(session.writer, ErrorCode.ROOM_NOT_FOUND,
                                ErrorCode.get_message(ErrorCode.ROOM_NOT_FOUND), msg_id)
        return

    if room["is_private"]:
        # Dodaj do ACL
        await db_pool.execute(
            """
            INSERT INTO room_acl (room_id, user_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            room_id, session.user_id
        )

    # Usuń wpis o wyrzuceniu (jeśli istniał) — zaproszenie przywraca dostęp
    if room_manager is not None:
        await room_manager.clear_kick(room_id, session.user_id)

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

    # Odśwież listę pokojów — kanał wraca na pasek po lewej
    if push_rooms_list is not None:
        await push_rooms_list(session.user_id)


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
    db_pool: asyncpg.Pool = None,
):
    """
    Wysyła ROOM_INVITE do konkretnego użytkownika.
    Wywoływane przez admina z handlera SEND_MESSAGE z flagą invite.
    Jeśli użytkownik jest offline i podano db_pool, zaproszenie zostaje
    zakolejkowane w room_invites i dostarczone przy jego najbliższym LOGIN_OK.
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
    delivered = await router.send_to_user(target_user_id, frame)

    if not delivered and db_pool is not None:
        await db_pool.execute(
            """
            INSERT INTO room_invites (room_id, invited_user_id, invited_by)
            VALUES ($1, $2, $3)
            """,
            room_id, target_user_id, invited_by
        )


async def deliver_pending_room_invites(
    session: Session,
    router: MessageRouter,
    db_pool: asyncpg.Pool,
):
    """
    Wysyła zaproszenia do pokojów które przyszły podczas gdy użytkownik
    był offline. Wywoływane po LOGIN_OK. Wiersze usuwane po dostarczeniu —
    zaproszenie ma sens tylko raz, klient odpowiada ROOM_INVITE_ACCEPT/DECLINE.
    """
    rows = await db_pool.fetch(
        """
        SELECT ri.id, ri.room_id, ri.invited_by, r.name AS room_name, r.is_private
        FROM room_invites ri
        JOIN rooms r ON r.id = ri.room_id
        WHERE ri.invited_user_id = $1
        ORDER BY ri.created_at ASC
        """,
        session.user_id
    )
    if not rows:
        return

    for row in rows:
        frame = {
            "type": "ROOM_INVITE",
            "msg_id": str(uuid.uuid4()),
            "ts": int(time.time() * 1000),
            "token": None,
            "payload": {
                "room_id": row["room_id"],
                "room_name": row["room_name"],
                "is_private": row["is_private"],
                "invited_by": row["invited_by"],
                "queued": True,
            }
        }
        await router._write(session.writer, frame)

    await db_pool.execute(
        "DELETE FROM room_invites WHERE invited_user_id = $1",
        session.user_id
    )