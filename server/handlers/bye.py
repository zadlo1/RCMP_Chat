import uuid
import time

from server.session import Session, SessionManager
from server.managers.message_router import MessageRouter
from server.managers.room_manager import RoomManager


async def handle_bye(
    data: dict,
    session: Session,
    session_manager: SessionManager,
    router: MessageRouter,
    room_manager: RoomManager,
):
    """
    Obsługuje BYE od klienta:
    1. Usuwa użytkownika ze wszystkich pokojów.
    2. Odsyła BYE_ACK.
    3. Zamyka sesję.
    """
    session.state = "CLOSING"

    # Opuszczenie wszystkich pokojów
    if session.user_id is not None:
        room_manager.leave_all_rooms(session.user_id)
        router.unregister(session.user_id)

        # Aktualizacja statusu w bazie — obsługiwane przez wywołujący kod
        # (main.py ma dostęp do db_pool)

    # BYE_ACK
    bye_ack = {
        "type": "BYE_ACK",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": session.token,
        "payload": {
            "ref_msg_id": data.get("msg_id")
        }
    }
    await router._write(session.writer, bye_ack)

    # Zamknięcie sesji
    session_manager.remove_session(session)
    session.state = "CLOSED"

    try:
        session.writer.close()
        await session.writer.wait_closed()
    except Exception:
        pass