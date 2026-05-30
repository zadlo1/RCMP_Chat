import hmac
import hashlib


def compute_hmac(secret: str, msg_id: str, ts: int, seq_id: int, body: str) -> str:
    """
    Oblicza HMAC-SHA256 dla wiadomości SEND_MESSAGE.
    Zgodnie ze specyfikacją: HMAC(msg_id + ts + seq_id + body)
    """
    data = f"{msg_id}{ts}{seq_id}{body}".encode("utf-8")
    key = secret.encode("utf-8")
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def verify_hmac(secret: str, msg_id: str, ts: int, seq_id: int, body: str, received_hmac: str) -> bool:
    """
    Weryfikuje HMAC wiadomości. Używa hmac.compare_digest żeby zapobiec timing attack.
    """
    expected = compute_hmac(secret, msg_id, ts, seq_id, body)
    return hmac.compare_digest(expected, received_hmac)