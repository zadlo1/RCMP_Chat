# RCMP Chat — Secure Real-Time Messaging System

**Bartłomiej Żądło, nr albumu: 151505**  
Projekt nr 3 — Programowanie Usług Sieciowych

---

## Spis treści

1. [Opis projektu](#1-opis-projektu)
2. [Protokół RCMP](#2-protokół-rcmp)
3. [Architektura aplikacji](#3-architektura-aplikacji)
4. [Struktura projektu](#4-struktura-projektu)
5. [Wymagania](#5-wymagania)
6. [Instalacja i uruchomienie](#6-instalacja-i-uruchomienie)
7. [Przypadki użycia](#7-przypadki-użycia)
8. [Bezpieczeństwo](#8-bezpieczeństwo)
9. [Obsługa błędów](#9-obsługa-błędów)
10. [Testy](#10-testy)

---

## 1. Opis projektu

RCMP Chat to aplikacja komunikatora czasu rzeczywistego zbudowana na autorskim protokole warstwy aplikacyjnej **RCMP (Real-Time Chat Messaging Protocol)**. System umożliwia bezpieczną wymianę wiadomości tekstowych pomiędzy użytkownikami w ramach pokoi tematycznych oraz w trybie wiadomości prywatnych 1:1.

### Rozwiązywane problemy

Typowe proste rozwiązania socketowe nie zapewniają mechanizmów niezbędnych w produkcyjnym komunikatorze. RCMP Chat implementuje:

- synchroniczną wymianę wiadomości tekstowych z niskim opóźnieniem,
- zarządzanie sesjami wielu równoczesnych użytkowników,
- śledzenie statusu obecności użytkowników (online / offline / away),
- potwierdzanie dostarczenia wiadomości,
- utrzymanie połączenia przy braku aktywności (keep-alive),
- dołączanie i opuszczanie pokojów w trakcie trwania sesji,
- odporność na utratę połączenia z mechanizmem reconnect,
- wykrywanie duplikatów wiadomości,
- ochronę przed replay attack,
- rate limiting i ochronę przed nadużyciami.

### Model działania

Model klient–serwer. Serwer centralny pośredniczy we wszystkich wiadomościach — klienci nie komunikują się bezpośrednio między sobą.

---

## 2. Protokół RCMP

### Założenia techniczne

| Parametr | Wartość |
|---|---|
| Transport | TCP |
| Szyfrowanie | TLS 1.3 |
| Kodowanie | Newline-delimited JSON (`\n`) |
| Dozwolone szyfry | TLS_AES_256_GCM_SHA384, TLS_CHACHA20_POLY1305_SHA256 |

Każda wiadomość to pojedyncza linia JSON zakończona znakiem `\n`. Wybór JSON uzasadniony jest czytelnością podczas debugowania i łatwością walidacji schematu.

### Struktura koperty (envelope)

Każda wiadomość zawiera wspólną kopertę:

```json
{
  "type": "<TYP_WIADOMOŚCI>",
  "msg_id": "<UUID v4>",
  "ts": 1715000000123,
  "token": "<session_token>",
  "payload": {}
}
```

| Pole | Wymagane | Opis |
|---|---|---|
| `type` | zawsze | typ wiadomości |
| `msg_id` | zawsze | unikalny UUID v4 wiadomości |
| `ts` | zawsze | Unix timestamp w milisekundach |
| `token` | poza LOGIN | token sesji JWT |
| `payload` | opcjonalne | dane właściwe wiadomości |

### Typy wiadomości

| Typ | Kierunek | Opis |
|---|---|---|
| `LOGIN` | C → S | uwierzytelnienie użytkownika |
| `LOGIN_OK` | S → C | potwierdzenie zalogowania, wydanie tokenu sesji |
| `LOGIN_ERR` | S → C | błąd logowania |
| `JOIN_ROOM` | C → S | dołączenie do pokoju |
| `LEAVE_ROOM` | C → S | opuszczenie pokoju |
| `ROOM_EVENT` | S → C | powiadomienie o zmianie w pokoju |
| `SEND_MESSAGE` | C → S | wysłanie wiadomości do pokoju lub użytkownika |
| `DELIVER_MESSAGE` | S → C | dostarczenie wiadomości do odbiorcy |
| `MESSAGE_ACK` | C → S | potwierdzenie odebrania wiadomości |
| `STATUS` | C → S | zmiana statusu (online/away) |
| `PING` | C ↔ S | keep-alive |
| `PONG` | S ↔ C | odpowiedź na PING |
| `ERROR` | S → C | błąd protokołu lub aplikacyjny |
| `BYE` | C → S | zamknięcie sesji przez klienta |
| `BYE_ACK` | S → C | potwierdzenie zamknięcia sesji |

### Stany sesji

**Po stronie serwera:**

```
CONNECTED → AUTHENTICATING → ACTIVE → CLOSING → CLOSED
```

- połączenie TCP otwiera stan `CONNECTED`,
- odebranie `LOGIN` przechodzi do `AUTHENTICATING`,
- pomyślna weryfikacja przechodzi do `ACTIVE`,
- `LOGIN_ERR`, `BYE` lub błąd krytyczny przechodzi do `CLOSING`,
- zamknięcie TCP kończy stan `CLOSED`.

**Po stronie klienta:**

```
DISCONNECTED → CONNECTED → LOGGED_IN → IN_ROOM → DISCONNECTED
```

- `BYE_ACK`, timeout lub błąd TCP przenosi z powrotem do `DISCONNECTED`.

### Timeouty i keep-alive

| Parametr | Wartość |
|---|---|
| Timeout logowania (TCP → LOGIN_OK) | 10 s |
| Interwał PING (klient → serwer) | 30 s |
| Timeout PONG (brak odpowiedzi) | 10 s |
| Timeout sesji (brak aktywności) | 90 s |
| Timeout oczekiwania na MESSAGE_ACK | 5 s |
| Liczba prób retransmisji SEND_MESSAGE | 3 |

### Retransmisja wiadomości

Jeśli klient nie otrzyma `MESSAGE_ACK` w ciągu 5 s, ponawia `SEND_MESSAGE` z tym samym `msg_id` i `seq_id` (maksymalnie 3 razy). Serwer wykrywa duplikat po `msg_id` i odsyła `MESSAGE_ACK` bez ponownego dostarczenia.

### Limity i ochrona przed nadużyciami

| Limit | Wartość |
|---|---|
| Maksymalny rozmiar wiadomości | 64 KB |
| Maksymalnie wiadomości / 10 s / użytkownik | 30 |
| Maksymalnie prób LOGIN / 60 s / IP | 5 |
| Maksymalnie otwartych połączeń / IP | 10 |
| Maksymalnie pokojów na użytkownika | 50 |
| Maksymalna długość nazwy użytkownika / pokoju | 64 znaki |

---

## 3. Architektura aplikacji

### Komponenty systemu

```
┌─────────────────┐
│   Client App    │
│   GUI / CLI     │
│   RCMP Client   │
└────────┬────────┘
         │ TLS/TCP
┌────────▼────────┐
│   RCMP Server   │
│ Session Manager │
│  Room Manager   │
│ Message Router  │
│ Auth Validator  │
│  Rate Limiter   │
└────────┬────────┘
         │
┌────────▼────────┐
│    Database     │
│     Users       │
│     Rooms       │
│    Messages     │
│      ACL        │
└─────────────────┘
```

### Klient RCMP

Odpowiada za logowanie, utrzymywanie sesji, wysyłanie i odbieranie wiadomości, reconnect po utracie połączenia, obsługę PING/PONG oraz retransmisję wiadomości. Zaimplementowany jako aplikacja desktopowa z GUI (CustomTkinter).

> **Uwaga:** Wcześniejsze etapy projektu (Etap 1 i Etap 2) wskazywały klienta CLI jako docelową formę MVP. W trakcie realizacji podjąłem decyzję o zastąpieniu CLI prostym interfejsem graficznym opartym na bibliotece CustomTkinter. Funkcjonalność protokołu pozostaje identyczna — zmiana dotyczy wyłącznie warstwy prezentacji.

### Serwer RCMP

Centralny komponent systemu obsługujący połączenia TCP/TLS, walidację komunikatów, autoryzację użytkowników, routing wiadomości, zarządzanie pokojami, retransmisję i ACK, wykrywanie timeoutów oraz ochronę przed nadużyciami.

### Baza danych (PostgreSQL)

Przechowuje użytkowników, hashe haseł bcrypt, pokoje, listy ACL pokojów prywatnych, historię wiadomości oraz logi systemowe.

### Przepływ danych

1. Klient nawiązuje połączenie TCP/TLS z serwerem.
2. Klient wysyła komunikat `LOGIN`.
3. Serwer weryfikuje dane użytkownika względem bazy.
4. Serwer odsyła `LOGIN_OK` wraz z tokenem JWT i `hmac_secret`.
5. Klient może dołączać do pokojów oraz wysyłać wiadomości.
6. Serwer przekazuje wiadomości odbiorcom przez `DELIVER_MESSAGE`.
7. Odbiorcy potwierdzają odbiór przez `MESSAGE_ACK`.
8. Po zakończeniu sesji klient wysyła `BYE`, serwer odpowiada `BYE_ACK`.

---

## 4. Struktura projektu

```
rcmp_chat/
│
├── server/                        # serwer RCMP
│   ├── main.py                    # punkt wejścia serwera
│   ├── config.py                  # konfiguracja (port, timeouty, limity)
│   ├── session.py                 # zarządzanie sesjami (SessionManager)
│   ├── handlers/                  # obsługa poszczególnych typów wiadomości
│   │   ├── login.py               # LOGIN → LOGIN_OK / LOGIN_ERR
│   │   ├── messaging.py           # SEND_MESSAGE → DELIVER_MESSAGE → MESSAGE_ACK
│   │   ├── rooms.py               # JOIN_ROOM, LEAVE_ROOM → ROOM_EVENT
│   │   ├── keepalive.py           # PING / PONG
│   │   └── bye.py                 # BYE → BYE_ACK
│   └── managers/                  # logika biznesowa
│       ├── auth.py                # JWT, bcrypt, walidacja tokenów
│       ├── room_manager.py        # zarządzanie pokojami i ACL
│       ├── rate_limiter.py        # rate limiting
│       └── message_router.py     # routing wiadomości do odbiorców
│
├── client/                        # klient RCMP
│   ├── main.py                    # punkt wejścia klienta
│   ├── protocol/                  # implementacja protokołu po stronie klienta
│   │   ├── connection.py          # TCP/TLS connect, reconnect, backoff
│   │   ├── sender.py              # wysyłanie ramek, retransmisja, HMAC
│   │   └── receiver.py            # odbieranie ramek, buforowanie do \n
│   └── gui/                       # interfejs użytkownika (CustomTkinter)
│       ├── app.py                 # główne okno aplikacji
│       ├── login_window.py        # okno logowania
│       ├── chat_window.py         # główny widok czatu
│       └── widgets.py             # reużywalne komponenty
│
├── shared/                        # kod współdzielony
│   ├── message_types.py           # stałe typów wiadomości
│   ├── error_codes.py             # kody błędów
│   ├── schemas.py                 # walidacja struktury ramek JSON
│   └── crypto.py                  # HMAC-SHA256
│
├── tests/
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
│   ├── init_db.sql                # schemat bazy danych
│   └── seed_db.py                 # testowe dane
│
├── certs/                         # certyfikaty TLS (nie commitować)
├── .env                           # zmienne środowiskowe (nie commitować)
├── .env.example                   # wzór pliku .env
├── requirements.txt
└── README.md
```

---

## 5. Wymagania

### Wymagania systemowe

- Python 3.12+
- PostgreSQL 15+
- OpenSSL (do generowania certyfikatów TLS)

### Zależności Python

```
asyncpg==0.29.0
PyJWT==2.8.0
bcrypt==4.1.3
python-dotenv==1.0.1
customtkinter==5.2.2
cryptography==42.0.5
pytest==8.2.0
pytest-asyncio==0.23.6
```

### Wymagania niefunkcjonalne

| Wymaganie | Wartość |
|---|---|
| Minimalna liczba równoczesnych klientów | 100 |
| Maksymalne opóźnienie dostarczenia wiadomości (LAN) | 200 ms |
| Szyfrowanie | TLS 1.3 |
| Integralność wiadomości | HMAC-SHA256 |
| Uwierzytelnienie | JWT (HS256, TTL 3600 s) |
| Hasła | bcrypt |

### Logowanie i diagnostyka

System loguje następujące zdarzenia do tabeli `system_logs` w bazie danych:

- błędy protokołu (niepoprawna koperta, nieznany typ wiadomości),
- próby logowania (zarówno udane, jak i nieudane),
- reconnect użytkowników,
- błędy weryfikacji HMAC,
- przekroczenia limitu wiadomości (rate limit),
- błędy wewnętrzne serwera.

---

## 6. Instalacja i uruchomienie

### Klonowanie i instalacja zależności

```bash
git clone <repo>
cd rcmp_chat
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac
pip install -r requirements.txt
```

### Konfiguracja

```bash
cp .env.example .env
# Uzupełnij .env swoimi danymi (baza, JWT secret)
```

### Baza danych

```bash
psql -U postgres -p 5433 -c "CREATE DATABASE rcmp_chat;"
psql -U postgres -p 5433 -d rcmp_chat -f scripts/init_db.sql
```

### Certyfikaty TLS

```bash
bash scripts/generate_certs.sh
```

### Uruchomienie serwera

```bash
python -m server.main
```

### Uruchomienie klienta

```bash
python -m client.main
```

---

## 7. Przypadki użycia

### UC1 — Logowanie użytkownika

Klient wysyła `LOGIN` z `username`, `password` i jednorazowym `nonce`. Serwer weryfikuje dane względem bcrypt hash w bazie, sprawdza nonce pod kątem replay attack i w przypadku sukcesu odsyła `LOGIN_OK` z tokenem JWT oraz `hmac_secret`. Przy błędnych danych — `LOGIN_ERR 4011`. Po przekroczeniu 5 prób w ciągu 60 s — `LOGIN_ERR 4012`.

### UC2 — Dołączenie do pokoju

Klient wysyła `JOIN_ROOM` z `room_id`. Serwer sprawdza czy pokój istnieje i czy użytkownik ma uprawnienia (lista ACL dla pokojów prywatnych). Przy sukcesie rozsyła `ROOM_EVENT` do pozostałych użytkowników pokoju.

### UC3 — Wysłanie wiadomości do pokoju

Klient generuje `SEND_MESSAGE` z `target_type=room`, `target_id`, `seq_id`, `body` i obliczonym HMAC-SHA256. Serwer weryfikuje HMAC, zapisuje wiadomość w bazie i rozsyła `DELIVER_MESSAGE` do wszystkich użytkowników pokoju. Odbiorcy odsyłają `MESSAGE_ACK`.

### UC4 — Wiadomość prywatna 1:1

Klient wysyła `SEND_MESSAGE` z `target_type=user` i `target_id` odbiorcy. Serwer odnajduje aktywną sesję odbiorcy i przesyła `DELIVER_MESSAGE` bezpośrednio do niego.

### UC5 — Utrata połączenia i reconnect

Klient wykrywa brak `PONG` po 10 s od wysłania `PING` i uznaje połączenie za zerwane. Inicjuje reconnect z wykładniczym backoff: 1 s, 2 s, 4 s, 8 s, maksymalnie 60 s. Po ponownym połączeniu wykonuje nowy `LOGIN`. Jeśli token JWT jest jeszcze ważny, serwer może odtworzyć stan pokojów.

### UC6 — Próba wejścia do prywatnego pokoju bez uprawnień

Klient wysyła `JOIN_ROOM` dla pokoju prywatnego. Serwer sprawdza listę ACL — jeśli użytkownik nie figuruje na liście, odsyła `ERROR 4032 FORBIDDEN`. Sesja pozostaje aktywna.

### UC7 — Zarządzanie pokojami przez administratora

Administrator z rolą `ADMIN` w tokenie JWT może tworzyć (`CREATE_ROOM`), modyfikować (`UPDATE_ROOM`) i usuwać (`DELETE_ROOM`) pokoje. Serwer propaguje zmiany do aktywnych klientów przez `ROOM_EVENT`.

### UC8 — Zarządzanie użytkownikami przez administratora

Administrator może blokować (`BLOCK_USER`) i odblokowywać (`UNBLOCK_USER`) konta użytkowników oraz wymuszać rozłączenie (`FORCE_DISCONNECT`). Zablokowany użytkownik nie może ponownie się zalogować.

### Planowane rozszerzenia protokołu

W ramach dalszego rozwoju przewidziane są dwa dodatkowe typy wiadomości:

- `HISTORY_REQUEST` — żądanie pobrania historii wiadomości pokoju (klient → serwer),
- `HISTORY_RESPONSE` — odpowiedź serwera zawierająca historię wiadomości.

Rozszerzenie umożliwi odtworzenie historii po reconnect, synchronizację stanu klienta oraz pobieranie starszych wiadomości.

---

## 8. Bezpieczeństwo

### Poufność

Całe połączenie szyfrowane przez TLS 1.3. Dozwolone szyfry: `TLS_AES_256_GCM_SHA384`, `TLS_CHACHA20_POLY1305_SHA256`. Certyfikat serwera weryfikowany przez klienta.

### Integralność wiadomości

Każda wiadomość `SEND_MESSAGE` zawiera pole `hmac` — HMAC-SHA256 obliczony z konkatenacji `msg_id + ts + seq_id + body`. Klucz (`hmac_secret`) wymieniany przy logowaniu w `LOGIN_OK`, chroniony przez TLS.

### Uwierzytelnienie

Klient wysyła `username + password + nonce`. Hasło nigdy nie jest eksponowane w sieci (chronione przez TLS). Serwer weryfikuje względem `bcrypt(password)` i wydaje token JWT (HS256, TTL 3600 s).

### Ochrona przed replay

- pole `nonce` w `LOGIN` — jednorazowe, serwer przechowuje użyte nonce przez 300 s,
- pole `ts` — serwer odrzuca wiadomości z timestampem oddalonym o więcej niż ±300 s,
- pole `msg_id` — serwer przechowuje odebrane `msg_id` przez 5 minut; duplikat jest odrzucany.

### Model zagrożeń

| Zagrożenie | Mechanizm ochrony |
|---|---|
| Podsłuch komunikacji | TLS 1.3 |
| Podszywanie się pod użytkownika | JWT + HMAC, hasło chronione przez TLS |
| Replay attack | nonce + timestamp + msg_id cache |
| Fałszowanie treści wiadomości | HMAC-SHA256 na body |
| Brute-force logowania | rate limit: max 5 prób / 60 s / IP |
| Flooding serwera | max rozmiar 64 KB, rate limit SEND_MESSAGE |
| Przejęcie sesji | JWT z TTL, unieważnienie po BYE |

---

## 9. Obsługa błędów

### Kody błędów

| Kod | Nazwa | Znaczenie |
|---|---|---|
| 4001 | MALFORMED_ENVELOPE | brak wymaganego pola w kopercie |
| 4002 | UNKNOWN_TYPE | nieznany typ wiadomości |
| 4003 | TIMESTAMP_SKEW | timestamp poza oknem ±300 s |
| 4004 | MESSAGE_TOO_LARGE | wiadomość przekracza 64 KB |
| 4005 | INVALID_HMAC | błąd weryfikacji HMAC |
| 4011 | LOGIN_FAILED | błędne dane logowania |
| 4012 | LOGIN_RATE_LIMIT | przekroczono limit prób logowania |
| 4031 | UNAUTHORIZED | brak lub nieważny token sesji |
| 4032 | FORBIDDEN | brak uprawnień do zasobu |
| 4041 | ROOM_NOT_FOUND | pokój nie istnieje |
| 4042 | USER_NOT_FOUND | nieznany użytkownik |
| 4291 | SEND_RATE_LIMIT | przekroczono limit wiadomości |
| 5001 | SERVER_ERROR | wewnętrzny błąd serwera |
| 5002 | SERVER_OVERLOAD | serwer przeciążony |

### Zachowanie po błędach

- błąd formatu (4001–4004): serwer odsyła `ERROR` i zamyka połączenie TCP po 3 kolejnych błędach w ciągu 60 s,
- błąd HMAC (4005): wiadomość odrzucona, sesja utrzymana,
- błąd autoryzacji (4031): po 3 wystąpieniach w ciągu 60 s — rozłączenie,
- rate limit (4291): okno max 30 wiadomości / 10 s / użytkownik.

---

## 10. Testy

### Testy funkcjonalne

- poprawne logowanie i wylogowanie,
- wysyłanie i odbieranie wiadomości w pokoju,
- komunikacja prywatna 1:1,
- dołączanie i opuszczanie pokojów,
- reconnect po utracie połączenia.

### Testy błędów

- niepoprawny JSON,
- nieznany typ wiadomości,
- invalid HMAC,
- wygasły JWT,
- timeout PONG.

### Testy bezpieczeństwa

- brute-force logowania,
- replay attack z ponownym użyciem nonce,
- duplikaty wiadomości (ten sam msg_id),
- próby wejścia do prywatnego pokoju.

### Testy obciążeniowe

- wielu równoczesnych klientów (cel: 100+),
- wysoka liczba wiadomości w krótkim czasie,
- reconnect storm,
- spam wiadomościami (rate limiter).

### Uruchomienie testów

```bash
pytest tests/ -v
pytest tests/ -v --asyncio-mode=auto
```
---

## 11. Zaproszenia do pokojów prywatnych

### Mechanizm

System zaproszeń pozwala administratorowi zapraszać użytkowników do pokojów prywatnych bez ręcznej edycji bazy danych. Przepływ:

1. Admin otwiera listę pokojów (przycisk **+** w sidebarze).
2. Przy pokojach prywatnych widzi przycisk **✉** (tylko dla roli `admin`).
3. Admin wpisuje nazwę użytkownika i wysyła zaproszenie.
4. Użytkownik otrzymuje okno dialogowe z możliwością akceptacji lub odrzucenia.
5. Po akceptacji użytkownik zostaje dodany do `room_acl` i automatycznie dołącza do pokoju.

### Nowe typy wiadomości

| Typ | Kierunek | Opis |
|---|---|---|
| `ROOM_INVITE` | S → C | zaproszenie do prywatnego pokoju |
| `ROOM_INVITE_ACCEPT` | C → S | akceptacja zaproszenia |
| `ROOM_INVITE_DECLINE` | C → S | odrzucenie zaproszenia |

### Ograniczenia

- Zaproszenia może wysyłać tylko użytkownik z rolą `admin`.
- Zapraszany użytkownik musi być online (aktywna sesja).
- Po akceptacji wpis trafia do tabeli `room_acl` i jest trwały.

---

## 12. Szczegóły implementacji GUI

### Struktura okien

- **LoginWindow** — modal z polami username/hasło, blokuje główne okno do czasu zalogowania.
- **ChatWindow** — główny widok podzielony na sidebar (pokoje, użytkownicy) i obszar czatu.
- **BrowseRoomsDialog** — lista dostępnych pokojów z możliwością dołączenia; dla admina przyciski zaproszeń przy pokojach prywatnych.
- **InviteDialog** — powiadomienie o zaproszeniu z przyciskami Akceptuj/Odrzuć.
- **SendInviteDialog** — formularz wysyłania zaproszenia (wpisanie nazwy użytkownika).

### Historia wiadomości per pokój

Każdy pokój ma własną historię wiadomości przechowywaną w pamięci klienta. Przełączanie między pokojami odświeża widok czatu i pokazuje tylko wiadomości z aktualnego pokoju. Historia zawiera trzy rodzaje wpisów: wiadomości (bąbelki), wiadomości systemowe (zdarzenia pokoju) i wiadomości niewyslane (oznaczone czerwonym tłem).

### Wiadomości niewyslane

Jeśli użytkownik opuścił pokój po wysłaniu wiadomości, wiadomość wyświetlana jest z ciemnoczerwonym tłem i informacją `⚠ Nie wysłano — opuściłeś pokój`.

### Placeholder braku pokoju

Gdy użytkownik nie należy do żadnego pokoju, obszar czatu wyświetla duży napis z ikoną 💬 i instrukcją wyboru pokoju. Próba wysłania wiadomości bez aktywnego pokoju powoduje podświetlenie nagłówka na czerwono z komunikatem `⚠ Wybierz pokój!`.

### Asyncio + Tkinter

GUI działa w głównym wątku Tkintera, a protokół RCMP w osobnym wątku z własną pętlą asyncio. Komunikacja między wątkami odbywa się przez `asyncio.run_coroutine_threadsafe` (wątek GUI → asyncio) oraz `self.after(0, callback)` (asyncio → wątek GUI).

---

## 13. Konfiguracja PyCharm

W folderze `.idea/runConfigurations/` znajdują się gotowe konfiguracje uruchomienia:

| Konfiguracja | Opis |
|---|---|
| `RCMP Server` | uruchamia serwer (`python -m server.main`) |
| `RCMP Client` | uruchamia klienta (`python -m client.main`) |
| `RCMP Seed DB` | wypełnia bazę testowymi danymi |
| `RCMP Generate Certs` | generuje certyfikaty TLS |

Każda konfiguracja ma ustawiony `Working directory` na korzeń projektu, co zapewnia poprawne wczytanie `.env` i certyfikatów.

---

## 14. Znane ograniczenia

- Lista pokojów w kliencie jest hardcodowana (3 pokoje). Docelowo powinna być pobierana z serwera przez dedykowany typ wiadomości `ROOMS_LIST`.
- Historia wiadomości przechowywana jest tylko w pamięci klienta — po restarcie jest tracona. Docelowo można ją pobierać z bazy przez `HISTORY_REQUEST / HISTORY_RESPONSE`.
- Zapraszany użytkownik musi być online — zaproszenia dla offline użytkowników nie są kolejkowane.
- Certyfikaty TLS są self-signed — w produkcji należy użyć certyfikatu od zaufanego CA.