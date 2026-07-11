import asyncio
import json
import logging
import uuid
import time
import hmac
import hashlib

from client.protocol.connection import RCMPConnection
from server.config import Config

logger = logging.getLogger("rcmp.client.sender")


class RCMPSender:
    """
    Odpowiada za wysyłanie ramek RCMP do serwera.
    Obsługuje retransmisję SEND_MESSAGE i obliczanie HMAC.
    """

    def __init__(self, connection: RCMPConnection):
        self.conn = connection
        self.token: str = None
        self.hmac_secret: str = None
        self._seq_id: int = 0

        # Oczekujące ACK: {msg_id: (ramka, liczba_prób, czas_wysłania)}
        self._pending_acks: dict[str, tuple] = {}

    # ------------------------------------------------------------------
    # Wysyłanie ramek
    # ------------------------------------------------------------------

    async def send(self, msg_type: str, payload: dict = None) -> str:
        """
        Buduje i wysyła ramkę RCMP.
        Zwraca msg_id wysłanej wiadomości.
        """
        msg_id = str(uuid.uuid4())
        frame = {
            "type": msg_type,
            "msg_id": msg_id,
            "ts": int(time.time() * 1000),
            "token": self.token,
            "payload": payload or {},
        }
        await self._write(frame)
        return msg_id

    async def send_login(self, username: str, password: str) -> str:
        nonce = uuid.uuid4().hex[:8]
        return await self.send("LOGIN", {
            "username": username,
            "password": password,
            "nonce": nonce,
        })

    async def send_join_room(self, room_id: int) -> str:
        return await self.send("JOIN_ROOM", {"room_id": room_id})

    async def send_leave_room(self, room_id: int) -> str:
        return await self.send("LEAVE_ROOM", {"room_id": room_id})

    async def send_room_members_request(self, room_id: int) -> str:
        return await self.send("ROOM_MEMBERS_REQUEST", {"room_id": room_id})

    async def send_room_history_request(self, room_id: int, limit: int = 100) -> str:
        """Żąda trwałej historii wiadomości pokoju zapisanej w bazie."""
        return await self.send("HISTORY_REQUEST", {
            "history_type": "room", "room_id": room_id, "limit": limit,
        })

    async def send_dm_history_request(self, username: str, limit: int = 100) -> str:
        """Żąda trwałej historii wiadomości prywatnych (DM) z danym użytkownikiem."""
        return await self.send("HISTORY_REQUEST", {
            "history_type": "dm", "username": username, "limit": limit,
        })

    async def send_room_kick(self, room_id: int, user_id: int) -> str:
        return await self.send("ROOM_KICK", {"room_id": room_id, "user_id": user_id})

    async def send_room_ban(self, room_id: int, user_id: int) -> str:
        return await self.send("ROOM_BAN", {"room_id": room_id, "user_id": user_id})

    async def send_room_unban(self, room_id: int, user_id: int) -> str:
        return await self.send("ROOM_UNBAN", {"room_id": room_id, "user_id": user_id})

    # ------------------------------------------------------------------
    # Panel administratora — zarządzanie użytkownikami
    # ------------------------------------------------------------------

    async def send_admin_users_request(self) -> str:
        return await self.send("ADMIN_USERS_REQUEST")

    async def send_delete_user(self, user_id: int) -> str:
        return await self.send("DELETE_USER", {"user_id": user_id})

    async def send_set_user_role(self, user_id: int, role: str) -> str:
        return await self.send("SET_USER_ROLE", {"user_id": user_id, "role": role})

    async def send_message(self, target_type: str, target_id: int, body: str) -> str:
        """
        Wysyła SEND_MESSAGE z obliczonym HMAC.
        Rejestruje wiadomość w kolejce oczekujących na ACK.
        """
        msg_id = str(uuid.uuid4())
        self._seq_id += 1
        seq_id = self._seq_id
        ts = int(time.time() * 1000)

        hmac_val = self._compute_hmac(msg_id, ts, seq_id, body)

        frame = {
            "type": "SEND_MESSAGE",
            "msg_id": msg_id,
            "ts": ts,
            "token": self.token,
            "payload": {
                "target_type": target_type,
                "target_id": target_id,
                "seq_id": seq_id,
                "body": body,
                "hmac": hmac_val,
            }
        }
        await self._write(frame)
        # Rejestruj do retransmisji
        self._pending_acks[msg_id] = (frame, 1, time.time())
        return msg_id

    async def send_room_invite(self, room_id: int, room_name: str, invite_to: str) -> str:
        """Wysyła zaproszenie do pokoju z prawidłowym HMAC."""
        msg_id = str(uuid.uuid4())
        ts = int(time.time() * 1000)
        seq_id = 0
        body = f"Zaproszenie do #{room_name}"
        hmac_val = self._compute_hmac(msg_id, ts, seq_id, body)

        frame = {
            "type": "SEND_MESSAGE",
            "msg_id": msg_id,
            "ts": ts,
            "token": self.token,
            "payload": {
                "target_type": "invite",
                "target_id": room_id,
                "room_name": room_name,
                "invite_to": invite_to,
                "seq_id": seq_id,
                "body": body,
                "hmac": hmac_val,
            }
        }
        await self._write(frame)
        return msg_id

    async def send_ping(self) -> str:
        return await self.send("PING")

    async def send_bye(self) -> str:
        return await self.send("BYE")

    async def send_message_ack(self, ack_msg_id: str) -> str:
        return await self.send("MESSAGE_ACK", {"ack_msg_id": ack_msg_id})

    # ------------------------------------------------------------------
    # Retransmisja
    # ------------------------------------------------------------------

    def confirm_ack(self, msg_id: str):
        """Usuwa wiadomość z kolejki oczekujących po otrzymaniu ACK."""
        self._pending_acks.pop(msg_id, None)

    async def check_retransmissions(self):
        """
        Sprawdza czy któreś wiadomości czekają za długo na ACK.
        Wywołuj cyklicznie (np. co sekundę).
        """
        now = time.time()
        to_remove = []

        for msg_id, (frame, attempts, sent_at) in self._pending_acks.items():
            if now - sent_at < Config.TIMEOUT_MESSAGE_ACK:
                continue

            if attempts >= Config.MESSAGE_RETRANSMIT_MAX:
                logger.warning("Brak ACK po %d próbach: %s...", attempts, msg_id[:8])
                to_remove.append(msg_id)
                continue

            # Retransmisja z tym samym msg_id, seq_id i ts (ts musi być niezmieniony — HMAC go obejmuje)
            logger.info("Retransmisja (%d): %s...", attempts + 1, msg_id[:8])
            await self._write(frame)
            self._pending_acks[msg_id] = (frame, attempts + 1, time.time())

        for msg_id in to_remove:
            del self._pending_acks[msg_id]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _write(self, frame: dict):
        """Serializuje ramkę i wysyła przez połączenie."""
        if not self.conn.is_connected():
            return
        try:
            data = json.dumps(frame, ensure_ascii=False) + "\n"
            self.conn.writer.write(data.encode("utf-8"))
            await self.conn.writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            logger.warning("Błąd zapisu do gniazda: %s", e)
            self.conn.connected = False

    def _compute_hmac(self, msg_id: str, ts: int, seq_id: int, body: str) -> str:
        if not self.hmac_secret:
            return ""
        data = f"{msg_id}{ts}{seq_id}{body}".encode("utf-8")
        key = self.hmac_secret.encode("utf-8")
        return hmac.new(key, data, hashlib.sha256).hexdigest()
