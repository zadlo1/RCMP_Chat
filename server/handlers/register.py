import uuid
import time
import re

import asyncpg

from server.session import Session
from server.managers.auth import AuthManager
from server.managers.message_router import MessageRouter
from server.managers.rate_limiter import RateLimiter
from server.config import Config
from shared.error_codes import ErrorCode

# Dozwolone znaki w nazwie użytkownika: litery, cyfry, podkreślenie, myślnik
_USERNAME_RE = re.compile(r'^[a-zA-Z0-9_\-]{3,64}$')


async def handle_register(
    data: dict,
    session: Session,
    auth: AuthManager,
    router: MessageRouter,
    rate_limiter: RateLimiter,
    db_pool: asyncpg.Pool,
):
    """
    Obsługuje REGISTER od klienta.
    Tworzy nowe konto użytkownika z rolą 'user' i automatycznie
    dodaje go do pokoju 'general'.
    """
    writer = session.writer
    ip = session.ip

    # Rate limit logowania obowiązuje też rejestrację
    if not rate_limiter.check_login_rate(ip):
        await router.send_error(writer, ErrorCode.LOGIN_RATE_LIMIT,
                                ErrorCode.get_message(ErrorCode.LOGIN_RATE_LIMIT),
                                data.get("msg_id"))
        session.state = "CLOSING"
        return

    payload = data.get("payload") or {}
    username = payload.get("username", "").strip()
    password = payload.get("password", "")

    # Walidacja pól
    if not username or not password:
        await _send_register_err(writer, router, "Missing username or password",
                                 data.get("msg_id"))
        session.state = "CLOSING"
        return

    # Walidacja formatu nazwy użytkownika
    if not _USERNAME_RE.match(username):
        await _send_register_err(
            writer, router,
            "Username must be 3-64 characters: letters, digits, _ or -",
            data.get("msg_id"),
            code=ErrorCode.USERNAME_INVALID,
        )
        session.state = "CLOSING"
        return

    # Minimalna długość hasła
    if len(password) < 6:
        await _send_register_err(writer, router,
                                 "Password must be at least 6 characters",
                                 data.get("msg_id"))
        session.state = "CLOSING"
        return

    # Sprawdź czy nazwa już zajęta
    existing = await db_pool.fetchrow(
        "SELECT id FROM users WHERE username = $1", username
    )
    if existing:
        await _send_register_err(
            writer, router,
            f"Username '{username}' is already taken",
            data.get("msg_id"),
            code=ErrorCode.USERNAME_TAKEN,
        )
        session.state = "CLOSING"
        return

    # Hashuj hasło i utwórz konto
    hashed = AuthManager.hash_password(password)
    row = await db_pool.fetchrow(
        """
        INSERT INTO users (username, password, role, status)
        VALUES ($1, $2, 'user', 'offline')
        RETURNING id, username, role
        """,
        username, hashed,
    )
    user_id = row["id"]

    # Dodaj do pokoju 'general' automatycznie (brak ACL — pokój publiczny,
    # ale logujemy że użytkownik jest jego „stałym" członkiem przez pierwszą wizytę)
    general = await db_pool.fetchrow(
        "SELECT id FROM rooms WHERE name = 'general' LIMIT 1"
    )
    if general:
        # Dla pokojów publicznych nie ma wpisów w room_acl — dostęp domyślny.
        # Możemy zapisać do system_logs żeby mieć ślad.
        await auth.log_event("REGISTER", username=username, ip=ip,
                             detail=f"auto-joined general (id={general['id']})")

    # Odpowiedź REGISTER_OK
    response = {
        "type": "REGISTER_OK",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": None,
        "payload": {
            "username": username,
            "message": "Account created. You can now log in.",
        }
    }
    await router._write(writer, response)
    session.state = "CLOSING"


async def _send_register_err(writer, router: MessageRouter, message: str,
                              ref_msg_id: str = None, code: int = None):
    if code is None:
        code = ErrorCode.MALFORMED_ENVELOPE
    frame = {
        "type": "REGISTER_ERR",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": None,
        "payload": {
            "code": code,
            "message": message,
            "ref_msg_id": ref_msg_id,
        }
    }
    await router._write(writer, frame)