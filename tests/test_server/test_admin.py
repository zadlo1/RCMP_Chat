"""
Testy jednostkowe dla handlerów admin: handle_create_room i handle_delete_room.
"""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from server.handlers.admin import (
    handle_create_room, handle_delete_room,
    handle_admin_users_request, handle_delete_user, handle_set_user_role,
)
from server.session import Session, SessionManager
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_session_manager(target_session=None):
    sm = MagicMock(spec=SessionManager)
    sm.get_by_user_id = MagicMock(return_value=target_session)
    sm.remove_session = MagicMock()
    return sm


def make_txn_db(fetchrow_return=None, fetchval_return=0):
    db = AsyncMock()
    db.fetchrow = AsyncMock(return_value=fetchrow_return)
    db.fetchval = AsyncMock(return_value=fetchval_return)
    db.execute = AsyncMock()

    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    txn = AsyncMock()
    txn.__aenter__ = AsyncMock(return_value=txn)
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn)
    db.acquire = MagicMock(return_value=conn)
    return db


# ---------------------------------------------------------------------------
# handle_admin_users_request
# ---------------------------------------------------------------------------

class TestHandleAdminUsersRequest:
    def _envelope(self):
        return {
            "type": "ADMIN_USERS_REQUEST",
            "msg_id": "mid-u1",
            "ts": int(time.time() * 1000),
            "token": "tok",
            "payload": {},
        }

    @pytest.mark.asyncio
    async def test_non_admin_gets_forbidden(self):
        session = make_session(role="user")
        router = make_router()
        db = make_db()
        await handle_admin_users_request(self._envelope(), session, router, db)
        router.send_error.assert_awaited_once()
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.FORBIDDEN

    @pytest.mark.asyncio
    async def test_success_returns_users_list(self):
        session = make_session(role="admin")
        router = make_router()
        db = make_db()
        db.fetch = AsyncMock(return_value=[
            {"id": 1, "username": "admin_user", "role": "admin", "status": "online",
             "is_blocked": False, "created_at": None, "last_seen": None},
            {"id": 2, "username": "bob", "role": "user", "status": "offline",
             "is_blocked": False, "created_at": None, "last_seen": None},
        ])
        await handle_admin_users_request(self._envelope(), session, router, db)
        router._write.assert_awaited_once()
        sent = router._write.call_args[0][1]
        assert sent["type"] == "ADMIN_USERS_LIST"
        assert len(sent["payload"]["users"]) == 2
        assert sent["payload"]["users"][1]["username"] == "bob"


# ---------------------------------------------------------------------------
# handle_delete_user
# ---------------------------------------------------------------------------

class TestHandleDeleteUser:
    def _envelope(self, user_id=2):
        return {
            "type": "DELETE_USER",
            "msg_id": "mid-d1",
            "ts": int(time.time() * 1000),
            "token": "tok",
            "payload": {"user_id": user_id} if user_id is not None else {},
        }

    @pytest.mark.asyncio
    async def test_non_admin_gets_forbidden(self):
        session = make_session(role="user")
        router = make_router()
        db = make_db()
        sm = make_session_manager()
        rm = make_room_manager()
        await handle_delete_user(self._envelope(), session, router, db, sm, rm)
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.FORBIDDEN

    @pytest.mark.asyncio
    async def test_missing_user_id_gets_error(self):
        session = make_session(role="admin")
        router = make_router()
        db = make_db()
        sm = make_session_manager()
        rm = make_room_manager()
        await handle_delete_user(self._envelope(user_id=None), session, router, db, sm, rm)
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.MALFORMED_ENVELOPE

    @pytest.mark.asyncio
    async def test_cannot_delete_self(self):
        session = make_session(role="admin", user_id=1)
        router = make_router()
        db = make_db()
        sm = make_session_manager()
        rm = make_room_manager()
        await handle_delete_user(self._envelope(user_id=1), session, router, db, sm, rm)
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.SELF_ACTION_FORBIDDEN

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        session = make_session(role="admin")
        router = make_router()
        db = make_txn_db(fetchrow_return=None)
        sm = make_session_manager()
        rm = make_room_manager()
        await handle_delete_user(self._envelope(user_id=999), session, router, db, sm, rm)
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.USER_NOT_FOUND

    @pytest.mark.asyncio
    async def test_cannot_delete_last_admin(self):
        session = make_session(role="admin", user_id=1)
        router = make_router()
        db = make_txn_db(
            fetchrow_return={"id": 2, "username": "other_admin", "role": "admin"},
            fetchval_return=1,
        )
        sm = make_session_manager()
        rm = make_room_manager()
        await handle_delete_user(self._envelope(user_id=2), session, router, db, sm, rm)
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.LAST_ADMIN

    @pytest.mark.asyncio
    async def test_success_deletes_offline_user(self):
        session = make_session(role="admin", user_id=1)
        router = make_router()
        db = make_txn_db(fetchrow_return={"id": 2, "username": "bob", "role": "user"})
        sm = make_session_manager(target_session=None)
        rm = make_room_manager()

        await handle_delete_user(self._envelope(user_id=2), session, router, db, sm, rm)

        router._write.assert_awaited_once()
        response = router._write.call_args[0][1]
        assert response["type"] == "DELETE_USER_OK"
        assert response["payload"]["user_id"] == 2
        assert response["payload"]["username"] == "bob"

    @pytest.mark.asyncio
    async def test_success_notifies_online_user_and_closes_session(self):
        session = make_session(role="admin", user_id=1)
        router = make_router()
        db = make_txn_db(fetchrow_return={"id": 2, "username": "bob", "role": "user"})

        target_writer = make_writer()
        target_session = Session(writer=target_writer, ip="127.0.0.2")
        target_session.user_id = 2
        target_session.username = "bob"
        target_session.role = "user"
        target_session.state = "ACTIVE"

        sm = make_session_manager(target_session=target_session)
        rm = make_room_manager()
        rm.leave_all_rooms = MagicMock()

        await handle_delete_user(self._envelope(user_id=2), session, router, db, sm, rm)

        # Wysłano ACCOUNT_DELETED bezpośrednio do writera celu
        router._write.await_args_list
        notice_call = router._write.await_args_list[0]
        notice = notice_call[0][1]
        assert notice["type"] == "ACCOUNT_DELETED"

        rm.leave_all_rooms.assert_called_once_with(2)
        router.unregister.assert_called_once_with(2)
        sm.remove_session.assert_called_once_with(target_session)

        # Odpowiedź dla admina
        response_call = router._write.await_args_list[1]
        response = response_call[0][1]
        assert response["type"] == "DELETE_USER_OK"


# ---------------------------------------------------------------------------
# handle_set_user_role
# ---------------------------------------------------------------------------

class TestHandleSetUserRole:
    def _envelope(self, user_id=2, role="admin"):
        payload = {}
        if user_id is not None:
            payload["user_id"] = user_id
        if role is not None:
            payload["role"] = role
        return {
            "type": "SET_USER_ROLE",
            "msg_id": "mid-r1",
            "ts": int(time.time() * 1000),
            "token": "tok",
            "payload": payload,
        }

    @pytest.mark.asyncio
    async def test_non_admin_gets_forbidden(self):
        session = make_session(role="user")
        router = make_router()
        db = make_db()
        sm = make_session_manager()
        await handle_set_user_role(self._envelope(), session, router, db, sm)
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.FORBIDDEN

    @pytest.mark.asyncio
    async def test_missing_fields_gets_error(self):
        session = make_session(role="admin")
        router = make_router()
        db = make_db()
        sm = make_session_manager()
        await handle_set_user_role(self._envelope(role=None), session, router, db, sm)
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.MALFORMED_ENVELOPE

    @pytest.mark.asyncio
    async def test_invalid_role_gets_error(self):
        session = make_session(role="admin")
        router = make_router()
        db = make_db()
        sm = make_session_manager()
        await handle_set_user_role(self._envelope(role="superuser"), session, router, db, sm)
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.INVALID_ROLE

    @pytest.mark.asyncio
    async def test_cannot_change_own_role(self):
        session = make_session(role="admin", user_id=1)
        router = make_router()
        db = make_db()
        sm = make_session_manager()
        await handle_set_user_role(self._envelope(user_id=1, role="user"), session, router, db, sm)
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.SELF_ACTION_FORBIDDEN

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        session = make_session(role="admin")
        router = make_router()
        db = make_db()
        db.fetchrow = AsyncMock(return_value=None)
        sm = make_session_manager()
        await handle_set_user_role(self._envelope(user_id=999), session, router, db, sm)
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.USER_NOT_FOUND

    @pytest.mark.asyncio
    async def test_cannot_demote_last_admin(self):
        session = make_session(role="admin", user_id=1)
        router = make_router()
        db = make_db()
        db.fetchrow = AsyncMock(return_value={"id": 2, "username": "other_admin", "role": "admin"})
        db.fetchval = AsyncMock(return_value=1)
        sm = make_session_manager()
        await handle_set_user_role(self._envelope(user_id=2, role="user"), session, router, db, sm)
        code = router.send_error.call_args[0][1]
        assert code == ErrorCode.LAST_ADMIN

    @pytest.mark.asyncio
    async def test_success_promotes_user(self):
        session = make_session(role="admin", user_id=1)
        router = make_router()
        db = make_db()
        db.fetchrow = AsyncMock(return_value={"id": 2, "username": "bob", "role": "user"})
        db.execute = AsyncMock()
        sm = make_session_manager(target_session=None)

        await handle_set_user_role(self._envelope(user_id=2, role="admin"), session, router, db, sm)

        db.execute.assert_awaited_once_with("UPDATE users SET role = $1 WHERE id = $2", "admin", 2)
        router._write.assert_awaited_once()
        response = router._write.call_args[0][1]
        assert response["type"] == "SET_USER_ROLE_OK"
        assert response["payload"]["role"] == "admin"

    @pytest.mark.asyncio
    async def test_success_notifies_online_user(self):
        session = make_session(role="admin", user_id=1)
        router = make_router()
        db = make_db()
        db.fetchrow = AsyncMock(return_value={"id": 2, "username": "bob", "role": "user"})
        db.execute = AsyncMock()

        target_writer = make_writer()
        target_session = Session(writer=target_writer, ip="127.0.0.2")
        target_session.user_id = 2
        target_session.username = "bob"
        target_session.role = "user"
        target_session.token = "bob-tok"
        target_session.state = "ACTIVE"

        sm = make_session_manager(target_session=target_session)

        await handle_set_user_role(self._envelope(user_id=2, role="admin"), session, router, db, sm)

        assert target_session.role == "admin"

        notice_call = router._write.await_args_list[0]
        notice = notice_call[0][1]
        assert notice["type"] == "ROLE_CHANGED"
        assert notice["payload"]["role"] == "admin"

        response_call = router._write.await_args_list[1]
        response = response_call[0][1]
        assert response["type"] == "SET_USER_ROLE_OK"
