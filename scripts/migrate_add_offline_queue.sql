-- Migracja: kolejkowanie DM i zaproszeń do pokoi dla użytkowników offline
-- Uruchomienie: psql -U rcmp_user -d rcmp_chat -f scripts/migrate_add_offline_queue.sql

-- ============================================================
-- Flaga dostarczenia na wiadomościach prywatnych.
-- Wiadomości do pokoju zawsze mają delivered = TRUE (broadcast, brak kolejkowania).
-- DM wysłane do offline odbiorcy dostają delivered = FALSE i są wypychane przy LOGIN_OK.
-- ============================================================
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS delivered BOOLEAN NOT NULL DEFAULT TRUE;

CREATE INDEX IF NOT EXISTS idx_messages_undelivered
    ON messages (recipient_id)
    WHERE delivered = FALSE;

-- ============================================================
-- Zaproszenia do pokojów oczekujące na dostarczenie.
-- Wiersz usuwany po dostarczeniu (skip_locked na wypadek wielu
-- równoległych połączeń tego samego usera przy reconnect race).
-- ============================================================
CREATE TABLE IF NOT EXISTS room_invites (
    id             SERIAL PRIMARY KEY,
    room_id        INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    invited_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    invited_by     VARCHAR(64) NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_room_invites_user ON room_invites (invited_user_id);
