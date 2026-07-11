"""
Testy jednostkowe dla trwałej historii wiadomości (HISTORY_REQUEST / HISTORY_RESPONSE).
"""
import asyncio
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.session import Session
from server.managers.message_router import MessageRouter
from server.handlers.history import handle_history_request
from shared.error_codes import ErrorCode


def make_writer():
    w = MagicMock(spec=asyncio.StreamWriter)
    w.write = MagicMock()
    w.drain = AsyncMock()
    return w


def make_session(user_id=1, username="alice", role="user"):
    s = Session(writer=make_writer(), ip="127.0.0.1")
    s.user_id = user_id
    s.username = username
    s.role = role
    s.token = "tok"
    s.state = "ACTIVE"
    return s


def make_router():
    router = MagicMock(spec=MessageRouter)
    router.send_error = AsyncMock()
    router._write = AsyncMock()
    return router


def make_room_manager(room=None, is_member=False, has_access=True):
    rm = MagicMock()
    rm.get_room = AsyncMock(return_value=room)
    rm.is_member = MagicMock(return_value=is_member)
    rm.check_access = AsyncMock(return_value=has_access)
    return rm


def make_db(fetch_result=None, fetchrow_result=None):
    db = AsyncMock()
    db.fetch = AsyncMock(return_value=fetch_result or [])
    db.fetchrow = AsyncMock(return_value=fetchrow_result)
    return db


def make_row(msg_id, from_user_id, from_user, body, sent_at=None):
    return {
        "msg_id": msg_id,
        "from_user_id": from_user_id,
        "from_user": from_user,
        "body": body,
        "sent_at": sent_at or datetime.now(timezone.utc),
    }


def envelope(history_type, **payload):
    return {
        "type": "HISTORY_REQUEST",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": "tok",
        "payload": {"history_type": history_type, **payload},
    }


# ---------------------------------------------------------------------------
# Walidacja
# ---------------------------------------------------------------------------

class TestValidation:
    @pytest.mark.asyncio
    async def test_missing_history_type_gets_error(self):
        session = make_session()
        router = make_router()
        room_manager = make_room_manager()
        db = make_db()

        await handle_history_request({"msg_id": "x", "payload": {}}, session, router, room_manager, db)

        router.send_error.assert_awaited_once()
        args = router.send_error.call_args[0]
        assert args[1] == ErrorCode.MALFORMED_ENVELOPE

    @pytest.mark.asyncio
    async def test_room_missing_room_id_gets_error(self):
        session = make_session()
        router = make_router()
        room_manager = make_room_manager()
        db = make_db()

        await handle_history_request(envelope("room"), session, router, room_manager, db)

        router.send_error.assert_awaited_once()
        assert router.send_error.call_args[0][1] == ErrorCode.MALFORMED_ENVELOPE

    @pytest.mark.asyncio
    async def test_dm_missing_username_gets_error(self):
        session = make_session()
        router = make_router()
        room_manager = make_room_manager()
        db = make_db()

        await handle_history_request(envelope("dm"), session, router, room_manager, db)

        router.send_error.assert_awaited_once()
        assert router.send_error.call_args[0][1] == ErrorCode.MALFORMED_ENVELOPE


# ---------------------------------------------------------------------------
# Historia pokoju
# ---------------------------------------------------------------------------

class TestRoomHistory:
    @pytest.mark.asyncio
    async def test_room_not_found_gets_error(self):
        session = make_session()
        router = make_router()
        room_manager = make_room_manager(room=None)
        db = make_db()

        await handle_history_request(envelope("room", room_id=99), session, router, room_manager, db)

        router.send_error.assert_awaited_once()
        assert router.send_error.call_args[0][1] == ErrorCode.ROOM_NOT_FOUND

    @pytest.mark.asyncio
    async def test_no_access_gets_forbidden(self):
        session = make_session()
        router = make_router()
        room_manager = make_room_manager(
            room={"id": 1, "name": "general", "is_private": True},
            is_member=False, has_access=False,
        )
        db = make_db()

        await handle_history_request(envelope("room", room_id=1), session, router, room_manager, db)

        router.send_error.assert_awaited_once()
        assert router.send_error.call_args[0][1] == ErrorCode.FORBIDDEN

    @pytest.mark.asyncio
    async def test_success_returns_messages_in_chronological_order(self):
        session = make_session(user_id=1, username="alice")
        router = make_router()
        room_manager = make_room_manager(
            room={"id": 1, "name": "general", "is_private": False},
            is_member=True, has_access=True,
        )
        # Baza zwraca DESC (najnowsze pierwsze) — handler musi odwrócić kolejność
        rows = [
            make_row("m2", 2, "bob", "druga"),
            make_row("m1", 1, "alice", "pierwsza"),
        ]
        db = make_db(fetch_result=rows)

        await handle_history_request(envelope("room", room_id=1), session, router, room_manager, db)

        router._write.assert_awaited_once()
        frame = router._write.call_args[0][1]
        assert frame["type"] == "HISTORY_RESPONSE"
        assert frame["payload"]["history_type"] == "room"
        assert frame["payload"]["room_id"] == 1
        bodies = [m["body"] for m in frame["payload"]["messages"]]
        assert bodies == ["pierwsza", "druga"]

    @pytest.mark.asyncio
    async def test_offline_but_has_access_can_fetch_history(self):
        """Użytkownik z dostępem do pokoju, ale nie aktualnie w nim (in-memory),
        nadal może pobrać historię (np. zaraz po zalogowaniu, przed JOIN_ROOM)."""
        session = make_session(user_id=1, username="alice")
        router = make_router()
        room_manager = make_room_manager(
            room={"id": 1, "name": "general", "is_private": False},
            is_member=False, has_access=True,
        )
        db = make_db(fetch_result=[])

        await handle_history_request(envelope("room", room_id=1), session, router, room_manager, db)

        router.send_error.assert_not_awaited()
        router._write.assert_awaited_once()


# ---------------------------------------------------------------------------
# Historia DM
# ---------------------------------------------------------------------------

class TestDmHistory:
    @pytest.mark.asyncio
    async def test_unknown_user_gets_error(self):
        session = make_session()
        router = make_router()
        room_manager = make_room_manager()
        db = make_db(fetchrow_result=None)

        await handle_history_request(envelope("dm", username="ghost"), session, router, room_manager, db)

        router.send_error.assert_awaited_once()
        assert router.send_error.call_args[0][1] == ErrorCode.USER_NOT_FOUND

    @pytest.mark.asyncio
    async def test_success_returns_both_directions_in_order(self):
        session = make_session(user_id=1, username="alice")
        router = make_router()
        room_manager = make_room_manager()
        rows = [
            make_row("m2", 2, "bob", "odpowiedź"),
            make_row("m1", 1, "alice", "cześć"),
        ]
        db = make_db(fetch_result=rows, fetchrow_result={"id": 2})

        await handle_history_request(envelope("dm", username="bob"), session, router, room_manager, db)

        router._write.assert_awaited_once()
        frame = router._write.call_args[0][1]
        assert frame["payload"]["history_type"] == "dm"
        assert frame["payload"]["username"] == "bob"
        bodies = [m["body"] for m in frame["payload"]["messages"]]
        assert bodies == ["cześć", "odpowiedź"]

    @pytest.mark.asyncio
    async def test_limit_is_clamped_to_max(self):
        session = make_session(user_id=1, username="alice")
        router = make_router()
        room_manager = make_room_manager()
        db = make_db(fetch_result=[], fetchrow_result={"id": 2})

        await handle_history_request(
            envelope("dm", username="bob", limit=99999), session, router, room_manager, db
        )

        # Ostatni pozycyjny argument fetch() to limit — musi być <= HISTORY_MAX_LIMIT
        from server.config import Config
        called_limit = db.fetch.call_args[0][-1]
        assert called_limit <= Config.HISTORY_MAX_LIMIT
