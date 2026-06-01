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
        self._available_rooms: dict = {}
        self._bg_tasks: list = []  # referencje do tasków żeby GC ich nie zebrał

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
        self.receiver.on("ROOM_INVITE",     self._on_room_invite)
        self.receiver.on("ROOMS_LIST",      self._on_rooms_list)
        self.receiver.on("ERROR",           self._on_error)
        self.receiver.on("BYE_ACK",         self._on_bye_ack)

        self._bg_tasks = [
            asyncio.create_task(self.receiver.start()),
            asyncio.create_task(self._ping_loop()),
            asyncio.create_task(self._retransmit_loop()),
        ]

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

    async def _on_rooms_list(self, data: dict):
        payload = data.get("payload", {})
        rooms = payload.get("rooms", [])
        self._available_rooms = {
            r["id"]: {"name": r["name"], "is_private": r["is_private"]}
            for r in rooms
        }
        # Dodaj do sidebara tylko pokoje do których user ma dostęp
        for r in rooms:
            if r.get("has_access", True):
                self.after(0, lambda room=r: self._chat.add_room(
                    room["id"], room["name"], room["is_private"]
                ))

    async def _on_room_invite(self, data: dict):
        payload = data.get("payload", {})
        room_id = payload.get("room_id")
        room_name = payload.get("room_name", "?")
        invited_by = payload.get("invited_by", "?")

        def accept():
            self._run_async(self.sender.send(
                "ROOM_INVITE_ACCEPT", {"room_id": room_id}
            ))
            # Dodaj pokój do listy i dołącz
            self._available_rooms[room_id] = {
                "name": room_name, "is_private": True
            }
            self._join_room(room_id)

        def decline():
            self._run_async(self.sender.send(
                "ROOM_INVITE_DECLINE", {"room_id": room_id}
            ))

        self.after(0, lambda: InviteDialog(
            self, room_name, invited_by,
            on_accept=accept, on_decline=decline
        ))

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
        )
        self._chat.pack(fill="both", expand=True)

    def _send_invite(self, room_id: int, room_name: str, username: str):
        """Wysyła zaproszenie do prywatnego pokoju."""
        self._run_async(self.sender.send(
            "SEND_MESSAGE", {
                "target_type": "invite",
                "target_id": room_id,
                "room_name": room_name,
                "invite_to": username,
                "seq_id": 0,
                "body": f"Zaproszenie do #{room_name}",
                "hmac": "",
            }
        ))

    def _join_room(self, room_id: int):
        room = self._available_rooms.get(room_id, {})
        room_name = room.get("name", str(room_id))

        self._current_room_id = room_id
        self._current_room_name = room_name

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
    """Okno wyboru pokoju do dołączenia lub wysłania zaproszenia."""

    def __init__(self, parent, available_rooms: dict, on_join,
                 is_admin: bool = False, on_invite=None):
        super().__init__(parent)
        self.on_join = on_join
        self.on_invite = on_invite
        self.available_rooms = available_rooms
        self.is_admin = is_admin

        self.title("Dołącz do pokoju")
        self.geometry("360x420")
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
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill="x", pady=3)

            ctk.CTkButton(
                row,
                text=f"  {icon}  {info['name']}",
                anchor="w",
                height=40,
                font=ctk.CTkFont(size=13),
                fg_color="#2B2D42",
                hover_color="#3B3FA6",
                command=lambda rid=room_id: self._select(rid),
            ).pack(side="left", fill="x", expand=True)

            # Przycisk zaproszenia — tylko dla admina i pokojów prywatnych
            if is_admin and info["is_private"] and on_invite:
                ctk.CTkButton(
                    row,
                    text="✉",
                    width=40, height=40,
                    font=ctk.CTkFont(size=16),
                    fg_color="#3B3FA6",
                    hover_color="#5558CC",
                    command=lambda rid=room_id, rn=info["name"]: self._invite(rid, rn),
                ).pack(side="right", padx=(4, 0))

    def _select(self, room_id: int):
        self.on_join(room_id)
        self.destroy()

    def _invite(self, room_id: int, room_name: str):
        if self.on_invite:
            self.on_invite(room_id, room_name)
        self.destroy()


class InviteDialog(ctk.CTkToplevel):
    """Okno zaproszenia do prywatnego pokoju."""

    def __init__(self, parent, room_name: str, invited_by: str,
                 on_accept, on_decline):
        super().__init__(parent)
        self.on_accept = on_accept
        self.on_decline = on_decline

        self.title("Zaproszenie do pokoju")
        self.geometry("360x220")
        self.resizable(False, False)
        self.grab_set()

        ctk.CTkLabel(self, text="🔒  Zaproszenie do pokoju",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(24, 4))

        ctk.CTkLabel(
            self,
            text=f"{invited_by} zaprasza Cię do pokoju\n#{room_name}",
            font=ctk.CTkFont(size=13),
            text_color="#CCCCCC",
            justify="center",
        ).pack(pady=(4, 20))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack()

        ctk.CTkButton(
            btn_frame, text="Akceptuj", width=130, height=38,
            fg_color="#1D9E75", hover_color="#158A63",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._accept,
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            btn_frame, text="Odrzuć", width=130, height=38,
            fg_color="#555555", hover_color="#CC4444",
            font=ctk.CTkFont(size=13),
            command=self._decline,
        ).pack(side="left", padx=8)

    def _accept(self):
        self.on_accept()
        self.destroy()

    def _decline(self):
        self.on_decline()
        self.destroy()


class SendInviteDialog(ctk.CTkToplevel):
    """Okno wysyłania zaproszenia do prywatnego pokoju (dla admina)."""

    def __init__(self, parent, room_id: int, room_name: str, on_send):
        super().__init__(parent)
        self.room_id = room_id
        self.room_name = room_name
        self.on_send = on_send

        self.title("Wyślij zaproszenie")
        self.geometry("340x220")
        self.resizable(False, False)
        self.grab_set()

        ctk.CTkLabel(self, text=f"Zaproś do #{room_name}",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(24, 4))

        ctk.CTkLabel(self, text="Podaj nazwę użytkownika:",
                     font=ctk.CTkFont(size=12),
                     text_color="#AAAAAA").pack(pady=(8, 4))

        self._entry = ctk.CTkEntry(self, placeholder_text="np. bob",
                                   height=38, font=ctk.CTkFont(size=13))
        self._entry.pack(fill="x", padx=32, pady=(0, 8))

        self._error = ctk.CTkLabel(self, text="", text_color="#CC4444",
                                   font=ctk.CTkFont(size=11))
        self._error.pack()

        ctk.CTkButton(
            self, text="Wyślij zaproszenie", height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._send,
        ).pack(fill="x", padx=32, pady=(8, 0))

        self._entry.bind("<Return>", lambda e: self._send())
        self.after(100, self._entry.focus)

    def _send(self):
        username = self._entry.get().strip()
        if not username:
            self._error.configure(text="Podaj nazwę użytkownika.")
            return
        self.on_send(self.room_id, self.room_name, username)
        self.destroy()