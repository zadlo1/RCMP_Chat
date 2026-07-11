import asyncio
import json
from server.config import Config


class MessageRouter:

    def __init__(self):
        # {user_id: asyncio.StreamWriter}
        self._writers: dict[int, asyncio.StreamWriter] = {}

    # ------------------------------------------------------------------
    # Rejestracja połączeń
    # ------------------------------------------------------------------

    def register(self, user_id: int, writer: asyncio.StreamWriter):
        """Rejestruje writer klienta po zalogowaniu."""
        self._writers[user_id] = writer

    def unregister(self, user_id: int):
        """Usuwa writer klienta po rozłączeniu."""
        self._writers.pop(user_id, None)

    def is_online(self, user_id: int) -> bool:
        return user_id in self._writers

    # ------------------------------------------------------------------
    # Wysyłanie wiadomości
    # ------------------------------------------------------------------

    async def send_to_user(self, user_id: int, message: dict) -> bool:
        """
        Wysyła wiadomość do konkretnego użytkownika.
        Zwraca True jeśli wysłano, False jeśli użytkownik offline.
        """
        writer = self._writers.get(user_id)
        if writer is None:
            return False
        await self._write(writer, message)
        return True

    async def send_to_room(self, room_members: set[int], message: dict, exclude_user_id: int = None):
        """
        Rozsyła wiadomość do wszystkich użytkowników w pokoju.
        exclude_user_id — opcjonalnie pomija nadawcę.
        """
        tasks = []
        for user_id in room_members:
            if user_id == exclude_user_id:
                continue
            writer = self._writers.get(user_id)
            if writer is not None:
                tasks.append(self._write(writer, message))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def send_error(self, writer: asyncio.StreamWriter, code: int, message: str, ref_msg_id: str = None):
        """Wysyła ramkę ERROR bezpośrednio przez writer (przed zalogowaniem też działa)."""

        # Debug logging dla błędu FORBIDDEN
        if code == 4032:
            import logging
            import traceback
            logger = logging.getLogger("rcmp.server")
            logger.warning("Wysyłanie FORBIDDEN (4032, msg_id=%s): %s", ref_msg_id, message)
            # Loguj stack trace aby zobaczyć skąd pochodzi błąd
            for line in traceback.format_stack()[:-1]:
                if 'site-packages' not in line and 'asyncio' not in line:
                    logger.debug(line.strip())

        frame = {
            "type": "ERROR",
            "msg_id": _new_msg_id(),
            "ts": _now_ms(),
            "token": None,
            "payload": {
                "code": code,
                "message": message,
                "ref_msg_id": ref_msg_id,
            }
        }
        await self._write(writer, frame)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _write(writer: asyncio.StreamWriter, message: dict):
        """Serializuje wiadomość do JSON i wysyła z separatorem \\n."""
        try:
            data = json.dumps(message, ensure_ascii=False) + "\n"
            writer.write(data.encode("utf-8"))
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass


def _new_msg_id() -> str:
    import uuid
    return str(uuid.uuid4())


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)