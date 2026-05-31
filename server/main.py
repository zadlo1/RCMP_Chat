import asyncio
import json
import ssl
import uuid
import time
import asyncpg

from server.config import Config
from server.session import Session, SessionManager
from server.managers.auth import AuthManager
from server.managers.message_router import MessageRouter
from server.managers.room_manager import RoomManager
from server.managers.rate_limiter import RateLimiter
from server.handlers.login import handle_login
from server.handlers.keepalive import handle_ping, handle_pong
from server.handlers.bye import handle_bye
from server.handlers.messaging import handle_send_message, handle_message_ack
from server.handlers.rooms import handle_join_room, handle_leave_room
from shared.message_types import MessageType
from shared.schemas import validate_envelope
from shared.error_codes import ErrorCode


class RCMPServer:

    def __init__(self):
        self.db_pool: asyncpg.Pool = None
        self.session_manager = SessionManager()
        self.router = MessageRouter()
        self.room_manager: RoomManager = None
        self.rate_limiter = RateLimiter()
        self.auth: AuthManager = None

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    async def start(self):
        self.db_pool = await asyncpg.create_pool(
            host=Config.DB_HOST,
            port=Config.DB_PORT,
            database=Config.DB_NAME,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            min_size=2,
            max_size=10,
        )
        self.auth = AuthManager(self.db_pool)
        self.room_manager = RoomManager(self.db_pool)

        ssl_ctx = self._build_ssl_context()

        server = await asyncio.start_server(
            self._handle_client,
            Config.HOST,
            Config.PORT,
            ssl=ssl_ctx,
        )

        # Zadanie timeoutów sesji
        asyncio.create_task(self._session_timeout_loop())

        addr = server.sockets[0].getsockname()
        print(f"[RCMP] Serwer nasłuchuje na {addr[0]}:{addr[1]} (TLS)")

        async with server:
            await server.serve_forever()

    def _build_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(Config.TLS_CERT_PATH, Config.TLS_KEY_PATH)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        return ctx

    # ------------------------------------------------------------------
    # Obsługa klienta
    # ------------------------------------------------------------------

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        ip = writer.get_extra_info("peername")[0]

        # Sprawdź limit połączeń per IP
        if not self.rate_limiter.register_connection(ip):
            writer.close()
            return

        session = self.session_manager.create_session(writer, ip)
        print(f"[RCMP] Nowe połączenie: {ip}")

        # Timeout logowania
        try:
            await asyncio.wait_for(
                self._client_loop(reader, writer, session),
                timeout=None
            )
        except Exception as e:
            print(f"[RCMP] Błąd klienta {ip}: {e}")
        finally:
            await self._cleanup(session, ip)

    async def _client_loop(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, session: Session):
        buffer = b""

        # Timeout na login
        login_deadline = time.time() + Config.TIMEOUT_LOGIN

        while True:
            # Czytamy dane do \n
            try:
                timeout = max(0.1, login_deadline - time.time()) if session.state == "CONNECTED" else None
                chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
            except asyncio.TimeoutError:
                if session.state in ("CONNECTED", "AUTHENTICATING"):
                    print(f"[RCMP] Timeout logowania: {session.ip}")
                    break
                chunk = b""

            if not chunk:
                break

            buffer += chunk

            # Sprawdź rozmiar bufora
            if len(buffer) > Config.MAX_MESSAGE_SIZE:
                await self.router.send_error(writer, ErrorCode.MESSAGE_TOO_LARGE,
                                             ErrorCode.get_message(ErrorCode.MESSAGE_TOO_LARGE))
                break

            # Przetwarzaj kompletne ramki (zakończone \n)
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line:
                    should_close = await self._process_frame(line, session, writer)
                    if should_close:
                        return

    async def _process_frame(self, raw: bytes, session: Session, writer) -> bool:
        """
        Przetwarza pojedynczą ramkę JSON.
        Zwraca True jeśli połączenie powinno zostać zamknięte.
        """
        # Parsowanie JSON
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            errors = session.register_format_error()
            await self.router.send_error(writer, ErrorCode.MALFORMED_ENVELOPE,
                                         ErrorCode.get_message(ErrorCode.MALFORMED_ENVELOPE))
            if errors >= 3:
                print(f"[RCMP] Zbyt wiele błędów formatu: {session.ip}")
                return True
            return False

        # Walidacja koperty
        valid, error_code = validate_envelope(data)
        if not valid:
            errors = session.register_format_error()
            await self.router.send_error(writer, error_code,
                                         ErrorCode.get_message(error_code), data.get("msg_id"))
            if errors >= 3:
                return True
            return False

        msg_type = data["type"]

        # Autoryzacja — wszystkie poza LOGIN wymagają tokenu
        if msg_type not in MessageType.NO_AUTH_REQUIRED:
            if not session.is_authenticated():
                errors = session.register_auth_error()
                await self.router.send_error(writer, ErrorCode.UNAUTHORIZED,
                                             ErrorCode.get_message(ErrorCode.UNAUTHORIZED))
                if errors >= 3:
                    return True
                return False

            # Walidacja tokenu JWT
            token = data.get("token")
            payload = self.auth.verify_token(token)
            if payload is None:
                errors = session.register_auth_error()
                await self.router.send_error(writer, ErrorCode.UNAUTHORIZED,
                                             "Invalid or expired token")
                if errors >= 3:
                    return True
                return False

        # Dispatch do odpowiedniego handlera
        await self._dispatch(msg_type, data, session, writer)

        # Jeśli handler zamknął sesję
        if session.state in ("CLOSING", "CLOSED"):
            return True

        return False

    async def _dispatch(self, msg_type: str, data: dict, session: Session, writer):
        if msg_type == MessageType.LOGIN:
            await handle_login(
                data, session, self.session_manager,
                self.auth, self.router, self.rate_limiter
            )
            if session.state == "ACTIVE":
                self.router.register(session.user_id, writer)

        elif msg_type == MessageType.JOIN_ROOM:
            await handle_join_room(data, session, self.router, self.room_manager)

        elif msg_type == MessageType.LEAVE_ROOM:
            await handle_leave_room(data, session, self.router, self.room_manager)

        elif msg_type == MessageType.SEND_MESSAGE:
            await handle_send_message(
                data, session, self.router,
                self.room_manager, self.rate_limiter, self.db_pool
            )

        elif msg_type == MessageType.MESSAGE_ACK:
            await handle_message_ack(data, session)

        elif msg_type == MessageType.PING:
            await handle_ping(data, session, self.router)

        elif msg_type == MessageType.PONG:
            await handle_pong(data, session)

        elif msg_type == MessageType.BYE:
            await handle_bye(
                data, session, self.session_manager,
                self.router, self.room_manager
            )
            await self.db_pool.execute(
                "UPDATE users SET status = 'offline', last_seen = NOW() WHERE id = $1",
                session.user_id
            )

        else:
            await self.router.send_error(
                session.writer, ErrorCode.UNKNOWN_TYPE,
                ErrorCode.get_message(ErrorCode.UNKNOWN_TYPE)
            )

    # ------------------------------------------------------------------
    # Timeout sesji
    # ------------------------------------------------------------------

    async def _session_timeout_loop(self):
        """Co 10 s sprawdza sesje które przekroczyły timeout aktywności."""
        while True:
            await asyncio.sleep(10)
            timed_out = self.session_manager.get_timed_out(Config.TIMEOUT_SESSION)
            for session in timed_out:
                print(f"[RCMP] Timeout sesji: {session.username} ({session.ip})")
                await self.router.send_error(
                    session.writer, ErrorCode.UNAUTHORIZED, "Session expired"
                )
                await self._cleanup(session, session.ip)
                try:
                    session.writer.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def _cleanup(self, session: Session, ip: str):
        if session.user_id:
            self.router.unregister(session.user_id)
            self.room_manager.leave_all_rooms(session.user_id)
            try:
                await self.db_pool.execute(
                    "UPDATE users SET status = 'offline', last_seen = NOW() WHERE id = $1",
                    session.user_id
                )
            except Exception:
                pass
        self.session_manager.remove_session(session)
        self.rate_limiter.unregister_connection(ip)
        self.rate_limiter.cleanup()


# ------------------------------------------------------------------
# Punkt wejścia
# ------------------------------------------------------------------

async def main():
    server = RCMPServer()
    await server.start()


if __name__ == "__main__":
    asyncio.run(main())