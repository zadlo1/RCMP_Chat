"""
Testy jednostkowe dla RoomManager (in-memory, bez bazy danych).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from server.managers.room_manager import RoomManager
from server.config import Config


def make_manager(rows_fetchrow=None, rows_fetch=None):
    db = AsyncMock()
    db.fetchrow = AsyncMock(return_value=rows_fetchrow)
    db.fetch = AsyncMock(return_value=rows_fetch or [])
    return RoomManager(db), db


class TestGetRoom:
    @pytest.mark.asyncio
    async def test_returns_room_dict_when_exists(self):
        row = {"id": 1, "name": "general", "is_private": False}
        rm, _ = make_manager(rows_fetchrow=row)
        result = await rm.get_room(1)
        assert result == dict(row)

    @pytest.mark.asyncio
    async def test_returns_none_when_not_exists(self):
        rm, _ = make_manager(rows_fetchrow=None)
        result = await rm.get_room(999)
        assert result is None


class TestCheckAccess:
    @pytest.mark.asyncio
    async def test_public_room_grants_access(self):
        room = {"id": 1, "name": "public", "is_private": False}
        rm, _ = make_manager(rows_fetchrow=room)
        assert await rm.check_access(1, user_id=42) is True

    @pytest.mark.asyncio
    async def test_private_room_user_in_acl(self):
        room = {"id": 2, "name": "secret", "is_private": True}
        rm, db = make_manager(rows_fetchrow=room)
        # Pierwsze fetchrow → zwraca pokój; drugie → ACL hit
        db.fetchrow = AsyncMock(side_effect=[room, {"1": 1}])
        assert await rm.check_access(2, user_id=10) is True

    @pytest.mark.asyncio
    async def test_private_room_user_not_in_acl(self):
        room = {"id": 2, "name": "secret", "is_private": True}
        rm, db = make_manager(rows_fetchrow=room)
        db.fetchrow = AsyncMock(side_effect=[room, None])
        assert await rm.check_access(2, user_id=99) is False

    @pytest.mark.asyncio
    async def test_nonexistent_room_denies_access(self):
        rm, _ = make_manager(rows_fetchrow=None)
        assert await rm.check_access(999, user_id=1) is False


class TestJoinLeaveRoom:
    @pytest.mark.asyncio
    async def test_join_public_room(self):
        room = {"id": 1, "name": "general", "is_private": False}
        rm, _ = make_manager(rows_fetchrow=room)
        success = await rm.join_room(1, user_id=5)
        assert success is True
        assert rm.is_member(1, 5)

    @pytest.mark.asyncio
    async def test_join_denied_for_no_access(self):
        room = {"id": 2, "name": "secret", "is_private": True}
        rm, db = make_manager(rows_fetchrow=room)
        db.fetchrow = AsyncMock(side_effect=[room, None])  # ACL miss
        success = await rm.join_room(2, user_id=99)
        assert success is False
        assert not rm.is_member(2, 99)

    @pytest.mark.asyncio
    async def test_join_respects_room_limit(self):
        rm, db = make_manager()
        room = {"id": 0, "name": "r", "is_private": False}
        # Wypełnij limit pokojów
        for i in range(Config.MAX_ROOMS_PER_USER):
            if i not in rm._room_members:
                rm._room_members[i] = set()
            rm._room_members[i].add(99)

        db.fetchrow = AsyncMock(return_value=room)
        success = await rm.join_room(1000, user_id=99)
        assert success is False

    def test_leave_room(self):
        rm, _ = make_manager()
        rm._room_members[1] = {5, 6}
        rm.leave_room(1, 5)
        assert not rm.is_member(1, 5)
        assert rm.is_member(1, 6)

    def test_leave_all_rooms(self):
        rm, _ = make_manager()
        rm._room_members[1] = {5}
        rm._room_members[2] = {5, 6}
        rm.leave_all_rooms(5)
        assert not rm.is_member(1, 5)
        assert not rm.is_member(2, 5)
        assert rm.is_member(2, 6)

    def test_remove_room(self):
        rm, _ = make_manager()
        rm._room_members[10] = {1, 2, 3}
        rm.remove_room(10)
        assert rm.get_room_members(10) == set()
        assert 10 not in rm._room_members


class TestRoomMembers:
    def test_get_room_members_empty(self):
        rm, _ = make_manager()
        assert rm.get_room_members(99) == set()

    def test_get_room_members_copy(self):
        rm, _ = make_manager()
        rm._room_members[1] = {10, 20}
        members = rm.get_room_members(1)
        members.add(30)
        # Oryginał nie powinien być zmieniony
        assert 30 not in rm._room_members[1]

    def test_get_user_rooms(self):
        rm, _ = make_manager()
        rm._room_members[1] = {5}
        rm._room_members[2] = {5, 6}
        rm._room_members[3] = {6}
        assert set(rm.get_user_rooms(5)) == {1, 2}
        assert set(rm.get_user_rooms(6)) == {2, 3}


class TestListPublicRooms:
    @pytest.mark.asyncio
    async def test_returns_public_rooms(self):
        rows = [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]
        rm, db = make_manager(rows_fetch=rows)
        result = await rm.list_public_rooms()
        assert len(result) == 2
        assert result[0]["name"] == "alpha"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_rooms(self):
        rm, _ = make_manager(rows_fetch=[])
        result = await rm.list_public_rooms()
        assert result == []
