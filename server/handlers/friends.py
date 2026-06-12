import uuid
import time
import asyncpg

from server.session import Session
from server.managers.message_router import MessageRouter
from shared.error_codes import ErrorCode


async def handle_friend_request(
    data: dict,
    session: Session,
    router: MessageRouter,
    db_pool: asyncpg.Pool,
):
    """
    Obsługuje FRIEND_REQUEST — wysyła zaproszenie do znajomych.
    Odbiorca musi być online.
    """
    msg_id = data.get("msg_id")
    payload = data.get("payload") or {}
    target_username = payload.get("username", "").strip()

    if not target_username:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Missing username", msg_id)
        return

    if target_username == session.username:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Cannot add yourself", msg_id)
        return

    # Znajdź użytkownika w bazie
    row = await db_pool.fetchrow(
        "SELECT id, username, status FROM users WHERE username = $1", target_username
    )
    if not row:
        await router.send_error(session.writer, ErrorCode.USER_NOT_FOUND,
                                f"User '{target_username}' not found", msg_id)
        return

    target_id = row["id"]

    # Sprawdź czy już są znajomymi lub zaproszenie istnieje
    existing = await db_pool.fetchrow(
        """
        SELECT status FROM friendships
        WHERE (user_id = $1 AND friend_id = $2)
           OR (user_id = $2 AND friend_id = $1)
        """,
        session.user_id, target_id
    )
    if existing:
        if existing["status"] == "accepted":
            await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                    f"Already friends with '{target_username}'", msg_id)
        else:
            await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                    f"Friend request already pending", msg_id)
        return

    # Zapisz zaproszenie w bazie
    await db_pool.execute(
        """
        INSERT INTO friendships (user_id, friend_id, status)
        VALUES ($1, $2, 'pending')
        ON CONFLICT DO NOTHING
        """,
        session.user_id, target_id
    )

    # Jeśli odbiorca jest online — wyślij od razu
    # Jeśli offline — zaproszenie zostanie dostarczone przy następnym logowaniu
    if router.is_online(target_id):
        invite_frame = {
            "type": "FRIEND_REQUEST",
            "msg_id": str(uuid.uuid4()),
            "ts": int(time.time() * 1000),
            "token": None,
            "payload": {
                "from_user": session.username,
                "from_user_id": session.user_id,
            }
        }
        await router.send_to_user(target_id, invite_frame)
        await _send_ok(session, router, "Friend request sent")
    else:
        await _send_ok(session, router, f"Friend request sent. {target_username} will see it when they log in.")


async def handle_friend_request_accept(
    data: dict,
    session: Session,
    router: MessageRouter,
    db_pool: asyncpg.Pool,
):
    """Akceptuje zaproszenie do znajomych."""
    msg_id = data.get("msg_id")
    payload = data.get("payload") or {}
    from_user_id = payload.get("from_user_id")

    if not from_user_id:
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Missing from_user_id", msg_id)
        return

    # Zaktualizuj status
    result = await db_pool.execute(
        """
        UPDATE friendships SET status = 'accepted', updated_at = NOW()
        WHERE user_id = $1 AND friend_id = $2 AND status = 'pending'
        """,
        from_user_id, session.user_id
    )

    if result == "UPDATE 0":
        await router.send_error(session.writer, ErrorCode.MALFORMED_ENVELOPE,
                                "No pending request found", msg_id)
        return

    # Pobierz dane obu użytkowników
    from_user = await db_pool.fetchrow(
        "SELECT id, username, status FROM users WHERE id = $1", from_user_id
    )

    # Powiadom nadawcę zaproszenia że został zaakceptowany
    if router.is_online(from_user_id):
        accept_frame = {
            "type": "FRIEND_REQUEST_ACCEPT",
            "msg_id": str(uuid.uuid4()),
            "ts": int(time.time() * 1000),
            "token": None,
            "payload": {
                "username": session.username,
                "user_id": session.user_id,
                "status": "online",
            }
        }
        await router.send_to_user(from_user_id, accept_frame)

    # Wyślij zaktualizowaną listę znajomych obu użytkownikom
    await _send_friends_list(session, router, db_pool)
    if router.is_online(from_user_id):
        from_session_data = {
            "user_id": from_user_id,
            "writer": router._writers.get(from_user_id),
            "token": None,
        }
        await _send_friends_list_to_writer(
            router._writers.get(from_user_id),
            from_user_id, router, db_pool
        )


async def handle_friend_request_decline(
    data: dict,
    session: Session,
    router: MessageRouter,
    db_pool: asyncpg.Pool,
):
    """Odrzuca zaproszenie do znajomych - usuwa rekord żeby można było wysłać ponownie."""
    payload = data.get("payload") or {}
    from_user_id = payload.get("from_user_id")

    await db_pool.execute(
        """
        DELETE FROM friendships
        WHERE user_id = $1 AND friend_id = $2 AND status = 'pending'
        """,
        from_user_id, session.user_id
    )


async def send_friends_list_on_login(
    session: Session,
    router: MessageRouter,
    db_pool: asyncpg.Pool,
):
    """Wysyła listę znajomych i dostarcza oczekujące zaproszenia po zalogowaniu."""
    await _send_friends_list(session, router, db_pool)

    # Dostarcz zaproszenia które przyszły gdy byliśmy offline
    pending = await db_pool.fetch(
        """
        SELECT u.id, u.username
        FROM friendships f
        JOIN users u ON u.id = f.user_id
        WHERE f.friend_id = $1 AND f.status = 'pending'
        """,
        session.user_id
    )
    for row in pending:
        invite_frame = {
            "type": "FRIEND_REQUEST",
            "msg_id": str(uuid.uuid4()),
            "ts": int(time.time() * 1000),
            "token": None,
            "payload": {
                "from_user": row["username"],
                "from_user_id": row["id"],
            }
        }
        await router._write(session.writer, invite_frame)


async def notify_friends_status(
    user_id: int,
    username: str,
    status: str,
    router: MessageRouter,
    db_pool: asyncpg.Pool,
):
    """
    Powiadamia znajomych o zmianie statusu użytkownika (online/offline).
    Wywoływane przy logowaniu i rozłączeniu.
    """
    rows = await db_pool.fetch(
        """
        SELECT CASE WHEN user_id = $1 THEN friend_id ELSE user_id END AS friend_id
        FROM friendships
        WHERE (user_id = $1 OR friend_id = $1) AND status = 'accepted'
        """,
        user_id
    )

    frame = {
        "type": "FRIEND_STATUS_UPDATE",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": None,
        "payload": {
            "username": username,
            "user_id": user_id,
            "status": status,
        }
    }

    for row in rows:
        fid = row["friend_id"]
        if router.is_online(fid):
            await router.send_to_user(fid, frame)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

async def _send_friends_list(session: Session, router: MessageRouter, db_pool: asyncpg.Pool):
    await _send_friends_list_to_writer(session.writer, session.user_id, router, db_pool)


async def _send_friends_list_to_writer(writer, user_id: int, router: MessageRouter, db_pool: asyncpg.Pool):
    rows = await db_pool.fetch(
        """
        SELECT
            u.id, u.username, u.status,
            f.status AS friendship_status,
            f.user_id AS requester_id
        FROM friendships f
        JOIN users u ON u.id = CASE WHEN f.user_id = $1 THEN f.friend_id ELSE f.user_id END
        WHERE (f.user_id = $1 OR f.friend_id = $1)
          AND f.status IN ('accepted', 'pending')
        ORDER BY u.username
        """,
        user_id
    )

    friends = []
    for row in rows:
        # Status bazuje wyłącznie na aktywnej sesji w routerze
        # — wartość z bazy może być nieaktualna po restarcie serwera
        is_online = router.is_online(row["id"])
        friends.append({
            "user_id": row["id"],
            "username": row["username"],
            "status": "online" if is_online else "offline",
            "friendship_status": row["friendship_status"],
            "is_incoming": row["requester_id"] != user_id,
        })

    frame = {
        "type": "FRIENDS_LIST",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": None,
        "payload": {"friends": friends}
    }
    await router._write(writer, frame)


async def _send_ok(session: Session, router: MessageRouter, message: str):
    frame = {
        "type": "ROOM_EVENT",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": None,
        "payload": {"event": "info", "message": message}
    }
    await router._write(session.writer, frame)