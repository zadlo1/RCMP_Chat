-- Migracja: dodaje brakującą tabelę room_bans (i friendships jeśli brak)
-- Uruchomienie: psql -U rcmp_user -d rcmp_chat -f scripts/migrate_add_room_bans.sql

CREATE TABLE IF NOT EXISTS room_bans (
    room_id     INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    banned_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    banned_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (room_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_room_bans_room ON room_bans(room_id);

CREATE TABLE IF NOT EXISTS friendships (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    friend_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status      VARCHAR(16) NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, friend_id)
);

CREATE INDEX IF NOT EXISTS idx_friendships_user   ON friendships(user_id, status);
CREATE INDEX IF NOT EXISTS idx_friendships_friend ON friendships(friend_id, status);

\echo 'Migracja zakończona.'
