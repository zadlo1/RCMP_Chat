import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Serwer
    HOST = os.getenv("SERVER_HOST", "127.0.0.1")
    PORT = int(os.getenv("SERVER_PORT", 9999))
    TLS_CERT_PATH = os.getenv("TLS_CERT_PATH", "certs/server.crt")
    TLS_KEY_PATH = os.getenv("TLS_KEY_PATH", "certs/server.key")

    # Baza danych
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = int(os.getenv("DB_PORT", 5433))
    DB_NAME = os.getenv("DB_NAME", "rcmp_chat")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")

    @classmethod
    def db_dsn(cls) -> str:
        return (
            f"postgresql://{cls.DB_USER}:{cls.DB_PASSWORD}"
            f"@{cls.DB_HOST}:{cls.DB_PORT}/{cls.DB_NAME}"
        )

    # JWT
    JWT_SECRET = os.getenv("JWT_SECRET", "zmien_mnie_w_env")
    JWT_TTL_SECONDS = int(os.getenv("JWT_TTL_SECONDS", 3600))

    # Timeouty (sekundy)
    TIMEOUT_LOGIN = int(os.getenv("TIMEOUT_LOGIN", 10))
    TIMEOUT_SESSION = int(os.getenv("TIMEOUT_SESSION", 90))
    TIMEOUT_PONG = int(os.getenv("TIMEOUT_PONG", 10))
    PING_INTERVAL = int(os.getenv("PING_INTERVAL", 30))
    TIMEOUT_MESSAGE_ACK = 5
    MESSAGE_RETRANSMIT_MAX = 3

    # Limity
    MAX_MESSAGE_SIZE = 64 * 1024
    MAX_MESSAGES_PER_WINDOW = 30
    RATE_WINDOW_SECONDS = 10
    MAX_LOGIN_ATTEMPTS = 5
    MAX_CONNECTIONS_PER_IP = 10
    MAX_ROOMS_PER_USER = 50
    MAX_NAME_LENGTH = 64
    MSG_ID_CACHE_TTL = 300