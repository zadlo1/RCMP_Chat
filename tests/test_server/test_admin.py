"""
Testy jednostkowe dla handlerów admin: handle_create_room i handle_delete_room.
"""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from server.handlers.admin import handle_create_room, handle_delete_room
from server.session import Session
from server.managers.message_router import MessageRouter
from server.managers.room_manager import RoomManager
from shared.error_codes import ErrorCode

import asyncio


def make_writer():
    w = MagicMock(spec=asyncio.StreamWriter)
    w.write = MagicMock()
    w.drain = AsyncMock()
    return w


def make_session(role="admin", user_id=1, username="admin_user", token="tok"):
    w = make_writer()
    s = Session(writer=w, ip="127.0.0.1")
    s.user_id = user_id
    s.username = username
    s.role = role
    s.token = token
    s.state = "ACTIVE"
    return s


def make_router():
    router = MagicMock(spec=MessageRouter)
    router.send_error = AsyncMock()
    router._write = AsyncMock()
    router.send_to_room = AsyncMock()
    return router


def make_db():
    db = AsyncMock()
    return db


def make_room_manager():
    rm = MagicMock(spec=RoomManager)
    rm.get_room_members = MagicMock(return_value={1, 2})
    rm.remove_room = MagicMock()
    return rm


# ---------------------------------------------------------------------------
# handle_create_room
# ---------------------------------------------------------------------------

class TestHandleCreateRoom:
    def _envelope(self, name="testroom", is_private=False):
        return {
            "type": "CREATE_ROOM",
            "msg_id": "mid-1",
            "ts": int(time.time() * 1000),
            "token": "tok",
            "payload": {"name": name, "is_private": is_private},
        }

    @pytest.mark.asyncio
    async def test_non_admin_gets_forbidden(self):
        session = make_session(role="user")
        router = make_router()
        db = make_db()
        await handle_create_room(self._envelope(), session, router, db)
        router.send_error.assert_awaited_once()
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.FORBIDDEN

    @pytest.mark.asyncio
    async def test_missing_room_name_gets_error(self):
        session = make_session(role="admin")
        router = make_router()
        db = make_db()
        data = self._envelope(name="")
        await handle_create_room(data, session, router, db)
        router.send_error.assert_awaited_once()
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.MALFORMED_ENVELOPE

    @pytest.mark.asyncio
    async def test_duplicate_room_name_gets_error(self):
        session = make_session(role="admin")
        router = make_router()
        db = make_db()
        db.fetchrow = AsyncMock(return_value={"id": 1})  # pokój już istnieje
        await handle_create_room(self._envelope(), session, router, db)
        router.send_error.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success_creates_room_and_sends_ok(self):
        session = make_session(role="admin")
        router = make_router()
        db = make_db()
        db.fetchrow = AsyncMock(side_effect=[None, {"id": 42}])  # brak duplikatu, INSERT ok
        db.execute = AsyncMock()
        await handle_create_room(self._envelope("newroom"), session, router, db)
        router._write.assert_awaited_once()
        sent = router._write.call_args[0][1]
        assert sent["type"] == "CREATE_ROOM_OK"
        assert sent["payload"]["name"] == "newroom"

    @pytest.mark.asyncio
    async def test_private_room_adds_admin_to_acl(self):
        session = make_session(role="admin", user_id=5)
        router = make_router()
        db = make_db()
        db.fetchrow = AsyncMock(side_effect=[None, {"id": 10}])
        db.execute = AsyncMock()
        await handle_create_room(self._envelope("private", is_private=True), session, router, db)
        # Sprawdź że execute było wywołane (INSERT INTO room_acl)
        db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_room_name_too_long_gets_error(self):
        from server.config import Config
        session = make_session(role="admin")
        router = make_router()
        db = make_db()
        db.fetchrow = AsyncMock(return_value=None)
        long_name = "x" * (Config.MAX_NAME_LENGTH + 1)
        await handle_create_room(self._envelope(long_name), session, router, db)
        router.send_error.assert_awaited_once()
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.MALFORMED_ENVELOPE


# ---------------------------------------------------------------------------
# handle_delete_room
# ---------------------------------------------------------------------------

class TestHandleDeleteRoom:
    def _envelope(self, room_id=1):
        return {
            "type": "DELETE_ROOM",
            "msg_id": "mid-2",
            "ts": int(time.time() * 1000),
            "token": "tok",
            "payload": {"room_id": room_id},
        }

    @pytest.mark.asyncio
    async def test_non_admin_gets_forbidden(self):
        session = make_session(role="user")
        router = make_router()
        db = make_db()
        rm = make_room_manager()
        await handle_delete_room(self._envelope(), session, router, db, rm)
        router.send_error.assert_awaited_once()
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.FORBIDDEN

    @pytest.mark.asyncio
    async def test_missing_room_id_gets_error(self):
        session = make_session(role="admin")
        router = make_router()
        db = make_db()
        rm = make_room_manager()
        data = {
            "type": "DELETE_ROOM", "msg_id": "m",
            "ts": int(time.time() * 1000), "token": "tok",
            "payload": {},  # brak room_id
        }
        await handle_delete_room(data, session, router, db, rm)
        router.send_error.assert_awaited_once()
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.MALFORMED_ENVELOPE

    @pytest.mark.asyncio
    async def test_nonexistent_room_gets_not_found(self):
        session = make_session(role="admin")
        router = make_router()
        db = make_db()
        db.fetchrow = AsyncMock(return_value=None)
        rm = make_room_manager()
        await handle_delete_room(self._envelope(room_id=999), session, router, db, rm)
        router.send_error.assert_awaited_once()
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.ROOM_NOT_FOUND

    @pytest.mark.asyncio
    async def test_success_notifies_members_and_cleans_up(self):
        session = make_session(role="admin", username="admin1")
        router = make_router()
        db = make_db()
        db.fetchrow = AsyncMock(return_value={"id": 1, "name": "general"})

        # Symulacja transakcji
        conn = AsyncMock()
        conn.execute = AsyncMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=False)
        txn = AsyncMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=txn)
        db.acquire = MagicMock(return_value=conn)

        rm = make_room_manager()
        rm.get_room_members = MagicMock(return_value={2, 3})

        await handle_delete_room(self._envelope(room_id=1), session, router, db, rm)

        # Sprawdź powiadomienie członków
        router.send_to_room.assert_awaited_once()
        event = router.send_to_room.call_args[0][1]
        assert event["type"] == "ROOM_EVENT"
        assert event["payload"]["event"] == "deleted"
        assert event["payload"]["room_name"] == "general"

        # Sprawdź wyczyszczenie in-memory
        rm.remove_room.assert_called_once_with(1)

        # Sprawdź odpowiedź do admina
        router._write.assert_awaited_once()
        response = router._write.call_args[0][1]
        assert response["type"] == "DELETE_ROOM_OK"
        assert response["payload"]["room_id"] == 1
        assert response["payload"]["room_name"] == "general"

    @pytest.mark.asyncio
    async def test_delete_event_includes_deleted_by(self):
        session = make_session(role="admin", username="superadmin")
        router = make_router()
        db = make_db()
        db.fetchrow = AsyncMock(return_value={"id": 5, "name": "lobby"})

        conn = AsyncMock()
        conn.execute = AsyncMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=False)
        txn = AsyncMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=txn)
        db.acquire = MagicMock(return_value=conn)

        rm = make_room_manager()
        await handle_delete_room(self._envelope(room_id=5), session, router, db, rm)

        event = router.send_to_room.call_args[0][1]
        assert event["payload"]["deleted_by"] == "superadmin"
