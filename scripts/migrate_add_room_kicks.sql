-- Migracja: dodaje tabelę room_kicks
-- Przechowuje informację o użytkownikach wyrzuconych (kick, bez bana)
-- z pokojów publicznych — wymagają nowego zaproszenia, aby dołączyć ponownie.
-- Uruchomienie: psql -U rcmp_user -d rcmp_chat -f scripts/migrate_add_room_kicks.sql

CREATE TABLE IF NOT EXISTS room_kicks (
    room_id     INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kicked_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    kicked_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (room_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_room_kicks_room ON room_kicks(room_id);

\echo 'Migracja zakończona.'
