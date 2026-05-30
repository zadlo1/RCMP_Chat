import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class Session:
    """Reprezentuje aktywną sesję użytkownika po stronie serwera."""

    # Dane połączenia
    writer: asyncio.StreamWriter
    ip: str

    # Dane użytkownika (wypełniane po zalogowaniu)
    user_id: int = None
    username: str = None
    role: str = None
    token: str = None
    hmac_secret: str = None

    # Stan sesji
    state: str = "CONNECTED"   # CONNECTED | AUTHENTICATING | ACTIVE | CLOSING | CLOSED

    # Czas ostatniej aktywności
    last_activity: float = field(default_factory=time.time)

    # Licznik błędów formatu (max 3 w ciągu 60 s → rozłączenie)
    format_errors: list[float] = field(default_factory=list)

    # Licznik błędów autoryzacji (max 3 w ciągu 60 s → rozłączenie)
    auth_errors: list[float] = field(default_factory=list)

    # Cache msg_id odebranych wiadomości (wykrywanie duplikatów)
    # {msg_id: timestamp}
    received_msg_ids: dict[str, float] = field(default_factory=dict)

    def touch(self):
        """Aktualizuje czas ostatniej aktywności."""
        self.last_activity = time.time()

    def is_authenticated(self) -> bool:
        return self.state == "ACTIVE"

    def is_timed_out(self, timeout: float) -> bool:
        return time.time() - self.last_activity > timeout

    # ------------------------------------------------------------------
    # Wykrywanie duplikatów wiadomości
    # ------------------------------------------------------------------

    def is_duplicate_msg(self, msg_id: str) -> bool:
        """Sprawdza czy msg_id był już widziany (okno 5 minut)."""
        self._cleanup_msg_cache()
        return msg_id in self.received_msg_ids

    def store_msg_id(self, msg_id: str):
        """Zapisuje msg_id do cache."""
        self.received_msg_ids[msg_id] = time.time()

    def _cleanup_msg_cache(self):
        """Usuwa przeterminowane msg_id (starsze niż 300 s)."""
        now = time.time()
        expired = [mid for mid, ts in self.received_msg_ids.items() if now - ts > 300]
        for mid in expired:
            del self.received_msg_ids[mid]

    # ------------------------------------------------------------------
    # Liczniki błędów
    # ------------------------------------------------------------------

    def register_format_error(self) -> int:
        """
        Rejestruje błąd formatu.
        Zwraca liczbę błędów w ostatnich 60 s.
        """
        now = time.time()
        self.format_errors = [t for t in self.format_errors if now - t < 60]
        self.format_errors.append(now)
        return len(self.format_errors)

    def register_auth_error(self) -> int:
        """
        Rejestruje błąd autoryzacji.
        Zwraca liczbę błędów w ostatnich 60 s.
        """
        now = time.time()
        self.auth_errors = [t for t in self.auth_errors if now - t < 60]
        self.auth_errors.append(now)
        return len(self.auth_errors)


class SessionManager:
    """Zarządza wszystkimi aktywnymi sesjami serwera."""

    def __init__(self):
        # {user_id: Session}
        self._sessions: dict[int, Session] = {}
        # {writer: Session} — do wyszukiwania przed zalogowaniem
        self._pending: dict[asyncio.StreamWriter, Session] = {}

    def create_session(self, writer: asyncio.StreamWriter, ip: str) -> Session:
        """Tworzy nową sesję dla świeżego połączenia TCP."""
        session = Session(writer=writer, ip=ip)
        self._pending[writer] = session
        return session

    def activate_session(self, session: Session):
        """Przenosi sesję z pending do aktywnych po zalogowaniu."""
        self._pending.pop(session.writer, None)
        session.state = "ACTIVE"
        self._sessions[session.user_id] = session

    def get_by_user_id(self, user_id: int) -> Session | None:
        return self._sessions.get(user_id)

    def get_by_writer(self, writer: asyncio.StreamWriter) -> Session | None:
        session = self._sessions.get(writer)
        if session:
            return session
        return self._pending.get(writer)

    def remove_session(self, session: Session):
        """Usuwa sesję przy rozłączeniu klienta."""
        self._sessions.pop(session.user_id, None)
        self._pending.pop(session.writer, None)
        session.state = "CLOSED"

    def all_active(self) -> list[Session]:
        return list(self._sessions.values())

    def get_timed_out(self, timeout: float) -> list[Session]:
        """Zwraca sesje które przekroczyły timeout aktywności."""
        return [s for s in self._sessions.values() if s.is_timed_out(timeout)]