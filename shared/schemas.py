import time
from shared.message_types import MessageType
from shared.error_codes import ErrorCode

MAX_MESSAGE_SIZE = 64 * 1024   # 64 KB
TIMESTAMP_SKEW_LIMIT = 300     # sekundy


def validate_envelope(data: dict) -> tuple[bool, int | None]:
    """
    Waliduje kopertę wiadomości RCMP.
    Zwraca (True, None) jeśli poprawna, lub (False, kod_błędu) jeśli błąd.
    """
    # Wymagane pola koperty
    for field in ("type", "msg_id", "ts"):
        if field not in data:
            return False, ErrorCode.MALFORMED_ENVELOPE

    # Nieznany typ
    if data["type"] not in MessageType.ALL:
        return False, ErrorCode.UNKNOWN_TYPE

    # Sprawdzenie timestamp
    now_ms = int(time.time() * 1000)
    ts = data["ts"]
    if not isinstance(ts, int) or abs(now_ms - ts) > TIMESTAMP_SKEW_LIMIT * 1000:
        return False, ErrorCode.TIMESTAMP_SKEW

    # Token wymagany dla wszystkich poza LOGIN
    if data["type"] not in MessageType.NO_AUTH_REQUIRED:
        if not data.get("token"):
            return False, ErrorCode.UNAUTHORIZED

    return True, None