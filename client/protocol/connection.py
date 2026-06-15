import asyncio
import logging
import ssl
import time

from server.config import Config

logger = logging.getLogger("rcmp.client.connection")


class RCMPConnection:
    """
    Zarządza połączeniem TCP/TLS z serwerem RCMP.
    Obsługuje reconnect z wykładniczym backoff.
    """

    BACKOFF = [1, 2, 4, 8, 16, 32, 60]

    def __init__(self):
        self.reader: asyncio.StreamReader = None
        self.writer: asyncio.StreamWriter = None
        self.connected = False
        self._backoff_idx = 0

    # ------------------------------------------------------------------
    # Połączenie
    # ------------------------------------------------------------------

    async def connect(self, host: str = None, port: int = None) -> bool:
        """
        Nawiązuje połączenie TLS z serwerem.
        Zwraca True jeśli połączono, False jeśli błąd.
        """
        host = host or Config.HOST
        port = port or Config.PORT

        ssl_ctx = self._build_ssl_context()

        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=ssl_ctx),
                timeout=10.0
            )
            self.connected = True
            self._backoff_idx = 0
            return True
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError) as e:
            logger.warning("Błąd połączenia z %s:%s — %s", host, port, e)
            self.connected = False
            return False

    async def reconnect(self, host: str = None, port: int = None) -> bool:
        """
        Próbuje ponownie połączyć z wykładniczym backoff.
        """
        delay = self.BACKOFF[min(self._backoff_idx, len(self.BACKOFF) - 1)]
        self._backoff_idx += 1
        logger.info("Reconnect za %ds...", delay)
        await asyncio.sleep(delay)
        return await self.connect(host, port)

    def disconnect(self):
        """Zamyka połączenie."""
        self.connected = False
        if self.writer:
            try:
                self.writer.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # SSL
    # ------------------------------------------------------------------

    def _build_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # Self-signed certyfikat — ładujemy go jako zaufany CA
        ctx.load_verify_locations(Config.TLS_CERT_PATH)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    # ------------------------------------------------------------------
    # Właściwości
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        if not self.connected:
            return False
        if self.writer is None:
            self.connected = False
            return False
        return True

    def reset_backoff(self):
        self._backoff_idx = 0

    def get_tls_version(self) -> str:
        """Zwraca wersję TLS aktywnego połączenia np. 'TLSv1.3'."""
        if not self.writer:
            return "TLS"
        try:
            ssl_obj = self.writer.get_extra_info("ssl_object")
            if ssl_obj:
                return ssl_obj.version() or "TLS"
        except Exception:
            pass
        return "TLS"
