"""
Testy jednostkowe dla RCMPConnection.
Testują logikę połączenia, backoff, disconnect i stan is_connected
bez rzeczywistego połączenia sieciowego.
"""
import asyncio
import ssl
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from client.protocol.connection import RCMPConnection
from server.config import Config


def make_mock_writer():
    writer = MagicMock()
    writer.close = MagicMock()
    return writer


def make_mock_reader():
    return MagicMock()


# ---------------------------------------------------------------------------
# Testy stanu is_connected
# ---------------------------------------------------------------------------

class TestIsConnected:
    def test_initially_not_connected(self):
        conn = RCMPConnection()
        assert conn.is_connected() is False

    def test_connected_true_with_writer(self):
        conn = RCMPConnection()
        conn.connected = True
        conn.writer = make_mock_writer()
        assert conn.is_connected() is True

    def test_connected_false_when_flag_false(self):
        conn = RCMPConnection()
        conn.connected = False
        conn.writer = make_mock_writer()
        assert conn.is_connected() is False

    def test_connected_false_sets_flag_when_writer_none(self):
        conn = RCMPConnection()
        conn.connected = True
        conn.writer = None
        assert conn.is_connected() is False
        assert conn.connected is False

    def test_connected_false_without_writer(self):
        conn = RCMPConnection()
        conn.connected = True
        conn.writer = None
        assert conn.is_connected() is False


# ---------------------------------------------------------------------------
# Testy disconnect
# ---------------------------------------------------------------------------

class TestDisconnect:
    def test_disconnect_sets_connected_false(self):
        conn = RCMPConnection()
        conn.connected = True
        conn.writer = make_mock_writer()
        conn.disconnect()
        assert conn.connected is False

    def test_disconnect_calls_writer_close(self):
        conn = RCMPConnection()
        conn.connected = True
        writer = make_mock_writer()
        conn.writer = writer
        conn.disconnect()
        writer.close.assert_called_once()

    def test_disconnect_without_writer_does_not_raise(self):
        conn = RCMPConnection()
        conn.connected = True
        conn.writer = None
        conn.disconnect()
        assert conn.connected is False

    def test_disconnect_when_writer_close_raises(self):
        conn = RCMPConnection()
        conn.connected = True
        writer = make_mock_writer()
        writer.close.side_effect = OSError("broken pipe")
        conn.writer = writer
        conn.disconnect()
        assert conn.connected is False


# ---------------------------------------------------------------------------
# Testy backoff
# ---------------------------------------------------------------------------

class TestBackoff:
    def test_initial_backoff_index_zero(self):
        conn = RCMPConnection()
        assert conn._backoff_idx == 0

    def test_reset_backoff(self):
        conn = RCMPConnection()
        conn._backoff_idx = 5
        conn.reset_backoff()
        assert conn._backoff_idx == 0

    def test_backoff_sequence_values(self):
        assert RCMPConnection.BACKOFF == [1, 2, 4, 8, 16, 32, 60]

    def test_backoff_capped_at_max(self):
        conn = RCMPConnection()
        conn._backoff_idx = 100
        delay = RCMPConnection.BACKOFF[min(conn._backoff_idx, len(RCMPConnection.BACKOFF) - 1)]
        assert delay == 60

    def test_backoff_first_delay_is_one(self):
        conn = RCMPConnection()
        delay = RCMPConnection.BACKOFF[min(conn._backoff_idx, len(RCMPConnection.BACKOFF) - 1)]
        assert delay == 1


# ---------------------------------------------------------------------------
# Testy connect() — sukces i błędy sieciowe
# ---------------------------------------------------------------------------

class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_success_sets_connected_true(self):
        conn = RCMPConnection()
        mock_reader = make_mock_reader()
        mock_writer = make_mock_writer()

        with patch("client.protocol.connection.ssl.SSLContext"), \
             patch("asyncio.wait_for", new=AsyncMock(return_value=(mock_reader, mock_writer))):
            result = await conn.connect("127.0.0.1", 9999)

        assert result is True
        assert conn.connected is True
        assert conn._backoff_idx == 0

    @pytest.mark.asyncio
    async def test_connect_refused_returns_false(self):
        conn = RCMPConnection()

        with patch("client.protocol.connection.ssl.SSLContext"), \
             patch("asyncio.wait_for", new=AsyncMock(side_effect=ConnectionRefusedError)):
            result = await conn.connect("127.0.0.1", 9999)

        assert result is False
        assert conn.connected is False

    @pytest.mark.asyncio
    async def test_connect_timeout_returns_false(self):
        conn = RCMPConnection()

        with patch("client.protocol.connection.ssl.SSLContext"), \
             patch("asyncio.wait_for", new=AsyncMock(side_effect=asyncio.TimeoutError)):
            result = await conn.connect("127.0.0.1", 9999)

        assert result is False
        assert conn.connected is False

    @pytest.mark.asyncio
    async def test_connect_os_error_returns_false(self):
        conn = RCMPConnection()

        with patch("client.protocol.connection.ssl.SSLContext"), \
             patch("asyncio.wait_for", new=AsyncMock(side_effect=OSError("network error"))):
            result = await conn.connect("127.0.0.1", 9999)

        assert result is False
        assert conn.connected is False

    @pytest.mark.asyncio
    async def test_connect_success_resets_backoff(self):
        conn = RCMPConnection()
        conn._backoff_idx = 4
        mock_reader = make_mock_reader()
        mock_writer = make_mock_writer()

        with patch("client.protocol.connection.ssl.SSLContext"), \
             patch("asyncio.wait_for", new=AsyncMock(return_value=(mock_reader, mock_writer))):
            await conn.connect("127.0.0.1", 9999)

        assert conn._backoff_idx == 0

    @pytest.mark.asyncio
    async def test_connect_failure_does_not_reset_backoff(self):
        conn = RCMPConnection()
        conn._backoff_idx = 3

        with patch("client.protocol.connection.ssl.SSLContext"), \
             patch("asyncio.wait_for", new=AsyncMock(side_effect=ConnectionRefusedError)):
            await conn.connect("127.0.0.1", 9999)

        assert conn._backoff_idx == 3


# ---------------------------------------------------------------------------
# Testy reconnect() — logika backoff i inkrementacja idx
# ---------------------------------------------------------------------------

class TestReconnect:
    @pytest.mark.asyncio
    async def test_reconnect_increments_backoff_index(self):
        conn = RCMPConnection()
        assert conn._backoff_idx == 0

        with patch("client.protocol.connection.ssl.SSLContext"), \
             patch("asyncio.wait_for", new=AsyncMock(side_effect=ConnectionRefusedError)), \
             patch("asyncio.sleep", new=AsyncMock()):
            await conn.reconnect("127.0.0.1", 9999)

        assert conn._backoff_idx == 1

    @pytest.mark.asyncio
    async def test_reconnect_uses_correct_delay_sequence(self):
        conn = RCMPConnection()
        sleep_calls = []

        async def fake_sleep(delay):
            sleep_calls.append(delay)

        with patch("client.protocol.connection.ssl.SSLContext"), \
             patch("asyncio.wait_for", new=AsyncMock(side_effect=ConnectionRefusedError)), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            await conn.reconnect("127.0.0.1", 9999)  # backoff_idx=0 → delay=1
            await conn.reconnect("127.0.0.1", 9999)  # backoff_idx=1 → delay=2
            await conn.reconnect("127.0.0.1", 9999)  # backoff_idx=2 → delay=4

        assert sleep_calls == [1, 2, 4]

    @pytest.mark.asyncio
    async def test_reconnect_success_returns_true(self):
        conn = RCMPConnection()
        mock_reader = make_mock_reader()
        mock_writer = make_mock_writer()

        with patch("client.protocol.connection.ssl.SSLContext"), \
             patch("asyncio.wait_for", new=AsyncMock(return_value=(mock_reader, mock_writer))), \
             patch("asyncio.sleep", new=AsyncMock()):
            result = await conn.reconnect("127.0.0.1", 9999)

        assert result is True

    @pytest.mark.asyncio
    async def test_reconnect_max_backoff_capped_at_60(self):
        conn = RCMPConnection()
        conn._backoff_idx = 10
        sleep_calls = []

        async def fake_sleep(delay):
            sleep_calls.append(delay)

        with patch("client.protocol.connection.ssl.SSLContext"), \
             patch("asyncio.wait_for", new=AsyncMock(side_effect=ConnectionRefusedError)), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            await conn.reconnect("127.0.0.1", 9999)

        assert sleep_calls[0] == 60


# ---------------------------------------------------------------------------
# Testy get_tls_version
# ---------------------------------------------------------------------------

class TestGetTlsVersion:
    def test_returns_tls_string_without_writer(self):
        conn = RCMPConnection()
        assert conn.get_tls_version() == "TLS"

    def test_returns_version_from_ssl_object(self):
        conn = RCMPConnection()
        writer = MagicMock()
        ssl_obj = MagicMock()
        ssl_obj.version.return_value = "TLSv1.3"
        writer.get_extra_info.return_value = ssl_obj
        conn.writer = writer
        assert conn.get_tls_version() == "TLSv1.3"

    def test_falls_back_to_tls_on_exception(self):
        conn = RCMPConnection()
        writer = MagicMock()
        writer.get_extra_info.side_effect = Exception("ssl error")
        conn.writer = writer
        assert conn.get_tls_version() == "TLS"

    def test_falls_back_when_ssl_object_none(self):
        conn = RCMPConnection()
        writer = MagicMock()
        writer.get_extra_info.return_value = None
        conn.writer = writer
        assert conn.get_tls_version() == "TLS"
