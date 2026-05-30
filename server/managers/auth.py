import time
import secrets
import bcrypt
import jwt
import asyncpg

from server.config import Config
from shared.error_codes import ErrorCode


class AuthManager:

    def __init__(self, db_pool: asyncpg.Pool):
        self.db = db_pool
        # Cache użytych nonce: {nonce: timestamp}
        self._used_nonces: dict[str, float] = {}
        # Cache prób logowania: {ip: [timestamp, ...]}
        self._login_attempts: dict[str, list[float]] = {}

    # ------------------------------------------------------------------
    # Nonce
    # ------------------------------------------------------------------

    def _cleanup_nonces(self):
        """Usuwa przeterminowane nonce z cache (starsze niż 300 s)."""
        now = time.time()
        expired = [n for n, t in self._used_nonces.items() if now - t > Config.MSG_ID_CACHE_TTL]
        for n in expired:
            del self._used_nonces[n]

    def check_and_store_nonce(self, nonce: str) -> bool:
        """
        Sprawdza czy nonce był już użyty.
        Zwraca True jeśli nonce jest świeży (można go użyć), False jeśli duplikat.
        """
        self._cleanup_nonces()
        if nonce in self._used_nonces:
            return False
        self._used_nonces[nonce] = time.time()
        return True

    # ------------------------------------------------------------------
    # Rate limiting logowania
    # ------------------------------------------------------------------

    def check_login_rate_limit(self, ip: str) -> bool:
        """
        Sprawdza czy IP nie przekroczyło limitu prób logowania.
        Zwraca True jeśli można kontynuować, False jeśli limit przekroczony.
        """
        now = time.time()
        attempts = self._login_attempts.get(ip, [])
        # Zostawiamy tylko próby z ostatnich 60 s
        attempts = [t for t in attempts if now - t < 60]
        self._login_attempts[ip] = attempts

        if len(attempts) >= Config.MAX_LOGIN_ATTEMPTS:
            return False

        attempts.append(now)
        self._login_attempts[ip] = attempts
        return True

    # ------------------------------------------------------------------
    # Weryfikacja hasła
    # ------------------------------------------------------------------

    async def verify_user(self, username: str, password: str) -> dict | None:
        """
        Weryfikuje dane logowania użytkownika.
        Zwraca rekord użytkownika jeśli dane poprawne, None jeśli błędne.
        """
        row = await self.db.fetchrow(
            "SELECT id, username, password, role, is_blocked FROM users WHERE username = $1",
            username
        )
        if row is None:
            return None
        if row["is_blocked"]:
            return None
        if not bcrypt.checkpw(password.encode("utf-8"), row["password"].encode("utf-8")):
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # JWT
    # ------------------------------------------------------------------

    def generate_token(self, user_id: int, username: str, role: str) -> str:
        """Generuje token JWT dla zalogowanego użytkownika."""
        payload = {
            "sub": user_id,
            "username": username,
            "role": role,
            "iat": int(time.time()),
            "exp": int(time.time()) + Config.JWT_TTL_SECONDS,
        }
        return jwt.encode(payload, Config.JWT_SECRET, algorithm="HS256")

    def verify_token(self, token: str) -> dict | None:
        """
        Weryfikuje token JWT.
        Zwraca payload jeśli token ważny, None jeśli wygasły lub niepoprawny.
        """
        try:
            payload = jwt.decode(token, Config.JWT_SECRET, algorithms=["HS256"])
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    def generate_hmac_secret(self) -> str:
        """Generuje losowy sekret HMAC dla sesji użytkownika."""
        return secrets.token_hex(32)

    # ------------------------------------------------------------------
    # Rejestracja użytkownika (seed / admin)
    # ------------------------------------------------------------------

    @staticmethod
    def hash_password(password: str) -> str:
        """Hashuje hasło bcrypt — używane przy tworzeniu kont."""
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    # ------------------------------------------------------------------
    # Logowanie zdarzeń bezpieczeństwa
    # ------------------------------------------------------------------

    async def log_event(self, event: str, username: str = None, ip: str = None, detail: str = None):
        """Zapisuje zdarzenie bezpieczeństwa do tabeli system_logs."""
        await self.db.execute(
            """
            INSERT INTO system_logs (event, username, ip, detail)
            VALUES ($1, $2, $3, $4)
            """,
            event, username, ip, detail
        )