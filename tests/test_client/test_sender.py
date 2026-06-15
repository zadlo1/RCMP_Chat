"""
Testy jednostkowe dla RCMPSender.
Testują budowanie ramek, obliczanie HMAC, retransmisję i kolejkę ACK
bez rzeczywistego połączenia sieciowego.
"""
import asyncio
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from client.protocol.sender import RCMPSender
from client.protocol.connection import RCMPConnection
from server.config import Config


def make_sender(connected: bool = True) -> tuple[RCMPSender, MagicMock]:
    """Tworzy RCMPSender z mockowanym, opcjonalnie połączonym RCMPConnection."""
    conn = MagicMock(spec=RCMPConnection)
    conn.is_connected.return_value = connected
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    conn.writer = writer
    sender = RCMPSender(conn)
    return sender, conn


def get_written_frame(writer) -> dict:
    """Deserializuje ostatnią ramkę JSON zapisaną przez writer.write()."""
    raw = writer.write.call_args[0][0]
    return json.loads(raw.decode("utf-8").strip())


# ---------------------------------------------------------------------------
# Testy send() — budowanie i wysyłanie ramek
# ---------------------------------------------------------------------------

class TestSend:
    @pytest.mark.asyncio
    async def test_send_returns_uuid_string(self):
        sender, _ = make_sender()
        sender.token = "tok"
        msg_id = await sender.send("PING")
        assert isinstance(msg_id, str)
        assert len(msg_id) == 36  # UUID v4

    @pytest.mark.asyncio
    async def test_send_frame_contains_required_fields(self):
        sender, conn = make_sender()
        sender.token = "mytoken"
        await sender.send("PING", {"key": "val"})
        frame = get_written_frame(conn.writer)
        assert frame["type"] == "PING"
        assert "msg_id" in frame
        assert "ts" in frame
        assert frame["token"] == "mytoken"
        assert frame["payload"] == {"key": "val"}

    @pytest.mark.asyncio
    async def test_send_empty_payload_when_none(self):
        sender, conn = make_sender()
        await sender.send("PING")
        frame = get_written_frame(conn.writer)
        assert frame["payload"] == {}

    @pytest.mark.asyncio
    async def test_send_does_nothing_when_disconnected(self):
        sender, conn = make_sender(connected=False)
        await sender.send("PING")
        conn.writer.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_each_call_produces_unique_msg_id(self):
        sender, _ = make_sender()
        id1 = await sender.send("PING")
        id2 = await sender.send("PING")
        assert id1 != id2

    @pytest.mark.asyncio
    async def test_send_timestamp_is_milliseconds(self):
        sender, conn = make_sender()
        before = int(time.time() * 1000)
        await sender.send("PING")
        after = int(time.time() * 1000)
        frame = get_written_frame(conn.writer)
        assert before <= frame["ts"] <= after

    @pytest.mark.asyncio
    async def test_send_frame_ends_with_newline(self):
        sender, conn = make_sender()
        await sender.send("PING")
        raw = conn.writer.write.call_args[0][0]
        assert raw.endswith(b"\n")

    @pytest.mark.asyncio
    async def test_send_handles_broken_pipe(self):
        sender, conn = make_sender()
        conn.writer.drain = AsyncMock(side_effect=BrokenPipeError)
        await sender.send("PING")
        assert conn.connected is False


# ---------------------------------------------------------------------------
# Testy send_login
# ---------------------------------------------------------------------------

class TestSendLogin:
    @pytest.mark.asyncio
    async def test_send_login_frame_type(self):
        sender, conn = make_sender()
        await sender.send_login("alice", "secret")
        frame = get_written_frame(conn.writer)
        assert frame["type"] == "LOGIN"

    @pytest.mark.asyncio
    async def test_send_login_includes_username_password_nonce(self):
        sender, conn = make_sender()
        await sender.send_login("alice", "secret")
        frame = get_written_frame(conn.writer)
        assert frame["payload"]["username"] == "alice"
        assert frame["payload"]["password"] == "secret"
        assert "nonce" in frame["payload"]
        assert len(frame["payload"]["nonce"]) == 8

    @pytest.mark.asyncio
    async def test_send_login_nonce_is_hex(self):
        sender, conn = make_sender()
        await sender.send_login("alice", "secret")
        frame = get_written_frame(conn.writer)
        nonce = frame["payload"]["nonce"]
        int(nonce, 16)  # nie powinno rzucić ValueError

    @pytest.mark.asyncio
    async def test_send_login_consecutive_calls_different_nonce(self):
        sender, conn = make_sender()
        await sender.send_login("alice", "secret")
        frame1 = get_written_frame(conn.writer)
        await sender.send_login("alice", "secret")
        frame2 = get_written_frame(conn.writer)
        assert frame1["payload"]["nonce"] != frame2["payload"]["nonce"]


# ---------------------------------------------------------------------------
# Testy send_message() — HMAC i kolejka ACK
# ---------------------------------------------------------------------------

class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_message_frame_type(self):
        sender, conn = make_sender()
        sender.hmac_secret = "secret"
        await sender.send_message("room", 1, "hello")
        frame = get_written_frame(conn.writer)
        assert frame["type"] == "SEND_MESSAGE"

    @pytest.mark.asyncio
    async def test_send_message_payload_fields(self):
        sender, conn = make_sender()
        sender.hmac_secret = "secret"
        await sender.send_message("room", 42, "cześć")
        frame = get_written_frame(conn.writer)
        p = frame["payload"]
        assert p["target_type"] == "room"
        assert p["target_id"] == 42
        assert p["body"] == "cześć"
        assert "seq_id" in p
        assert "hmac" in p

    @pytest.mark.asyncio
    async def test_send_message_seq_id_increments(self):
        sender, conn = make_sender()
        sender.hmac_secret = "secret"
        await sender.send_message("room", 1, "msg1")
        frame1 = get_written_frame(conn.writer)
        await sender.send_message("room", 1, "msg2")
        frame2 = get_written_frame(conn.writer)
        assert frame2["payload"]["seq_id"] == frame1["payload"]["seq_id"] + 1

    @pytest.mark.asyncio
    async def test_send_message_added_to_pending_acks(self):
        sender, conn = make_sender()
        sender.hmac_secret = "secret"
        msg_id = await sender.send_message("room", 1, "hello")
        assert msg_id in sender._pending_acks

    @pytest.mark.asyncio
    async def test_send_message_hmac_not_empty_with_secret(self):
        sender, conn = make_sender()
        sender.hmac_secret = "mysecret"
        await sender.send_message("room", 1, "hello")
        frame = get_written_frame(conn.writer)
        assert frame["payload"]["hmac"] != ""

    @pytest.mark.asyncio
    async def test_send_message_hmac_empty_without_secret(self):
        sender, conn = make_sender()
        sender.hmac_secret = None
        await sender.send_message("room", 1, "hello")
        frame = get_written_frame(conn.writer)
        assert frame["payload"]["hmac"] == ""


# ---------------------------------------------------------------------------
# Testy HMAC — compute_hmac
# ---------------------------------------------------------------------------

class TestComputeHmac:
    def test_hmac_is_hex_string(self):
        sender, _ = make_sender()
        sender.hmac_secret = "secret"
        result = sender._compute_hmac("msg_id", 123456, 1, "body")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex

    def test_hmac_same_inputs_same_output(self):
        sender, _ = make_sender()
        sender.hmac_secret = "secret"
        h1 = sender._compute_hmac("id", 100, 1, "text")
        h2 = sender._compute_hmac("id", 100, 1, "text")
        assert h1 == h2

    def test_hmac_different_body_different_output(self):
        sender, _ = make_sender()
        sender.hmac_secret = "secret"
        h1 = sender._compute_hmac("id", 100, 1, "aaa")
        h2 = sender._compute_hmac("id", 100, 1, "bbb")
        assert h1 != h2

    def test_hmac_different_secret_different_output(self):
        sender, _ = make_sender()
        sender.hmac_secret = "secret1"
        h1 = sender._compute_hmac("id", 100, 1, "body")
        sender.hmac_secret = "secret2"
        h2 = sender._compute_hmac("id", 100, 1, "body")
        assert h1 != h2

    def test_hmac_empty_without_secret(self):
        sender, _ = make_sender()
        sender.hmac_secret = None
        result = sender._compute_hmac("id", 100, 1, "body")
        assert result == ""


# ---------------------------------------------------------------------------
# Testy confirm_ack i check_retransmissions
# ---------------------------------------------------------------------------

class TestAckAndRetransmission:
    @pytest.mark.asyncio
    async def test_confirm_ack_removes_from_pending(self):
        sender, conn = make_sender()
        sender.hmac_secret = "secret"
        msg_id = await sender.send_message("room", 1, "hello")
        assert msg_id in sender._pending_acks
        sender.confirm_ack(msg_id)
        assert msg_id not in sender._pending_acks

    @pytest.mark.asyncio
    async def test_confirm_ack_nonexistent_does_not_raise(self):
        sender, _ = make_sender()
        sender.confirm_ack("nonexistent-id")

    @pytest.mark.asyncio
    async def test_retransmission_sent_after_timeout(self):
        sender, conn = make_sender()
        sender.hmac_secret = "secret"
        msg_id = await sender.send_message("room", 1, "hello")
        # Symuluj przekroczony timeout ACK
        frame, attempts, _ = sender._pending_acks[msg_id]
        sender._pending_acks[msg_id] = (frame, attempts, time.time() - Config.TIMEOUT_MESSAGE_ACK - 1)

        conn.writer.write.reset_mock()
        await sender.check_retransmissions()
        conn.writer.write.assert_called()

    @pytest.mark.asyncio
    async def test_no_retransmission_before_timeout(self):
        sender, conn = make_sender()
        sender.hmac_secret = "secret"
        await sender.send_message("room", 1, "hello")
        conn.writer.write.reset_mock()
        await sender.check_retransmissions()
        conn.writer.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_dropped_after_max_retransmits(self):
        sender, conn = make_sender()
        sender.hmac_secret = "secret"
        msg_id = await sender.send_message("room", 1, "hello")
        frame, _, _ = sender._pending_acks[msg_id]
        sender._pending_acks[msg_id] = (
            frame,
            Config.MESSAGE_RETRANSMIT_MAX,
            time.time() - Config.TIMEOUT_MESSAGE_ACK - 1
        )
        await sender.check_retransmissions()
        assert msg_id not in sender._pending_acks

    @pytest.mark.asyncio
    async def test_retransmission_increments_attempt_counter(self):
        sender, conn = make_sender()
        sender.hmac_secret = "secret"
        msg_id = await sender.send_message("room", 1, "hello")
        frame, _, _ = sender._pending_acks[msg_id]
        sender._pending_acks[msg_id] = (frame, 1, time.time() - Config.TIMEOUT_MESSAGE_ACK - 1)
        await sender.check_retransmissions()
        _, new_attempts, _ = sender._pending_acks[msg_id]
        assert new_attempts == 2


# ---------------------------------------------------------------------------
# Testy metod pomocniczych send_*
# ---------------------------------------------------------------------------

class TestHelperSendMethods:
    @pytest.mark.asyncio
    async def test_send_ping_type(self):
        sender, conn = make_sender()
        await sender.send_ping()
        frame = get_written_frame(conn.writer)
        assert frame["type"] == "PING"

    @pytest.mark.asyncio
    async def test_send_bye_type(self):
        sender, conn = make_sender()
        await sender.send_bye()
        frame = get_written_frame(conn.writer)
        assert frame["type"] == "BYE"

    @pytest.mark.asyncio
    async def test_send_message_ack_type_and_payload(self):
        sender, conn = make_sender()
        await sender.send_message_ack("some-uuid")
        frame = get_written_frame(conn.writer)
        assert frame["type"] == "MESSAGE_ACK"
        assert frame["payload"]["ack_msg_id"] == "some-uuid"

    @pytest.mark.asyncio
    async def test_send_join_room_payload(self):
        sender, conn = make_sender()
        await sender.send_join_room(7)
        frame = get_written_frame(conn.writer)
        assert frame["type"] == "JOIN_ROOM"
        assert frame["payload"]["room_id"] == 7

    @pytest.mark.asyncio
    async def test_send_leave_room_payload(self):
        sender, conn = make_sender()
        await sender.send_leave_room(3)
        frame = get_written_frame(conn.writer)
        assert frame["type"] == "LEAVE_ROOM"
        assert frame["payload"]["room_id"] == 3

    @pytest.mark.asyncio
    async def test_send_delete_user_payload(self):
        sender, conn = make_sender()
        await sender.send_delete_user(99)
        frame = get_written_frame(conn.writer)
        assert frame["type"] == "DELETE_USER"
        assert frame["payload"]["user_id"] == 99

    @pytest.mark.asyncio
    async def test_send_set_user_role_payload(self):
        sender, conn = make_sender()
        await sender.send_set_user_role(5, "admin")
        frame = get_written_frame(conn.writer)
        assert frame["type"] == "SET_USER_ROLE"
        assert frame["payload"]["user_id"] == 5
        assert frame["payload"]["role"] == "admin"

    @pytest.mark.asyncio
    async def test_send_room_kick_payload(self):
        sender, conn = make_sender()
        await sender.send_room_kick(10, 20)
        frame = get_written_frame(conn.writer)
        assert frame["type"] == "ROOM_KICK"
        assert frame["payload"]["room_id"] == 10
        assert frame["payload"]["user_id"] == 20

    @pytest.mark.asyncio
    async def test_send_room_ban_payload(self):
        sender, conn = make_sender()
        await sender.send_room_ban(10, 20)
        frame = get_written_frame(conn.writer)
        assert frame["type"] == "ROOM_BAN"

    @pytest.mark.asyncio
    async def test_send_room_unban_payload(self):
        sender, conn = make_sender()
        await sender.send_room_unban(10, 20)
        frame = get_written_frame(conn.writer)
        assert frame["type"] == "ROOM_UNBAN"
