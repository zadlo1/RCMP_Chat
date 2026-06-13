import asyncpg
from server.config import Config


class RoomManager:

    def __init__(self, db_pool: asyncpg.Pool):
        self.db = db_pool
        # Aktywni użytkownicy w pokojach: {room_id: set(user_id)}
        self._room_members: dict[int, set[int]] = {}

    # ------------------------------------------------------------------
    # Sprawdzanie pokoju i uprawnień
    # ------------------------------------------------------------------

    async def get_room(self, room_id: int) -> dict | None:
        """Pobiera dane pokoju z bazy. Zwraca None jeśli pokój nie istnieje."""
        row = await self.db.fetchrow(
            "SELECT id, name, is_private FROM rooms WHERE id = $1",
            room_id
        )
        return dict(row) if row else None

    async def get_room_by_name(self, name: str) -> dict | None:
        """Pobiera dane pokoju po nazwie."""
        row = await self.db.fetchrow(
            "SELECT id, name, is_private FROM rooms WHERE name = $1",
            name
        )
        return dict(row) if row else None

    async def check_access(self, room_id: int, user_id: int) -> bool:
        """
        Sprawdza czy użytkownik ma dostęp do pokoju.
        Zbanowani użytkownicy — zawsze False.
        Pokoje publiczne — zawsze True (jeśli nie zbanowani).
        Pokoje prywatne — tylko jeśli user_id jest na liście ACL.
        """
        if await self.is_banned(room_id, user_id):
            return False

        room = await self.get_room(room_id)
        if room is None:
            return False
        if not room["is_private"]:
            return True
        row = await self.db.fetchrow(
            "SELECT 1 FROM room_acl WHERE room_id = $1 AND user_id = $2",
            room_id, user_id
        )
        return row is not None

    # ------------------------------------------------------------------
    # Banowanie użytkowników
    # ------------------------------------------------------------------

    async def is_banned(self, room_id: int, user_id: int) -> bool:
        """Sprawdza czy użytkownik jest zbanowany w danym pokoju."""
        rows = await self.db.fetch(
            "SELECT 1 FROM room_bans WHERE room_id = $1 AND user_id = $2",
            room_id, user_id
        )
        return len(rows) > 0

    async def ban_user(self, room_id: int, user_id: int, banned_by: int):
        """Banuje użytkownika w pokoju — usuwa z ACL i blokuje dołączanie."""
        await self.db.execute(
            """
            INSERT INTO room_bans (room_id, user_id, banned_by)
            VALUES ($1, $2, $3)
            ON CONFLICT (room_id, user_id) DO NOTHING
            """,
            room_id, user_id, banned_by
        )
        await self.db.execute(
            "DELETE FROM room_acl WHERE room_id = $1 AND user_id = $2",
            room_id, user_id
        )
        self.leave_room(room_id, user_id)

    async def unban_user(self, room_id: int, user_id: int):
        """Usuwa bana użytkownika z pokoju."""
        await self.db.execute(
            "DELETE FROM room_bans WHERE room_id = $1 AND user_id = $2",
            room_id, user_id
        )

    async def list_banned(self, room_id: int) -> list[dict]:
        """Zwraca listę zbanowanych użytkowników w pokoju."""
        rows = await self.db.fetch(
            """
            SELECT u.id AS user_id, u.username
            FROM room_bans b
            JOIN users u ON u.id = b.user_id
            WHERE b.room_id = $1
            ORDER BY u.username
            """,
            room_id
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Dołączanie i opuszczanie pokoju
    # ------------------------------------------------------------------

    async def join_room(self, room_id: int, user_id: int) -> bool:
        """
        Dodaje użytkownika do pokoju.
        Zwraca False jeśli pokój nie istnieje, brak dostępu lub limit pokojów.
        """
        # Sprawdź limit pokojów na użytkownika
        user_rooms = self._get_user_rooms(user_id)
        if len(user_rooms) >= Config.MAX_ROOMS_PER_USER:
            return False

        if not await self.check_access(room_id, user_id):
            return False

        if room_id not in self._room_members:
            self._room_members[room_id] = set()
        self._room_members[room_id].add(user_id)
        return True

    def leave_room(self, room_id: int, user_id: int):
        """Usuwa użytkownika z pokoju."""
        if room_id in self._room_members:
            self._room_members[room_id].discard(user_id)

    def leave_all_rooms(self, user_id: int):
        """Usuwa użytkownika ze wszystkich pokojów — przy rozłączeniu."""
        for members in self._room_members.values():
            members.discard(user_id)

    # ------------------------------------------------------------------
    # Informacje o członkach
    # ------------------------------------------------------------------

    def get_room_members(self, room_id: int) -> set[int]:
        """Zwraca zbiór user_id aktualnie w pokoju."""
        return self._room_members.get(room_id, set()).copy()

    def is_member(self, room_id: int, user_id: int) -> bool:
        """Sprawdza czy użytkownik jest w pokoju."""
        return user_id in self._room_members.get(room_id, set())

    def _get_user_rooms(self, user_id: int) -> list[int]:
        """Zwraca listę room_id do których należy użytkownik."""
        return [
            room_id for room_id, members in self._room_members.items()
            if user_id in members
        ]

    def get_user_rooms(self, user_id: int) -> list[int]:
        return self._get_user_rooms(user_id)

    # ------------------------------------------------------------------
    # Lista pokojów
    # ------------------------------------------------------------------

    async def list_public_rooms(self) -> list[dict]:
        """Zwraca listę wszystkich publicznych pokojów."""
        rows = await self.db.fetch(
            "SELECT id, name FROM rooms WHERE is_private = FALSE ORDER BY name"
        )
        return [dict(r) for r in rows]
    def remove_room(self, room_id: int):
        """Usuwa pokój z pamięci in-memory (po usunięciu z bazy przez admina)."""
        self._room_members.pop(room_id, None)
