import asyncio
import threading
import customtkinter as ctk

from client.protocol.connection import RCMPConnection
from client.protocol.sender import RCMPSender
from client.protocol.receiver import RCMPReceiver
from client.gui.login_window import LoginWindow
from client.gui.chat_window import ChatWindow
from server.config import Config


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class RCMPApp(ctk.CTk):

    def __init__(self):
        super().__init__()

        self.title("RCMP Chat")
        self.geometry("900x620")
        self.minsize(700, 500)

        self.conn = RCMPConnection()
        self.sender = RCMPSender(self.conn)
        self.receiver = RCMPReceiver(self.conn)

        self._username: str = None
        self._current_room_id: int = None
        self._current_room_name: str = None
        self._jwt_exp: int = None

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        self._chat: ChatWindow = None
        self._login_window = None
        self._show_login()

        self.after(1000, self._tick)

    # ------------------------------------------------------------------
    # Asyncio
    # ------------------------------------------------------------------

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_async(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ------------------------------------------------------------------
    # Logowanie
    # ------------------------------------------------------------------

    def _show_login(self):
        self._login_window = LoginWindow(self, on_login=self._do_login)

    def _do_login(self, username: str, password: str):
        self._run_async(self._async_login(username, password))

    async def _async_login(self, username: str, password: str):
        connected = await self.conn.connect()
        if not connected:
            self.after(0, lambda: self._login_window.show_error(
                "Nie można połączyć z serwerem.\nSprawdź czy serwer jest uruchomiony."))
            return

        self.receiver.on("LOGIN_OK",        self._on_login_ok)
        self.receiver.on("LOGIN_ERR",       self._on_login_err)
        self.receiver.on("DELIVER_MESSAGE", self._on_deliver_message)
        self.receiver.on("ROOM_EVENT",      self._on_room_event)
        self.receiver.on("MESSAGE_ACK",     self._on_message_ack)
        self.receiver.on("PING",            self._on_ping)
        self.receiver.on("PONG",            self._on_pong)
        self.receiver.on("ERROR",           self._on_error)
        self.receiver.on("BYE_ACK",         self._on_bye_ack)

        asyncio.create_task(self.receiver.start())
        asyncio.create_task(self._ping_loop())
        asyncio.create_task(self._retransmit_loop())

        await self.sender.send_login(username, password)
        self._username = username

    # ------------------------------------------------------------------
    # Handlery serwera
    # ------------------------------------------------------------------

    async def _on_login_ok(self, data: dict):
        payload = data.get("payload", {})
        self.sender.token = payload.get("session_token")
        self.sender.hmac_secret = payload.get("hmac_secret")
        self._jwt_exp = payload.get("expires_in", 3600)
        self.after(0, self._show_chat)

    async def _on_login_err(self, data: dict):
        payload = data.get("payload", {})
        msg = payload.get("message", "Błędne dane logowania.")
        self.after(0, lambda: self._login_window.show_error(msg))

    async def _on_deliver_message(self, data: dict):
        payload = data.get("payload", {})
        username = payload.get("from_user", "?")
        body = payload.get("body", "")
        ts = data.get("ts")
        own = username == self._username
        msg_id = data.get("msg_id")
        self.after(0, lambda: self._chat.add_message(username, body, ts, own))
        await self.sender.send_message_ack(msg_id)

    async def _on_room_event(self, data: dict):
        payload = data.get("payload", {})
        event = payload.get("event")
        uname = payload.get("username", "?")
        room_name = payload.get("room_name", "")

        if event == "joined":
            text = f"{uname} dołączył do #{room_name}"
        elif event == "left":
            text = f"{uname} opuścił #{room_name}"
        else:
            text = f"Zdarzenie: {event}"

        self.after(0, lambda: self._chat.add_system_message(text))

    async def _on_message_ack(self, data: dict):
        payload = data.get("payload", {})
        ack_id = payload.get("ack_msg_id")
        if ack_id:
            self.sender.confirm_ack(ack_id)

    async def _on_ping(self, data: dict):
        await self.sender.send("PONG", {"ref_msg_id": data.get("msg_id")})

    async def _on_pong(self, data: dict):
        # Serwer odpowiedział na nasz PING — połączenie żyje
        pass

    async def _on_error(self, data: dict):
        payload = data.get("payload", {})
        code = payload.get("code")
        msg = payload.get("message", "Błąd serwera")
        print(f"[APP] ERROR {code}: {msg}")
        if self._chat:
            self.after(0, lambda: self._chat.add_system_message(f"Błąd {code}: {msg}"))

    async def _on_bye_ack(self, data: dict):
        self.conn.disconnect()
        self.after(0, self.destroy)

    # ------------------------------------------------------------------
    # Akcje użytkownika
    # ------------------------------------------------------------------

    # Dostępne pokoje (z serwera) — {room_id: {name, is_private}}
    _available_rooms = {
        1: {"name": "general",  "is_private": False},
        2: {"name": "random",   "is_private": False},
        3: {"name": "vip-room", "is_private": True},
    }

    def _show_chat(self):
        if self._login_window:
            self._login_window.close()
            self._login_window = None

        self._chat = ChatWindow(
            self,
            username=self._username,
            on_send=self._send_message,
            on_join_room=self._join_room,
            on_leave_room=self._leave_room,
            on_browse_rooms=self._browse_rooms,
        )
        self._chat.pack(fill="both", expand=True)

    def _browse_rooms(self):
        """Otwiera okno wyboru pokoju do dołączenia."""
        BrowseRoomsDialog(self, self._available_rooms, on_join=self._join_room)

    def _join_room(self, room_id: int):
        room = self._available_rooms.get(room_id, {})
        room_name = room.get("name", str(room_id))
        is_private = room.get("is_private", False)

        self._current_room_id = room_id
        self._current_room_name = room_name

        # Dodaj pokój do listy jeśli jeszcze nie ma
        self._chat.add_room(room_id, room_name, is_private)
        self._chat.set_active_room(room_id, room_name)
        self._chat.add_system_message(f"Dołączyłeś do #{room_name}")

        # Wyślij JOIN_ROOM do serwera
        self._run_async(self.sender.send_join_room(room_id))

    def _leave_room(self, room_id: int):
        self._run_async(self.sender.send_leave_room(room_id))
        self._current_room_id = None
        self._current_room_name = None
        self._chat.room_left(room_id)

    def _send_message(self, room_id: int, body: str):
        import time
        ts = int(time.time() * 1000)
        self._chat.add_message(self._username, body, ts, own=True)
        self._run_async(self.sender.send_message("room", room_id, body))

    # ------------------------------------------------------------------
    # Pętle w tle
    # ------------------------------------------------------------------

    async def _ping_loop(self):
        while self.conn.is_connected():
            await asyncio.sleep(Config.PING_INTERVAL)
            if self.conn.is_connected():
                await self.sender.send_ping()

    async def _retransmit_loop(self):
        while self.conn.is_connected():
            await asyncio.sleep(1)
            await self.sender.check_retransmissions()

    def _tick(self):
        if self._chat:
            self._chat.set_connected(self.conn.is_connected())
            if self._jwt_exp:
                self._jwt_exp -= 1
                self._chat.set_jwt_ttl(self._jwt_exp)
        self.after(1000, self._tick)

    # ------------------------------------------------------------------
    # Zamknięcie
    # ------------------------------------------------------------------

    def on_closing(self):
        if self.conn.is_connected():
            self._run_async(self.sender.send_bye())
        else:
            self.destroy()


class BrowseRoomsDialog(ctk.CTkToplevel):
    """Okno wyboru pokoju do dołączenia."""

    def __init__(self, parent, available_rooms: dict, on_join):
        super().__init__(parent)
        self.on_join = on_join
        self.available_rooms = available_rooms

        self.title("Dołącz do pokoju")
        self.geometry("320x380")
        self.resizable(False, False)
        self.grab_set()

        ctk.CTkLabel(self, text="Dostępne pokoje",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 4))
        ctk.CTkLabel(self, text="Kliknij pokój aby dołączyć",
                     font=ctk.CTkFont(size=12), text_color="#888888").pack(pady=(0, 12))

        frame = ctk.CTkScrollableFrame(self)
        frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        for room_id, info in available_rooms.items():
            icon = "🔒" if info["is_private"] else "#"
            label = f"  {icon}  {info['name']}"
            btn = ctk.CTkButton(
                frame,
                text=label,
                anchor="w",
                height=40,
                font=ctk.CTkFont(size=13),
                fg_color="#2B2D42",
                hover_color="#3B3FA6",
                command=lambda rid=room_id: self._select(rid),
            )
            btn.pack(fill="x", pady=3)

    def _select(self, room_id: int):
        self.on_join(room_id)
        self.destroy()