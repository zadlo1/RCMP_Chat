-- Schemat bazy danych RCMP Chat
-- Uruchomienie: psql -U rcmp_user -d rcmp_chat -f scripts/init_db.sql

-- Rozszerzenie do UUID
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- Użytkownicy
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    username    VARCHAR(64) UNIQUE NOT NULL,
    password    VARCHAR(255) NOT NULL,       -- bcrypt hash
    status      VARCHAR(16) NOT NULL DEFAULT 'offline',  -- online / away / offline
    role        VARCHAR(16) NOT NULL DEFAULT 'user',     -- user / admin
    is_blocked  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen   TIMESTAMPTZ
);

-- ============================================================
-- Pokoje
-- ============================================================
CREATE TABLE IF NOT EXISTS rooms (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(64) UNIQUE NOT NULL,
    is_private  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Lista kontroli dostępu do pokojów prywatnych
-- ============================================================
CREATE TABLE IF NOT EXISTS room_acl (
    room_id     INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (room_id, user_id)
);

-- ============================================================
-- Banowanie użytkowników w pokojach (blokada ponownego dołączenia)
-- ============================================================
CREATE TABLE IF NOT EXISTS room_bans (
    room_id     INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    banned_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    banned_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (room_id, user_id)
);

-- ============================================================
-- Wyrzucenia użytkowników z pokojów publicznych (kick, bez bana)
-- Wymaga nowego zaproszenia (ROOM_INVITE) aby ponownie dołączyć.
-- ============================================================
CREATE TABLE IF NOT EXISTS room_kicks (
    room_id     INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kicked_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    kicked_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (room_id, user_id)
);

-- ============================================================
-- Historia wiadomości
-- ============================================================
CREATE TABLE IF NOT EXISTS messages (
    id          SERIAL PRIMARY KEY,
    msg_id      UUID UNIQUE NOT NULL,        -- UUID v4 z protokołu (wykrywanie duplikatów)
    seq_id      INTEGER NOT NULL,
    sender_id   INTEGER NOT NULL REFERENCES users(id),
    room_id     INTEGER REFERENCES rooms(id),         -- NULL jeśli wiadomość prywatna
    recipient_id INTEGER REFERENCES users(id),        -- NULL jeśli wiadomość do pokoju
    body        TEXT NOT NULL,
    hmac        VARCHAR(64) NOT NULL,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered   BOOLEAN NOT NULL DEFAULT TRUE  -- FALSE dla DM wysłanych do offline odbiorcy
);

-- ============================================================
-- Zaproszenia do pokojów oczekujące na dostarczenie offline użytkownikowi
-- ============================================================
CREATE TABLE IF NOT EXISTS room_invites (
    id              SERIAL PRIMARY KEY,
    room_id         INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    invited_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    invited_by      VARCHAR(64) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Cache nonce (ochrona przed replay attack przy logowaniu)
-- ============================================================
CREATE TABLE IF NOT EXISTS used_nonces (
    nonce       VARCHAR(64) PRIMARY KEY,
    used_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Logi systemowe
-- ============================================================
CREATE TABLE IF NOT EXISTS system_logs (
    id          SERIAL PRIMARY KEY,
    event       VARCHAR(64) NOT NULL,   -- LOGIN_FAILED, HMAC_ERROR, RATE_LIMIT, itp.
    username    VARCHAR(64),
    ip          VARCHAR(45),
    detail      TEXT,
    logged_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Indeksy
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_messages_room    ON messages(room_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_messages_sender  ON messages(sender_id);
CREATE INDEX IF NOT EXISTS idx_messages_msg_id  ON messages(msg_id);
CREATE INDEX IF NOT EXISTS idx_nonces_used_at   ON used_nonces(used_at);
CREATE INDEX IF NOT EXISTS idx_logs_event       ON system_logs(event, logged_at);
CREATE INDEX IF NOT EXISTS idx_room_bans_room   ON room_bans(room_id);
CREATE INDEX IF NOT EXISTS idx_room_kicks_room  ON room_kicks(room_id);
CREATE INDEX IF NOT EXISTS idx_messages_undelivered ON messages(recipient_id) WHERE delivered = FALSE;
CREATE INDEX IF NOT EXISTS idx_room_invites_user ON room_invites(invited_user_id);

-- ============================================================
-- Domyślne pokoje publiczne
-- ============================================================
INSERT INTO rooms (name, is_private) VALUES
    ('general', FALSE),
    ('random', FALSE)
ON CONFLICT (name) DO NOTHING;
-- ============================================================
-- Znajomi / zaproszenia do znajomych
-- ============================================================
CREATE TABLE IF NOT EXISTS friendships (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    friend_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status      VARCHAR(16) NOT NULL DEFAULT 'pending', -- pending / accepted / declined
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, friend_id)
);

CREATE INDEX IF NOT EXISTS idx_friendships_user   ON friendships(user_id, status);
CREATE INDEX IF NOT EXISTS idx_friendships_friend ON friendships(friend_id, status);