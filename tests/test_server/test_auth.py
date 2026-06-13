"""
Testy jednostkowe dla AuthManager (bez połączenia z bazą danych).
"""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from server.managers.auth import AuthManager
from server.config import Config


def make_auth():
    db_pool = AsyncMock()
    return AuthManager(db_pool), db_pool


class TestNonce:
    def test_fresh_nonce_accepted(self):
        auth, _ = make_auth()
        assert auth.check_and_store_nonce("abc123") is True

    def test_duplicate_nonce_rejected(self):
        auth, _ = make_auth()
        auth.check_and_store_nonce("abc123")
        assert auth.check_and_store_nonce("abc123") is False

    def test_different_nonces_accepted(self):
        auth, _ = make_auth()
        assert auth.check_and_store_nonce("nonce-1") is True
        assert auth.check_and_store_nonce("nonce-2") is True

    def test_expired_nonce_cleared_and_accepted_again(self):
        auth, _ = make_auth()
        old_time = time.time() - Config.MSG_ID_CACHE_TTL - 1
        auth._used_nonces["old-nonce"] = old_time
        # cleanup powinien usunąć stary nonce
        assert auth.check_and_store_nonce("old-nonce") is True


class TestLoginRateLimit:
    def test_first_attempt_allowed(self):
        auth, _ = make_auth()
        assert auth.check_login_rate_limit("1.2.3.4") is True

    def test_within_limit_allowed(self):
        auth, _ = make_auth()
        for _ in range(Config.MAX_LOGIN_ATTEMPTS):
            assert auth.check_login_rate_limit("1.2.3.4") is True

    def test_exceeding_limit_blocked(self):
        auth, _ = make_auth()
        for _ in range(Config.MAX_LOGIN_ATTEMPTS):
            auth.check_login_rate_limit("1.2.3.4")
        assert auth.check_login_rate_limit("1.2.3.4") is False

    def test_different_ips_independent(self):
        auth, _ = make_auth()
        for _ in range(Config.MAX_LOGIN_ATTEMPTS):
            auth.check_login_rate_limit("1.2.3.4")
        assert auth.check_login_rate_limit("9.9.9.9") is True

    def test_old_attempts_expire(self):
        auth, _ = make_auth()
        old_time = time.time() - 61
        auth._login_attempts["1.2.3.4"] = [old_time] * Config.MAX_LOGIN_ATTEMPTS
        assert auth.check_login_rate_limit("1.2.3.4") is True


class TestJWT:
    def test_generate_and_verify_token(self):
        auth, _ = make_auth()
        token = auth.generate_token(user_id=1, username="alice", role="user")
        payload = auth.verify_token(token)
        assert payload is not None
        assert payload["sub"] == 1
        assert payload["username"] == "alice"
        assert payload["role"] == "user"

    def test_expired_token_returns_none(self):
        auth, _ = make_auth()
        # Generuj token z datą wygaśnięcia w przeszłości
        import jwt
        payload = {
            "sub": 1,
            "username": "alice",
            "role": "user",
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
        }
        token = jwt.encode(payload, Config.JWT_SECRET, algorithm="HS256")
        assert auth.verify_token(token) is None

    def test_invalid_token_returns_none(self):
        auth, _ = make_auth()
        assert auth.verify_token("not.a.valid.token") is None

    def test_token_with_wrong_secret_returns_none(self):
        import jwt
        auth, _ = make_auth()
        payload = {"sub": 1, "username": "alice", "role": "user",
                   "iat": int(time.time()), "exp": int(time.time()) + 3600}
        token = jwt.encode(payload, "wrong-secret", algorithm="HS256")
        assert auth.verify_token(token) is None


class TestHashPassword:
    def test_hash_is_not_plaintext(self):
        hashed = AuthManager.hash_password("secret")
        assert hashed != "secret"

    def test_hash_verifiable_with_bcrypt(self):
        import bcrypt
        hashed = AuthManager.hash_password("secret")
        assert bcrypt.checkpw(b"secret", hashed.encode())

    def test_different_calls_produce_different_hashes(self):
        h1 = AuthManager.hash_password("secret")
        h2 = AuthManager.hash_password("secret")
        assert h1 != h2  # bcrypt używa losowego salt


class TestVerifyUser:
    @pytest.mark.asyncio
    async def test_valid_credentials_return_user(self):
        auth, db = make_auth()
        hashed = AuthManager.hash_password("correctpassword")
        db.fetchrow = AsyncMock(return_value={
            "id": 1, "username": "alice",
            "password": hashed, "role": "user", "is_blocked": False
        })
        result = await auth.verify_user("alice", "correctpassword")
        assert result is not None
        assert result["username"] == "alice"

    @pytest.mark.asyncio
    async def test_wrong_password_returns_none(self):
        auth, db = make_auth()
        hashed = AuthManager.hash_password("correctpassword")
        db.fetchrow = AsyncMock(return_value={
            "id": 1, "username": "alice",
            "password": hashed, "role": "user", "is_blocked": False
        })
        result = await auth.verify_user("alice", "wrongpassword")
        assert result is None

    @pytest.mark.asyncio
    async def test_nonexistent_user_returns_none(self):
        auth, db = make_auth()
        db.fetchrow = AsyncMock(return_value=None)
        result = await auth.verify_user("nobody", "pass")
        assert result is None

    @pytest.mark.asyncio
    async def test_blocked_user_returns_none(self):
        auth, db = make_auth()
        hashed = AuthManager.hash_password("pass")
        db.fetchrow = AsyncMock(return_value={
            "id": 2, "username": "blocked_user",
            "password": hashed, "role": "user", "is_blocked": True
        })
        result = await auth.verify_user("blocked_user", "pass")
        assert result is None


class TestHmacSecret:
    def test_generates_non_empty_string(self):
        auth, _ = make_auth()
        secret = auth.generate_hmac_secret()
        assert isinstance(secret, str)
        assert len(secret) > 0

    def test_generates_unique_secrets(self):
        auth, _ = make_auth()
        s1 = auth.generate_hmac_secret()
        s2 = auth.generate_hmac_secret()
        assert s1 != s2
