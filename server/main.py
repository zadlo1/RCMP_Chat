import asyncio
import json
import logging
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
from server.handlers.register import handle_register
from server.handlers.keepalive import handle_ping, handle_pong
from server.handlers.bye import handle_bye
from server.handlers.messaging import handle_send_message, handle_message_ack, deliver_pending_direct_messages
from server.handlers.rooms import (
    handle_join_room, handle_leave_room,
    handle_room_members, handle_room_kick, handle_room_ban, handle_room_unban,
)
from server.handlers.invite import (
    handle_room_invite_accept, handle_room_invite_decline, send_invite,
    deliver_pending_room_invites,
)
from server.handlers.admin import (
    handle_create_room, handle_delete_room,
    handle_admin_users_request, handle_delete_user, handle_set_user_role,
)
from server.handlers.friends import (
    handle_friend_request, handle_friend_request_accept,
    handle_friend_request_decline, send_friends_list_on_login,
    notify_friends_status, handle_friend_remove
)
from shared.message_types import MessageType
from shared.schemas import validate_envelope
from shared.error_codes import ErrorCode

logger = logging.getLogger("rcmp.server")


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

        # Automatyczna migracja — utwórz brakujące tabele jeśli nie istnieją
        await self._run_migrations()

        # Reset statusów — przy starcie serwera wszyscy są offline
        await self.db_pool.execute("UPDATE users SET status = 'offline'")
        logger.info("Statusy użytkowników zresetowane do offline")

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
        logger.info("Serwer nasłuchuje na %s:%s (TLS)", addr[0], addr[1])

        async with server:
            await server.serve_forever()

    async def _run_migrations(self):
        """Tworzy brakujące tabele jeśli nie istnieją (bezpieczna migracja)."""
        migrations = [
            (
                "room_bans",
                """
                CREATE TABLE IF NOT EXISTS room_bans (
                    room_id     INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    banned_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    banned_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (room_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_room_bans_room ON room_bans(room_id);
                """
            ),
            (
                "room_kicks",
                """
                CREATE TABLE IF NOT EXISTS room_kicks (
                    room_id     INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    kicked_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    kicked_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (room_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_room_kicks_room ON room_kicks(room_id);
                """
            ),
            (
                "friendships",
                """
                CREATE TABLE IF NOT EXISTS friendships (
                    id          SERIAL PRIMARY KEY,
                    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    friend_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    status      VARCHAR(16) NOT NULL DEFAULT 'pending',
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (user_id, friend_id)
                );
                CREATE INDEX IF NOT EXISTS idx_friendships_user   ON friendships(user_id, status);
                CREATE INDEX IF NOT EXISTS idx_friendships_friend ON friendships(friend_id, status);
                """
            ),
        ]
        for table_name, sql in migrations:
            exists = await self.db_pool.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=$1)",
                table_name
            )
            if not exists:
                await self.db_pool.execute(sql)
                logger.info("Migracja: utworzono tabelę '%s'", table_name)
            else:
                # Upewnij się że indeksy też istnieją (idempotentne)
                await self.db_pool.execute(sql)

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
        logger.info("Nowe połączenie: %s", ip)

        # Timeout logowania
        try:
            await asyncio.wait_for(
                self._client_loop(reader, writer, session),
                timeout=None
            )
        except Exception as e:
            logger.error("Błąd klienta %s: %s", ip, e, exc_info=True)
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
                    logger.warning("Timeout logowania: %s", session.ip)
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
                logger.warning("Zbyt wiele błędów formatu: %s", session.ip)
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
        try:
            await self._dispatch(msg_type, data, session, writer)
        except Exception as e:
            logger.error("Błąd handlera '%s' dla %s: %s", msg_type, session.ip, e, exc_info=True)
            try:
                await self.router.send_error(
                    writer, ErrorCode.SERVER_ERROR,
                    "Błąd wewnętrzny serwera", data.get("msg_id")
                )
            except Exception:
                pass

        # Jeśli handler zamknął sesję
        if session.state in ("CLOSING", "CLOSED"):
            return True

        return False

    async def _dispatch(self, msg_type: str, data: dict, session: Session, writer):
        if msg_type == MessageType.REGISTER:
            await handle_register(
                data, session, self.auth, self.router,
                self.rate_limiter, self.db_pool
            )

        elif msg_type == MessageType.LOGIN:
            await handle_login(
                data, session, self.session_manager,
                self.auth, self.router, self.rate_limiter
            )
            if session.state == "ACTIVE":
                self.router.register(session.user_id, writer)
                # Wyślij listę dostępnych pokojów i znajomych
                asyncio.create_task(self._send_rooms_list(session))
                asyncio.create_task(send_friends_list_on_login(session, self.router, self.db_pool))
                asyncio.create_task(notify_friends_status(
                    session.user_id, session.username, "online", self.router, self.db_pool
                ))
                # Zaległe DM i zaproszenia do pokojów z czasu gdy użytkownik był offline
                asyncio.create_task(deliver_pending_direct_messages(session, self.router, self.db_pool))
                asyncio.create_task(deliver_pending_room_invites(session, self.router, self.db_pool))

        elif msg_type == MessageType.JOIN_ROOM:
            await handle_join_room(data, session, self.router, self.room_manager)

        elif msg_type == MessageType.LEAVE_ROOM:
            await handle_leave_room(data, session, self.router, self.room_manager, self.push_rooms_list)

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

        elif msg_type == MessageType.FRIEND_REQUEST:
            await handle_friend_request(data, session, self.router, self.db_pool)

        elif msg_type == MessageType.FRIEND_REQUEST_ACCEPT:
            await handle_friend_request_accept(data, session, self.router, self.db_pool)

        elif msg_type == MessageType.FRIEND_REQUEST_DECLINE:
            await handle_friend_request_decline(data, session, self.router, self.db_pool)

        elif msg_type == MessageType.FRIEND_REMOVE:
            await handle_friend_remove(data, session, self.router, self.db_pool)

        elif msg_type == MessageType.CREATE_ROOM:
            await handle_create_room(data, session, self.router, self.db_pool)

        elif msg_type == MessageType.DELETE_ROOM:
            await handle_delete_room(data, session, self.router, self.db_pool, self.room_manager)

        elif msg_type == MessageType.ROOM_MEMBERS_REQUEST:
            await handle_room_members(data, session, self.router, self.room_manager, self.session_manager, self.db_pool)

        elif msg_type == MessageType.ROOM_KICK:
            await handle_room_kick(data, session, self.router, self.room_manager, self.session_manager, self.push_rooms_list)

        elif msg_type == MessageType.ROOM_BAN:
            await handle_room_ban(data, session, self.router, self.room_manager, self.session_manager, self.push_rooms_list)

        elif msg_type == MessageType.ROOM_UNBAN:
            await handle_room_unban(data, session, self.router, self.room_manager, self.session_manager, self.push_rooms_list)

        elif msg_type == MessageType.ADMIN_USERS_REQUEST:
            await handle_admin_users_request(data, session, self.router, self.db_pool)

        elif msg_type == MessageType.DELETE_USER:
            await handle_delete_user(
                data, session, self.router, self.db_pool,
                self.session_manager, self.room_manager
            )

        elif msg_type == MessageType.SET_USER_ROLE:
            await handle_set_user_role(
                data, session, self.router, self.db_pool, self.session_manager
            )

        elif msg_type == MessageType.DIRECT_MESSAGE:
            await handle_send_message(data, session, self.router,
                                      self.room_manager, self.rate_limiter, self.db_pool)

        elif msg_type == MessageType.ROOM_INVITE_ACCEPT:
            await handle_room_invite_accept(data, session, self.router, self.db_pool, self.room_manager, self.push_rooms_list)

        elif msg_type == MessageType.ROOM_INVITE_DECLINE:
            await handle_room_invite_decline(data, session, self.router)

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
    # Lista pokojów
    # ------------------------------------------------------------------

    async def _send_rooms_list(self, session):
        """Wysyła listę dostępnych pokojów po zalogowaniu."""
        rooms = await self.room_manager.get_rooms_list(session.user_id, session.role)

        frame = {
            "type": "ROOMS_LIST",
            "msg_id": str(uuid.uuid4()),
            "ts": int(time.time() * 1000),
            "token": session.token,
            "payload": {"rooms": rooms}
        }
        await self.router._write(session.writer, frame)

    async def push_rooms_list(self, user_id: int):
        """
        Wysyła zaktualizowaną listę pokojów do konkretnego użytkownika
        (np. po banie/odbanowaniu/wyrzuceniu/zaproszeniu), jeśli jest online.
        """
        target_session = self.session_manager.get_by_user_id(user_id)
        if target_session is None or not target_session.is_authenticated():
            return

        rooms = await self.room_manager.get_rooms_list(user_id, target_session.role)
        frame = {
            "type": "ROOMS_LIST",
            "msg_id": str(uuid.uuid4()),
            "ts": int(time.time() * 1000),
            "token": target_session.token,
            "payload": {"rooms": rooms}
        }
        await self.router._write(target_session.writer, frame)

    # ------------------------------------------------------------------
    # Timeout sesji
    # ------------------------------------------------------------------

    async def _session_timeout_loop(self):
        """Co 10 s sprawdza sesje które przekroczyły timeout aktywności."""
        while True:
            await asyncio.sleep(10)
            timed_out = self.session_manager.get_timed_out(Config.TIMEOUT_SESSION)
            for session in timed_out:
                logger.warning("Timeout sesji: %s (%s)", session.username, session.ip)
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
                await notify_friends_status(
                    session.user_id, session.username, "offline", self.router, self.db_pool
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    server = RCMPServer()
    await server.start()


if __name__ == "__main__":
    asyncio.run(main())
