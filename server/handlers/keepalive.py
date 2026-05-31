import uuid
import time

from server.session import Session
from server.managers.message_router import MessageRouter


async def handle_ping(
    data: dict,
    session: Session,
    router: MessageRouter,
):
    """Odbiera PING od klienta, odsyła PONG i aktualizuje czas aktywności."""
    session.touch()

    pong = {
        "type": "PONG",
        "msg_id": str(uuid.uuid4()),
        "ts": int(time.time() * 1000),
        "token": session.token,
        "payload": {
            "ref_msg_id": data.get("msg_id")
        }
    }
    await router._write(session.writer, pong)


async def handle_pong(
    data: dict,
    session: Session,
):
    """Odbiera PONG od klienta (gdy serwer wysłał PING) — aktualizuje aktywność."""
    session.touch()