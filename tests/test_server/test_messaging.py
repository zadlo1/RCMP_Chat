"""
Testy jednostkowe dla logiki wiadomości — Session (duplikaty) i schematu validate_envelope.
"""
import time
import pytest

from server.session import Session, SessionManager
from shared.schemas import validate_envelope
from shared.error_codes import ErrorCode
from shared.message_types import MessageType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_envelope(msg_type="SEND_MESSAGE", token="tok", skew_ms=0):
    return {
        "type": msg_type,
        "msg_id": "some-uuid",
        "ts": int(time.time() * 1000) + skew_ms,
        "token": token,
        "payload": {},
    }


# ---------------------------------------------------------------------------
# Session: wykrywanie duplikatów
# ---------------------------------------------------------------------------

class TestSessionDuplicateDetection:
    def _session(self):
        import asyncio
        w = asyncio.StreamWriter.__new__(asyncio.StreamWriter)
        return Session(writer=w, ip="127.0.0.1")

    def test_first_msg_not_duplicate(self):
        s = self._session()
        assert s.is_duplicate_msg("msg-1") is False

    def test_same_msg_id_is_duplicate(self):
        s = self._session()
        s.store_msg_id("msg-1")
        assert s.is_duplicate_msg("msg-1") is True

    def test_different_msg_ids_not_duplicate(self):
        s = self._session()
        s.store_msg_id("msg-1")
        assert s.is_duplicate_msg("msg-2") is False

    def test_expired_msg_id_cleared(self):
        s = self._session()
        s.received_msg_ids["msg-old"] = time.time() - 301
        assert s.is_duplicate_msg("msg-old") is False

    def test_fresh_msg_id_retained(self):
        s = self._session()
        s.store_msg_id("msg-fresh")
        assert s.is_duplicate_msg("msg-fresh") is True


# ---------------------------------------------------------------------------
# Session: liczniki błędów
# ---------------------------------------------------------------------------

class TestSessionErrorCounters:
    def _session(self):
        import asyncio
        w = asyncio.StreamWriter.__new__(asyncio.StreamWriter)
        return Session(writer=w, ip="127.0.0.1")

    def test_format_error_increments(self):
        s = self._session()
        assert s.register_format_error() == 1
        assert s.register_format_error() == 2

    def test_auth_error_increments(self):
        s = self._session()
        assert s.register_auth_error() == 1
        assert s.register_auth_error() == 2

    def test_format_errors_expire(self):
        s = self._session()
        s.format_errors = [time.time() - 61, time.time() - 61]
        assert s.register_format_error() == 1  # stare usunięte

    def test_auth_errors_expire(self):
        s = self._session()
        s.auth_errors = [time.time() - 61, time.time() - 61]
        assert s.register_auth_error() == 1


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class TestSessionManager:
    def test_create_session_returns_session(self):
        import asyncio
        sm = SessionManager()
        w = asyncio.StreamWriter.__new__(asyncio.StreamWriter)
        s = sm.create_session(w, "127.0.0.1")
        assert s is not None
        assert s.state == "CONNECTED"

    def test_activate_session(self):
        import asyncio
        sm = SessionManager()
        w = asyncio.StreamWriter.__new__(asyncio.StreamWriter)
        s = sm.create_session(w, "127.0.0.1")
        s.user_id = 42
        sm.activate_session(s)
        assert sm.get_by_user_id(42) is s
        assert s.state == "ACTIVE"

    def test_remove_session(self):
        import asyncio
        sm = SessionManager()
        w = asyncio.StreamWriter.__new__(asyncio.StreamWriter)
        s = sm.create_session(w, "127.0.0.1")
        s.user_id = 99
        sm.activate_session(s)
        sm.remove_session(s)
        assert sm.get_by_user_id(99) is None
        assert s.state == "CLOSED"

    def test_get_timed_out(self):
        import asyncio
        sm = SessionManager()
        w = asyncio.StreamWriter.__new__(asyncio.StreamWriter)
        s = sm.create_session(w, "127.0.0.1")
        s.user_id = 7
        sm.activate_session(s)
        s.last_activity = time.time() - 999
        assert s in sm.get_timed_out(100)

    def test_all_active(self):
        import asyncio
        sm = SessionManager()
        for i in range(3):
            w = asyncio.StreamWriter.__new__(asyncio.StreamWriter)
            s = sm.create_session(w, "127.0.0.1")
            s.user_id = i
            sm.activate_session(s)
        assert len(sm.all_active()) == 3


# ---------------------------------------------------------------------------
# validate_envelope
# ---------------------------------------------------------------------------

class TestValidateEnvelope:
    def test_valid_login_envelope(self):
        env = {"type": "LOGIN", "msg_id": "x", "ts": int(time.time() * 1000), "payload": {}}
        ok, err = validate_envelope(env)
        assert ok is True
        assert err is None

    def test_valid_authenticated_envelope(self):
        env = _make_envelope("SEND_MESSAGE", token="mytoken")
        ok, err = validate_envelope(env)
        assert ok is True

    def test_missing_type_field(self):
        env = {"msg_id": "x", "ts": int(time.time() * 1000)}
        ok, err = validate_envelope(env)
        assert ok is False
        assert err == ErrorCode.MALFORMED_ENVELOPE

    def test_missing_msg_id_field(self):
        env = {"type": "LOGIN", "ts": int(time.time() * 1000)}
        ok, err = validate_envelope(env)
        assert ok is False
        assert err == ErrorCode.MALFORMED_ENVELOPE

    def test_missing_ts_field(self):
        env = {"type": "LOGIN", "msg_id": "x"}
        ok, err = validate_envelope(env)
        assert ok is False
        assert err == ErrorCode.MALFORMED_ENVELOPE

    def test_unknown_type(self):
        env = {"type": "NONEXISTENT_TYPE", "msg_id": "x", "ts": int(time.time() * 1000)}
        ok, err = validate_envelope(env)
        assert ok is False
        assert err == ErrorCode.UNKNOWN_TYPE

    def test_timestamp_too_old(self):
        env = _make_envelope(skew_ms=-(400 * 1000))  # 400 sekund w przeszłość
        ok, err = validate_envelope(env)
        assert ok is False
        assert err == ErrorCode.TIMESTAMP_SKEW

    def test_timestamp_too_future(self):
        env = _make_envelope(skew_ms=400 * 1000)  # 400 sekund w przyszłość
        ok, err = validate_envelope(env)
        assert ok is False
        assert err == ErrorCode.TIMESTAMP_SKEW

    def test_missing_token_for_authenticated_type(self):
        env = {
            "type": "SEND_MESSAGE",
            "msg_id": "x",
            "ts": int(time.time() * 1000),
            "payload": {},
        }
        ok, err = validate_envelope(env)
        assert ok is False
        assert err == ErrorCode.UNAUTHORIZED

    def test_login_does_not_require_token(self):
        env = {"type": "LOGIN", "msg_id": "x", "ts": int(time.time() * 1000), "payload": {}}
        ok, err = validate_envelope(env)
        assert ok is True

    def test_delete_room_type_recognized(self):
        env = _make_envelope("DELETE_ROOM", token="tok")
        ok, err = validate_envelope(env)
        assert ok is True
