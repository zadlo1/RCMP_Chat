rcmp_chat/
│
├── server/                        # serwer RCMP
│   ├── __init__.py
│   ├── main.py                    # punkt wejścia serwera
│   ├── config.py                  # konfiguracja (port, timeouty, limity)
│   ├── session.py                 # zarządzanie sesjami (SessionManager)
│   │
│   ├── handlers/                  # obsługa poszczególnych typów wiadomości
│   │   ├── __init__.py
│   │   ├── login.py               # LOGIN → LOGIN_OK / LOGIN_ERR
│   │   ├── messaging.py           # SEND_MESSAGE → DELIVER_MESSAGE → MESSAGE_ACK
│   │   ├── rooms.py               # JOIN_ROOM, LEAVE_ROOM → ROOM_EVENT
│   │   ├── keepalive.py           # PING / PONG
│   │   └── bye.py                 # BYE → BYE_ACK
│   │
│   └── managers/                  # logika biznesowa
│       ├── __init__.py
│       ├── auth.py                # JWT, bcrypt, walidacja tokenów
│       ├── room_manager.py        # zarządzanie pokojami i ACL
│       ├── rate_limiter.py        # rate limiting (wiadomości, logowania)
│       └── message_router.py     # routing wiadomości do odbiorców
│
├── client/                        # klient RCMP
│   ├── __init__.py
│   ├── main.py                    # punkt wejścia klienta
│   │
│   ├── protocol/                  # implementacja protokołu po stronie klienta
│   │   ├── __init__.py
│   │   ├── connection.py          # TCP/TLS connect, reconnect, backoff
│   │   ├── sender.py              # wysyłanie ramek, retransmisja, HMAC
│   │   └── receiver.py            # odbieranie ramek, buforowanie do \n
│   │
│   └── gui/                       # interfejs użytkownika
│       ├── __init__.py
│       ├── app.py                 # główne okno CustomTkinter
│       ├── login_window.py        # okno logowania
│       ├── chat_window.py         # główny widok czatu
│       └── widgets.py             # reużywalne komponenty (bąbelki, lista pokojów)
│
├── shared/                        # kod współdzielony przez serwer i klienta
│   ├── __init__.py
│   ├── message_types.py           # stałe typów wiadomości (LOGIN, BYE, ...)
│   ├── error_codes.py             # kody błędów (4001, 4002, ...)
│   ├── schemas.py                 # walidacja struktury ramek JSON
│   └── crypto.py                  # HMAC-SHA256, helpers kryptograficzne
│
├── tests/
│   ├── __init__.py
│   ├── test_server/
│   │   ├── test_auth.py
│   │   ├── test_messaging.py
│   │   └── test_rate_limiter.py
│   └── test_client/
│       ├── test_connection.py
│       └── test_sender.py
│
├── scripts/
│   ├── generate_certs.sh          # generowanie self-signed certyfikatów TLS
│   ├── init_db.sql                # schemat bazy danych PostgreSQL
│   └── seed_db.py                 # wypełnienie bazy testowymi danymi
│
├── certs/                         # certyfikaty TLS (gitignore!)
│   ├── server.crt
│   └── server.key
│
├── docs/
│   └── protocol_spec.md           # skrót specyfikacji protokołu
│
├── .env.example                   # przykładowy plik zmiennych środowiskowych
├── .gitignore
├── requirements.txt
└── README.md
