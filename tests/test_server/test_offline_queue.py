"""
Testy jednostkowe dla kolejkowania offline: DM (messaging.py) i ROOM_INVITE (invite.py).
"""
import time
import uuid
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from server.session import Session
from server.managers.message_router import MessageRouter
from server.handlers.messaging import (
    handle_send_message, deliver_pending_direct_messages,
)
from server.handlers.invite import send_invite, deliver_pending_room_invites
from shared.crypto import compute_hmac


def make_writer():
    w = MagicMock(spec=asyncio.StreamWriter)
    w.write = MagicMock()
    w.drain = AsyncMock()
    return w


def make_session(user_id=1, username="alice", hmac_secret="secret"):
    s = Session(writer=make_writer(), ip="127.0.0.1")
    s.user_id = user_id
    s.username = username
    s.role = "user"
    s.token = "tok"
    s.hmac_secret = hmac_secret
    s.state = "ACTIVE"
    return s


def make_router(online: bool):
    router = MagicMock(spec=MessageRouter)
    router.send_error = AsyncMock()
    router._write = AsyncMock()
    router.is_online = MagicMock(return_value=online)
    router.send_to_user = AsyncMock(return_value=online)
    return router


def make_db(fetchrow_result=None, fetch_result=None):
    db = AsyncMock()
    db.fetchrow = AsyncMock(return_value=fetchrow_result)
    db.fetch = AsyncMock(return_value=fetch_result or [])
    db.execute = AsyncMock()
    return db


def make_rate_limiter():
    rl = MagicMock()
    rl.check_message_rate = MagicMock(return_value=True)
    return rl


def make_room_manager():
    return MagicMock()


# ---------------------------------------------------------------------------
# DM do offline użytkownika (target_type=user) trafia do bazy jako niedostarczona
# ---------------------------------------------------------------------------

class TestQueueDirectMessageWhenOffline:
    def _envelope(self, session, target_id=2, body="hej"):
        msg_id = str(uuid.uuid4())
        ts = int(time.time() * 1000)
        seq_id = 1
        h = compute_hmac(session.hmac_secret, msg_id, ts, seq_id, body)
        return {
            "type": "SEND_MESSAGE",
            "msg_id": msg_id,
            "ts": ts,
            "token": session.token,
            "payload": {
                "target_type": "user",
                "target_id": target_id,
                "seq_id": seq_id,
                "body": body,
                "hmac": h,
            },
        }

    @pytest.mark.asyncio
    async def test_offline_recipient_inserted_with_delivered_false(self):
        session = make_session()
        router = make_router(online=False)
        db = make_db(fetchrow_result={"id": 2})  # odbiorca istnieje w bazie
        data = self._envelope(session)

        await handle_send_message(data, session, router, make_room_manager(),
                                   make_rate_limiter(), db)

        insert_call = [c for c in db.execute.call_args_list if "INSERT INTO messages" in c.args[0]]
        assert len(insert_call) == 1
        args = insert_call[0].args
        assert args[-1] is False  # delivered = False

    @pytest.mark.asyncio
    async def test_online_recipient_inserted_with_delivered_true(self):
        session = make_session()
        router = make_router(online=True)
        db = make_db(fetchrow_result={"id": 2})
        data = self._envelope(session)

        await handle_send_message(data, session, router, make_room_manager(),
                                   make_rate_limiter(), db)

        insert_call = [c for c in db.execute.call_args_list if "INSERT INTO messages" in c.args[0]]
        args = insert_call[0].args
        assert args[-1] is True

    @pytest.mark.asyncio
    async def test_offline_recipient_still_gets_ack_to_sender(self):
        # Nadawca dostaje ACK niezależnie od statusu odbiorcy - to nie jest błąd.
        session = make_session()
        router = make_router(online=False)
        db = make_db(fetchrow_result={"id": 2})
        data = self._envelope(session)

        await handle_send_message(data, session, router, make_room_manager(),
                                   make_rate_limiter(), db)

        router.send_error.assert_not_awaited()
        ack_calls = [c for c in router._write.call_args_list if c.args[1]["type"] == "MESSAGE_ACK"]
        assert len(ack_calls) == 1

    @pytest.mark.asyncio
    async def test_nonexistent_recipient_still_errors(self):
        session = make_session()
        router = make_router(online=False)
        db = make_db(fetchrow_result=None)  # użytkownik nie istnieje w ogóle
        data = self._envelope(session, target_id=999)

        await handle_send_message(data, session, router, make_room_manager(),
                                   make_rate_limiter(), db)

        router.send_error.assert_awaited_once()


# ---------------------------------------------------------------------------
# deliver_pending_direct_messages
# ---------------------------------------------------------------------------

class TestDeliverPendingDirectMessages:
    @pytest.mark.asyncio
    async def test_no_pending_messages_writes_nothing(self):
        session = make_session()
        router = make_router(online=True)
        db = make_db(fetch_result=[])

        await deliver_pending_direct_messages(session, router, db)

        router._write.assert_not_awaited()
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pending_messages_delivered_and_marked(self):
        session = make_session(user_id=2)
        router = make_router(online=True)
        rows = [
            {"msg_id": uuid.uuid4(), "seq_id": 1, "body": "hej", "sent_at": None,
             "from_user_id": 1, "from_user": "alice"},
            {"msg_id": uuid.uuid4(), "seq_id": 2, "body": "co słychać", "sent_at": None,
             "from_user_id": 1, "from_user": "alice"},
        ]
        db = make_db(fetch_result=rows)

        await deliver_pending_direct_messages(session, router, db)

        assert router._write.await_count == 2
        db.execute.assert_awaited_once()
        assert "delivered = TRUE" in db.execute.call_args.args[0]

    @pytest.mark.asyncio
    async def test_delivered_messages_marked_as_queued(self):
        session = make_session(user_id=2)
        router = make_router(online=True)
        rows = [{"msg_id": uuid.uuid4(), "seq_id": 1, "body": "hej", "sent_at": None,
                 "from_user_id": 1, "from_user": "alice"}]
        db = make_db(fetch_result=rows)

        await deliver_pending_direct_messages(session, router, db)

        frame = router._write.call_args.args[1]
        assert frame["payload"]["queued"] is True


# ---------------------------------------------------------------------------
# ROOM_INVITE do offline użytkownika
# ---------------------------------------------------------------------------

class TestQueueRoomInviteWhenOffline:
    @pytest.mark.asyncio
    async def test_offline_invite_persisted(self):
        router = make_router(online=False)
        db = make_db()

        await send_invite(router, target_user_id=5, room_id=10,
                           room_name="secret-room", invited_by="admin_user", db_pool=db)

        insert_call = [c for c in db.execute.call_args_list if "INSERT INTO room_invites" in c.args[0]]
        assert len(insert_call) == 1
        assert insert_call[0].args[1:] == (10, 5, "admin_user")

    @pytest.mark.asyncio
    async def test_online_invite_not_persisted(self):
        router = make_router(online=True)
        db = make_db()

        await send_invite(router, target_user_id=5, room_id=10,
                           room_name="secret-room", invited_by="admin_user", db_pool=db)

        db.execute.assert_not_awaited()


class TestDeliverPendingRoomInvites:
    @pytest.mark.asyncio
    async def test_no_pending_invites_writes_nothing(self):
        session = make_session()
        router = make_router(online=True)
        db = make_db(fetch_result=[])

        await deliver_pending_room_invites(session, router, db)

        router._write.assert_not_awaited()
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pending_invites_delivered_and_cleared(self):
        session = make_session(user_id=5)
        router = make_router(online=True)
        rows = [{"id": 1, "room_id": 10, "invited_by": "admin_user",
                 "room_name": "secret-room", "is_private": True}]
        db = make_db(fetch_result=rows)

        await deliver_pending_room_invites(session, router, db)

        router._write.assert_awaited_once()
        frame = router._write.call_args.args[1]
        assert frame["type"] == "ROOM_INVITE"
        assert frame["payload"]["room_id"] == 10
        assert frame["payload"]["queued"] is True

        db.execute.assert_awaited_once()
        assert "DELETE FROM room_invites" in db.execute.call_args.args[0]
