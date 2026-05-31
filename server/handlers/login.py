import uuid
import time

from server.session import Session, SessionManager
from server.managers.auth import AuthManager
from server.managers.message_router import MessageRouter
from server.managers.rate_limiter import RateLimiter
from shared.error_codes import ErrorCode


async def handle_login(
    data: dict,
    session: Session,
    session_manager: SessionManager,
    auth: AuthManager,
    router: MessageRouter,
    rate_limiter: RateLimiter,
):
    writer = session.writer
    ip = session.ip

    # Zmiana stanu
    session.state = "AUTHENTICATING"

    # Rate limit per IP
    if not rate_limiter.check_login_rate(ip):
        await router.send_error(writer, ErrorCode.LOGIN_RATE_LIMIT,
                                ErrorCode.get_message(ErrorCode.LOGIN_RATE_LIMIT), data.get("msg_id"))
        await auth.log_event("LOGIN_RATE_LIMIT", ip=ip)
        session.state = "CLOSING"
        return

    payload = data.get("payload") or {}
    username = payload.get("username", "").strip()
    password = payload.get("password", "")
    nonce = payload.get("nonce", "")

    # Walidacja pól
    if not username or not password or not nonce:
        await router.send_error(writer, ErrorCode.MALFORMED_ENVELOPE,
                                "Missing username, password or nonce", data.get("msg_id"))
        session.state = "CLOSING"
        return

    # Sprawdzenie nonce (ochrona przed replay)
    if not auth.check_and_store_nonce(nonce):
        await router.send_error(writer, ErrorCode.UNAUTHORIZED,
                                "Nonce already used", data.get("msg_id"))
        await auth.log_event("REPLAY_ATTEMPT", username=username, ip=ip)
        session.state = "CLOSING"
        return

    # Weryfikacja danych użytkownika
    user = await auth.verify_user(username, password)
    if user is None:
        await _send_login_err(writer, router, data.get("msg_id"))
        await auth.log_event("LOGIN_FAILED", username=username, ip=ip)
        session.state = "CLOSING"
        return

    # Generowanie tokenu i sekretu HMAC
    token = auth.generate_token(user["id"], user["username"], user["role"])
    hmac_secret = auth.generate_hmac_secret()

    # Wypełnienie sesji
    session.user_id = user["id"]
    session.username = user["username"]
    session.role = user["role"]
    session.token = token
    session.hmac_secret = hmac_secret
    session_manager.activate_session(session)

    # Aktualizacja statusu w bazie
    await auth.db.execute(
        "UPDATE users SET status = 'online', last_seen = NOW() WHERE id = $1",
        user["id"]
    )

    # Odpowiedź LOGIN_OK
    response = {
        "type": "LOGIN_OK",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": token,
        "payload": {
            "session_token": token,
            "hmac_secret": hmac_secret,
            "expires_in": 3600,
            "username": user["username"],
            "role": user["role"],
        }
    }
    await router._write(writer, response)
    await auth.log_event("LOGIN_OK", username=username, ip=ip)


async def _send_login_err(writer, router: MessageRouter, ref_msg_id: str = None):
    import uuid, time
    frame = {
        "type": "LOGIN_ERR",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": None,
        "payload": {
            "code": ErrorCode.LOGIN_FAILED,
            "message": ErrorCode.get_message(ErrorCode.LOGIN_FAILED),
            "ref_msg_id": ref_msg_id,
        }
    }
    await router._write(writer, frame)