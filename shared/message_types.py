class MessageType:
    # Klient -> Serwer
    LOGIN = "LOGIN"
    JOIN_ROOM = "JOIN_ROOM"
    LEAVE_ROOM = "LEAVE_ROOM"
    SEND_MESSAGE = "SEND_MESSAGE"
    MESSAGE_ACK = "MESSAGE_ACK"
    STATUS = "STATUS"
    PING = "PING"
    BYE = "BYE"

    # Serwer -> Klient
    LOGIN_OK = "LOGIN_OK"
    LOGIN_ERR = "LOGIN_ERR"
    ROOM_EVENT = "ROOM_EVENT"
    DELIVER_MESSAGE = "DELIVER_MESSAGE"
    PONG = "PONG"
    ERROR = "ERROR"
    BYE_ACK = "BYE_ACK"

    ALL = {
        LOGIN, JOIN_ROOM, LEAVE_ROOM, SEND_MESSAGE, MESSAGE_ACK,
        STATUS, PING, BYE, LOGIN_OK, LOGIN_ERR, ROOM_EVENT,
        DELIVER_MESSAGE, PONG, ERROR, BYE_ACK
    }

    # Typy które nie wymagają tokenu sesji
    NO_AUTH_REQUIRED = {LOGIN}