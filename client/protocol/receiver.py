import asyncio
import json
from typing import Callable, Awaitable

from client.protocol.connection import RCMPConnection
from server.config import Config


class RCMPReceiver:
    """
    Odbiera i parsuje ramki RCMP z serwera.
    Wywołuje zarejestrowane handlery dla każdego typu wiadomości.
    """

    def __init__(self, connection: RCMPConnection):
        self.conn = connection
        # {msg_type: callback}
        self._handlers: dict[str, Callable] = {}
        self._running = False

    # ------------------------------------------------------------------
    # Rejestracja handlerów
    # ------------------------------------------------------------------

    def on(self, msg_type: str, handler: Callable):
        """Rejestruje callback dla danego typu wiadomości."""
        self._handlers[msg_type] = handler

    # ------------------------------------------------------------------
    # Pętla odbioru
    # ------------------------------------------------------------------

    async def start(self):
        """Startuje pętlę odbioru ramek."""
        self._running = True
        buffer = b""

        while self._running and self.conn.is_connected():
            try:
                chunk = await asyncio.wait_for(
                    self.conn.reader.read(4096),
                    timeout=Config.TIMEOUT_PONG + 5
                )
            except asyncio.TimeoutError:
                # Brak danych — możliwe zerwanie połączenia
                self.conn.connected = False
                break
            except (ConnectionResetError, OSError):
                self.conn.connected = False
                break

            if not chunk:
                self.conn.connected = False
                break

            buffer += chunk

            # Zabezpieczenie przed przepełnieniem bufora
            if len(buffer) > Config.MAX_MESSAGE_SIZE:
                print("[RECEIVER] Bufor przekroczył 64KB — rozłączenie")
                self.conn.connected = False
                break

            # Przetwarzaj kompletne ramki
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line:
                    await self._process(line)

    def stop(self):
        self._running = False

    # ------------------------------------------------------------------
    # Parsowanie i dispatch
    # ------------------------------------------------------------------

    async def _process(self, raw: bytes):
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            print("[RECEIVER] Błąd parsowania JSON")
            return

        msg_type = data.get("type")
        if not msg_type:
            return

        handler = self._handlers.get(msg_type)
        if handler:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                print(f"[RECEIVER] Błąd handlera {msg_type}: {e}")
        else:
            print(f"[RECEIVER] Brak handlera dla: {msg_type}")