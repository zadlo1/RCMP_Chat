import time
from server.config import Config


class RateLimiter:

    def __init__(self):
        # {user_id: [timestamp, ...]} — okno dla SEND_MESSAGE
        self._message_windows: dict[int, list[float]] = {}
        # {ip: [timestamp, ...]} — okno dla LOGIN
        self._login_windows: dict[str, list[float]] = {}
        # {ip: int} — liczba otwartych połączeń per IP
        self._connections: dict[str, int] = {}

    # ------------------------------------------------------------------
    # SEND_MESSAGE — max 30 wiadomości / 10 s / użytkownik
    # ------------------------------------------------------------------

    def check_message_rate(self, user_id: int) -> bool:
        """
        Sprawdza czy użytkownik nie przekroczył limitu wiadomości.
        Zwraca True jeśli można wysłać, False jeśli limit przekroczony.
        """
        now = time.time()
        window = self._message_windows.get(user_id, [])
        window = [t for t in window if now - t < Config.RATE_WINDOW_SECONDS]

        if len(window) >= Config.MAX_MESSAGES_PER_WINDOW:
            self._message_windows[user_id] = window
            return False

        window.append(now)
        self._message_windows[user_id] = window
        return True

    # ------------------------------------------------------------------
    # LOGIN — max 5 prób / 60 s / IP
    # ------------------------------------------------------------------

    def check_login_rate(self, ip: str) -> bool:
        """
        Sprawdza czy IP nie przekroczyło limitu prób logowania.
        Zwraca True jeśli można kontynuować, False jeśli limit przekroczony.
        """
        now = time.time()
        window = self._login_windows.get(ip, [])
        window = [t for t in window if now - t < 60]

        if len(window) >= Config.MAX_LOGIN_ATTEMPTS:
            self._login_windows[ip] = window
            return False

        window.append(now)
        self._login_windows[ip] = window
        return True

    # ------------------------------------------------------------------
    # Połączenia — max 10 / IP
    # ------------------------------------------------------------------

    def register_connection(self, ip: str) -> bool:
        """
        Rejestruje nowe połączenie z IP.
        Zwraca True jeśli połączenie dozwolone, False jeśli limit przekroczony.
        """
        count = self._connections.get(ip, 0)
        if count >= Config.MAX_CONNECTIONS_PER_IP:
            return False
        self._connections[ip] = count + 1
        return True

    def unregister_connection(self, ip: str):
        """Usuwa połączenie z IP po rozłączeniu klienta."""
        count = self._connections.get(ip, 0)
        if count > 0:
            self._connections[ip] = count - 1

    def get_connection_count(self, ip: str) -> int:
        return self._connections.get(ip, 0)

    # ------------------------------------------------------------------
    # Czyszczenie
    # ------------------------------------------------------------------

    def cleanup(self):
        """Usuwa przeterminowane wpisy z okien czasowych."""
        now = time.time()
        for user_id in list(self._message_windows):
            self._message_windows[user_id] = [
                t for t in self._message_windows[user_id]
                if now - t < Config.RATE_WINDOW_SECONDS
            ]
        for ip in list(self._login_windows):
            self._login_windows[ip] = [
                t for t in self._login_windows[ip]
                if now - t < 60
            ]