"""
Wypełnia bazę danych testowymi danymi.
Użycie: python scripts/seed_db.py
"""
import asyncio
import asyncpg
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.config import Config
from server.managers.auth import AuthManager


async def seed():
    print("[SEED] Łączenie z bazą...")
    pool = await asyncpg.create_pool(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        database=Config.DB_NAME,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
    )

    # Użytkownicy testowi
    users = [
        ("alice",  "alice123",  "user"),
        ("bob",    "bob123",    "user"),
        ("carol",  "carol123",  "user"),
        ("admin",  "admin123",  "admin"),
    ]

    print("[SEED] Dodawanie użytkowników...")
    for username, password, role in users:
        hashed = AuthManager.hash_password(password)
        await pool.execute(
            """
            INSERT INTO users (username, password, role)
            VALUES ($1, $2, $3)
            ON CONFLICT (username) DO NOTHING
            """,
            username, hashed, role
        )
        print(f"  + {username} (rola: {role})")

    # Pokoje
    rooms = [
        ("general",  False),
        ("random",   False),
        ("vip-room", True),
    ]

    print("[SEED] Dodawanie pokojów...")
    for name, is_private in rooms:
        await pool.execute(
            """
            INSERT INTO rooms (name, is_private)
            VALUES ($1, $2)
            ON CONFLICT (name) DO NOTHING
            """,
            name, is_private
        )
        print(f"  + #{name} ({'prywatny' if is_private else 'publiczny'})")

    # ACL dla vip-room — tylko admin
    print("[SEED] Ustawianie ACL dla vip-room...")
    vip_room = await pool.fetchrow("SELECT id FROM rooms WHERE name = 'vip-room'")
    if vip_room:
        for username in ("admin",):
            user = await pool.fetchrow("SELECT id FROM users WHERE username = $1", username)
            if user:
                await pool.execute(
                    """
                    INSERT INTO room_acl (room_id, user_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                    """,
                    vip_room["id"], user["id"]
                )
                print(f"  + {username} -> vip-room")

    await pool.close()
    print("[SEED] Gotowe!")
    print()
    print("Dane logowania do testów:")
    print("  alice  / alice123  (user,  dostęp do vip-room)")
    print("  bob    / bob123    (user,  bez dostępu do vip-room)")
    print("  carol  / carol123  (user,  bez dostępu do vip-room)")
    print("  admin  / admin123  (admin, dostęp do vip-room)")


if __name__ == "__main__":
    asyncio.run(seed())