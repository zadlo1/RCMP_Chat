"""
Skrypt do czyszczenia tabeli friendships
"""
import asyncio
import asyncpg
import sys
import os

# Dodaj root projektu do path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from server.config import Config


async def main():
    pool = await asyncpg.create_pool(Config.db_dsn())

    # Usuń wszystkie zaproszenia
    result = await pool.execute("DELETE FROM friendships")
    print(f"Usunięto zaproszenia: {result}")

    # Pokaż aktualny stan
    rows = await pool.fetch("SELECT * FROM friendships")
    print(f"Pozostało rekordów: {len(rows)}")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
