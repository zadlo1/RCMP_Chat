"""
Testy jednostkowe dla RateLimiter.
"""
import time
import pytest
from unittest.mock import patch

from server.managers.rate_limiter import RateLimiter
from server.config import Config


class TestMessageRateLimit:
    def setup_method(self):
        self.rl = RateLimiter()

    def test_first_message_allowed(self):
        assert self.rl.check_message_rate(user_id=1) is True

    def test_within_limit_allowed(self):
        for _ in range(Config.MAX_MESSAGES_PER_WINDOW):
            assert self.rl.check_message_rate(user_id=1) is True

    def test_exceeding_limit_blocked(self):
        for _ in range(Config.MAX_MESSAGES_PER_WINDOW):
            self.rl.check_message_rate(user_id=1)
        assert self.rl.check_message_rate(user_id=1) is False

    def test_different_users_independent(self):
        for _ in range(Config.MAX_MESSAGES_PER_WINDOW):
            self.rl.check_message_rate(user_id=1)
        # user_id=2 nie powinien być blokowany
        assert self.rl.check_message_rate(user_id=2) is True

    def test_window_expires(self):
        for _ in range(Config.MAX_MESSAGES_PER_WINDOW):
            self.rl.check_message_rate(user_id=1)

        # Symuluj minięcie okna czasowego
        future = time.time() + Config.RATE_WINDOW_SECONDS + 1
        with patch("server.managers.rate_limiter.time.time", return_value=future):
            rl2 = RateLimiter()
            # Odtwórz wpisy z przeszłości
            past = future - Config.RATE_WINDOW_SECONDS - 1
            rl2._message_windows[1] = [past] * Config.MAX_MESSAGES_PER_WINDOW
            assert rl2.check_message_rate(user_id=1) is True


class TestLoginRateLimit:
    def setup_method(self):
        self.rl = RateLimiter()

    def test_first_attempt_allowed(self):
        assert self.rl.check_login_rate("1.2.3.4") is True

    def test_within_limit_allowed(self):
        for _ in range(Config.MAX_LOGIN_ATTEMPTS):
            assert self.rl.check_login_rate("1.2.3.4") is True

    def test_exceeding_limit_blocked(self):
        for _ in range(Config.MAX_LOGIN_ATTEMPTS):
            self.rl.check_login_rate("1.2.3.4")
        assert self.rl.check_login_rate("1.2.3.4") is False

    def test_different_ips_independent(self):
        for _ in range(Config.MAX_LOGIN_ATTEMPTS):
            self.rl.check_login_rate("1.2.3.4")
        assert self.rl.check_login_rate("9.9.9.9") is True

    def test_window_expires(self):
        for _ in range(Config.MAX_LOGIN_ATTEMPTS):
            self.rl.check_login_rate("1.2.3.4")

        future = time.time() + 61
        with patch("server.managers.rate_limiter.time.time", return_value=future):
            rl2 = RateLimiter()
            rl2._login_windows["1.2.3.4"] = [future - 62] * Config.MAX_LOGIN_ATTEMPTS
            assert rl2.check_login_rate("1.2.3.4") is True


class TestConnectionLimit:
    def setup_method(self):
        self.rl = RateLimiter()

    def test_first_connection_allowed(self):
        assert self.rl.register_connection("1.2.3.4") is True

    def test_connections_up_to_limit_allowed(self):
        for _ in range(Config.MAX_CONNECTIONS_PER_IP):
            assert self.rl.register_connection("1.2.3.4") is True

    def test_exceeding_connection_limit_blocked(self):
        for _ in range(Config.MAX_CONNECTIONS_PER_IP):
            self.rl.register_connection("1.2.3.4")
        assert self.rl.register_connection("1.2.3.4") is False

    def test_unregister_frees_slot(self):
        for _ in range(Config.MAX_CONNECTIONS_PER_IP):
            self.rl.register_connection("1.2.3.4")
        assert self.rl.register_connection("1.2.3.4") is False
        self.rl.unregister_connection("1.2.3.4")
        assert self.rl.register_connection("1.2.3.4") is True

    def test_get_connection_count(self):
        self.rl.register_connection("1.2.3.4")
        self.rl.register_connection("1.2.3.4")
        assert self.rl.get_connection_count("1.2.3.4") == 2

    def test_unregister_not_below_zero(self):
        self.rl.unregister_connection("5.5.5.5")  # nigdy nie rejestrowano
        assert self.rl.get_connection_count("5.5.5.5") == 0

    def test_different_ips_independent(self):
        for _ in range(Config.MAX_CONNECTIONS_PER_IP):
            self.rl.register_connection("1.2.3.4")
        assert self.rl.register_connection("5.5.5.5") is True


class TestCleanup:
    def test_cleanup_removes_expired_message_windows(self):
        rl = RateLimiter()
        expired_time = time.time() - Config.RATE_WINDOW_SECONDS - 1
        rl._message_windows[42] = [expired_time, expired_time]
        rl.cleanup()
        assert rl._message_windows.get(42) == [] or 42 not in rl._message_windows

    def test_cleanup_keeps_fresh_entries(self):
        rl = RateLimiter()
        rl._message_windows[42] = [time.time()]
        rl.cleanup()
        assert len(rl._message_windows.get(42, [])) == 1
