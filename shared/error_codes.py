class ErrorCode:
    # Błędy formatu
    MALFORMED_ENVELOPE = 4001
    UNKNOWN_TYPE = 4002
    TIMESTAMP_SKEW = 4003
    MESSAGE_TOO_LARGE = 4004
    INVALID_HMAC = 4005

    # Błędy logowania
    LOGIN_FAILED = 4011
    LOGIN_RATE_LIMIT = 4012

    # Błędy autoryzacji
    UNAUTHORIZED = 4031
    FORBIDDEN = 4032

    # Błędy zasobów
    ROOM_NOT_FOUND = 4041
    USER_NOT_FOUND = 4042
    ROOM_BANNED = 4043

    # Rate limiting
    SEND_RATE_LIMIT = 4291

    # Błędy serwera
    USERNAME_TAKEN = 4091
    USERNAME_INVALID = 4092
    SERVER_ERROR = 5001
    SERVER_OVERLOAD = 5002

    MESSAGES = {
        4001: "Malformed envelope",
        4002: "Unknown message type",
        4003: "Timestamp skew too large",
        4004: "Message too large",
        4005: "Invalid HMAC",
        4011: "Login failed",
        4012: "Login rate limit exceeded",
        4031: "Unauthorized",
        4032: "Forbidden",
        4041: "Room not found",
        4042: "User not found",
        4043: "Banned from this room",
        4291: "Send rate limit exceeded",
        4091: "Username already taken",
        4092: "Invalid username format",
        5001: "Internal server error",
        5002: "Server overload",
    }

    @classmethod
    def get_message(cls, code: int) -> str:
        return cls.MESSAGES.get(code, "Unknown error")