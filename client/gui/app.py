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
        self._bg_tasks: list = []
        self._dm_windows: dict[str, object] = {}  # {username: DMWindow}
        self._pending_friends: list = []  # Lista znajomych otrzymana przed utworzeniem GUI
        self._friends_cache: list = []
        self._tls_version: str = "TLS"
        self._user_role: str = "user"
        self._pending_rooms: list = []

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
        self.receiver.on("ROOMS_LIST",          self._on_rooms_list)
        self.receiver.on("FRIENDS_LIST",         self._on_friends_list)
        self.receiver.on("FRIEND_REQUEST",        self._on_friend_request_incoming)
        self.receiver.on("FRIEND_REQUEST_ACCEPT", self._on_friend_request_accepted)
        self.receiver.on("FRIEND_STATUS_UPDATE",  self._on_friend_status_update)
        self.receiver.on("ERROR",           self._on_error)
        self.receiver.on("BYE_ACK",         self._on_bye_ack)
        self.receiver.on("MESSAGE_EXPIRE",   self._on_message_expire)
        self.receiver.on("FRIEND_REMOVE",    self._on_friend_removed)
        self.receiver.on("CREATE_ROOM_OK",   self._on_create_room_ok)

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
        self._tls_version = self.conn.get_tls_version()
        self._user_role = payload.get("role", "user")
        # Wyczyść stare pending ACKs z poprzedniej sesji (stary HMAC secret!)
        self.sender._pending_acks.clear()
        self.sender._seq_id = 0
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
        target_type = payload.get("target_type", "room")

        if target_type == "dm":
            # Wiadomość prywatna — otwórz lub zaktualizuj DMWindow
            from_user = payload.get("from_user", "?")
            # Klucz okna DM to zawsze nazwa drugiej osoby
            dm_key = from_user if from_user != self._username else payload.get("target_username", from_user)
            is_own = from_user == self._username

            def show_dm(u=dm_key, b=body, t=ts, o=is_own, fu=from_user):
                if u not in self._dm_windows:
                    self._open_dm(u)
                dm = self._dm_windows.get(u)
                if dm:
                    dm.add_message(fu, b, t, own=o)
                    if not o:
                        try:
                            dm.deiconify()
                            dm.lift()
                        except Exception:
                            pass
            self.after(0, show_dm)
        else:
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
        self._pending_rooms = [r for r in rooms if r.get("has_access", True)]
        # Jeśli chat już istnieje — dodaj od razu, jeśli nie — doda _show_chat
        if self._chat:
            for r in self._pending_rooms:
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

        if not self._chat:
            # Błąd przed zalogowaniem — pokaż w oknie logowania
            if self._login_window:
                if code == 4031 and "already logged in" in msg:
                    err_msg = "Ten uzytkownik jest juz zalogowany w innej sesji."
                else:
                    err_msg = f"Blad serwera ({code}): {msg}"
                self.after(0, lambda m=err_msg: self._login_window.show_error(m))
            return

        # Błędy związane ze znajomymi — pokaż jako popup
        if code == 4042:
            self.after(0, lambda m=msg: self._show_info_popup("Nieznany użytkownik", m))
        elif code == 4001 and "already" in msg.lower():
            self.after(0, lambda m=msg: self._show_info_popup("Znajomi", m))
        else:
            self.after(0, lambda: self._chat.add_system_message(f"Błąd {code}: {msg}"))

    def _show_info_popup(self, title: str, message: str):
        """Mały popup z informacją — zamyka się sam po 3 sekundach."""
        popup = ctk.CTkToplevel(self)
        popup.title(title)
        popup.geometry("320x130")
        popup.resizable(False, False)
        popup.grab_set()

        ctk.CTkLabel(
            popup, text=message,
            font=ctk.CTkFont(size=13),
            wraplength=280,
            justify="center",
        ).pack(expand=True, pady=(20, 8))

        ctk.CTkButton(
            popup, text="OK", width=80, height=30,
            command=popup.destroy,
        ).pack(pady=(0, 16))

        popup.after(3000, lambda: popup.destroy() if popup.winfo_exists() else None)

    async def _on_bye_ack(self, data: dict):
        self.conn.disconnect()
        self.after(0, self.destroy)

    async def _on_friends_list(self, data: dict):
        payload = data.get("payload", {})
        friends = payload.get("friends", [])
        self._friends_cache = friends
        print(f"[APP] Otrzymano FRIENDS_LIST: {len(friends)} znajomych, _chat={self._chat is not None}")
        for f in friends:
            print(f"  - {f['username']}: {f.get('friendship_status')}, {f.get('status')}")

        def update_friends():
            if self._chat:
                print(f"[APP] Wywołuję _chat.set_friends z {len(friends)} znajomymi")
                self._chat.set_friends(friends)
            else:
                print("[APP] _chat jeszcze nie istnieje, zapisuję do _pending_friends")
                self._pending_friends = friends

        self.after(0, update_friends)

    async def _on_friend_request_incoming(self, data: dict):
        payload = data.get("payload", {})
        from_user = payload.get("from_user", "?")
        from_user_id = payload.get("from_user_id")

        def accept():
            self._run_async(self.sender.send(
                "FRIEND_REQUEST_ACCEPT", {"from_user_id": from_user_id}
            ))

        def decline():
            self._run_async(self.sender.send(
                "FRIEND_REQUEST_DECLINE", {"from_user_id": from_user_id}
            ))

        self.after(0, lambda: FriendRequestDialog(
            self, from_user, on_accept=accept, on_decline=decline
        ))

    async def _on_message_expire(self, data: dict):
        payload = data.get("payload", {})
        msg_id = payload.get("msg_id")
        expire_in = payload.get("expire_in_seconds", 60)

        def schedule_expire(mid=msg_id, delay=expire_in):
            for dm_win in self._dm_windows.values():
                self.after(delay * 1000, lambda m=mid, w=dm_win: w.expire_message(m)
                           if w.winfo_exists() else None)

        self.after(0, schedule_expire)

    async def _on_friend_request_accepted(self, data: dict):
        payload = data.get("payload", {})
        username = payload.get("username", "?")
        self.after(0, lambda: self._chat.add_system_message(
            f"{username} zaakceptował zaproszenie do znajomych"
        ) if self._chat else None)
        # Serwer automatycznie wysyła zaktualizowaną FRIENDS_LIST po akceptacji

    async def _on_friend_status_update(self, data: dict):
        payload = data.get("payload", {})
        username = payload.get("username", "?")
        status = payload.get("status", "offline")
        self.after(0, lambda: self._chat.update_friend_status(username, status)
                   if self._chat else None)
        # Aktualizuj DM window jeśli otwarte
        dm = self._dm_windows.get(username)
        if dm:
            self.after(0, lambda: dm.set_status(status))

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
            on_add_friend=self._show_add_friend_dialog,
            on_open_dm=self._open_dm,
            on_remove_friend=self._remove_friend,
            is_admin=(self._user_role == "admin"),
            on_create_room=self._show_create_room_dialog,
        )
        self._chat.pack(fill="both", expand=True)
        self._chat.set_tls_version(self._tls_version)

        # Załaduj pokoje z cache (mogły przyjść przed GUI)
        for r in self._pending_rooms:
            self._chat.add_room(r["id"], r["name"], r["is_private"])

        # Załaduj znajomych z cache
        if self._pending_friends:
            self._chat.set_friends(self._pending_friends)
            self._pending_friends = []

        # Zastosuj listę znajomych jeśli przyszła przed utworzeniem GUI
        if self._pending_friends:
            print(f"[APP] Stosowanie odłożonej listy {len(self._pending_friends)} znajomych")
            self._chat.set_friends(self._pending_friends)
            self._pending_friends = []

    async def _on_create_room_ok(self, data: dict):
        payload = data.get("payload", {})
        room_id = payload.get("id")
        room_name = payload.get("name", "")
        is_private = payload.get("is_private", False)
        self._available_rooms[room_id] = {"name": room_name, "is_private": is_private}

        def add_to_gui(rid=room_id, rname=room_name, rpriv=is_private):
            if self._chat:
                self._chat.add_room(rid, rname, rpriv)
                self._chat.add_system_message(f"Pokój #{rname} został utworzony")

        self.after(0, add_to_gui)

    def _show_create_room_dialog(self):
        CreateRoomDialog(self, on_create=self._create_room)

    def _create_room(self, name: str, is_private: bool):
        self._run_async(self.sender.send(
            "CREATE_ROOM", {"name": name, "is_private": is_private}
        ))

    def _remove_friend(self, username: str):
        """Wysyła żądanie usunięcia znajomego z potwierdzeniem."""
        ConfirmDialog(
            self,
            title="Usuń znajomego",
            message=f"Czy na pewno chcesz usunąć {username} ze znajomych?",
            on_confirm=lambda: self._run_async(
                self.sender.send("FRIEND_REMOVE", {"username": username})
            ),
        )

    async def _on_friend_removed(self, data: dict):
        """Ktoś nas usunął ze znajomych."""
        payload = data.get("payload", {})
        username = payload.get("username", "?")
        self._friends_cache = [
            f for f in self._friends_cache if f.get("username") != username
        ]
        if self._chat:
            self.after(0, lambda: self._chat.set_friends(self._friends_cache))
            self.after(0, lambda: self._chat.add_system_message(
                f"{username} usunął Cię ze znajomych"
            ))

    def _send_invite(self, room_id: int, room_name: str, username: str):
        """Wysyła zaproszenie do prywatnego pokoju."""
        self._run_async(self.sender.send_room_invite(room_id, room_name, username))

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

    def _show_add_friend_dialog(self, username: str = None):
        AddFriendDialog(self, prefill=username, on_send=self._send_friend_request)

    def _send_friend_request(self, username: str):
        self._run_async(self.sender.send("FRIEND_REQUEST", {"username": username}))

    def _open_dm(self, username: str):
        if username in self._dm_windows:
            try:
                self._dm_windows[username].lift()
                self._dm_windows[username].focus()
                return
            except Exception:
                pass
        win = DMWindow(
            self,
            my_username=self._username,
            friend_username=username,
            on_send=lambda body: self._send_dm(username, body),
        )
        self._dm_windows[username] = win

    def _send_dm(self, target_username: str, body: str):
        self._run_async(self._async_send_dm(target_username, body))

    async def _async_send_dm(self, target_username: str, body: str):
        # Pobierz user_id odbiorcy - musimy wysłać przez send_message która oblicza HMAC
        # Ale send_message przyjmuje target_id, a my mamy tylko username
        # Rozwiązanie: rozszerz sender.send_message o target_type="dm_by_username"
        # lub stwórz nową metodę send_dm w senderze
        # Tymczasowo: używamy bezpośredniego wywołania z obliczonym HMAC
        import time
        import uuid

        msg_id = str(uuid.uuid4())
        self.sender._seq_id += 1
        seq_id = self.sender._seq_id
        ts = int(time.time() * 1000)

        hmac_val = self.sender._compute_hmac(msg_id, ts, seq_id, body)

        frame = {
            "type": "SEND_MESSAGE",
            "msg_id": msg_id,
            "ts": ts,
            "token": self.sender.token,
            "payload": {
                "target_type": "dm_by_username",
                "target_username": target_username,
                "seq_id": seq_id,
                "body": body,
                "hmac": hmac_val,
            }
        }
        await self.sender._write(frame)
        self.sender._pending_acks[msg_id] = (frame, 1, time.time())

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


class FriendRequestDialog(ctk.CTkToplevel):
    """Okno przychodzącego zaproszenia do znajomych."""

    def __init__(self, parent, from_user: str, on_accept, on_decline):
        super().__init__(parent)
        self.on_accept = on_accept
        self.on_decline = on_decline

        self.title("Zaproszenie do znajomych")
        self.geometry("340x200")
        self.resizable(False, False)
        self.grab_set()

        ctk.CTkLabel(self, text="👤  Zaproszenie do znajomych",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(pady=(24, 8))

        ctk.CTkLabel(
            self,
            text=f"{from_user} chce dodać Cię do znajomych.",
            font=ctk.CTkFont(size=13),
            text_color="#CCCCCC",
        ).pack(pady=(0, 20))

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


class AddFriendDialog(ctk.CTkToplevel):
    """Okno dodawania znajomego."""

    def __init__(self, parent, on_send, prefill: str = None):
        super().__init__(parent)
        self.on_send = on_send

        self.title("Dodaj znajomego")
        self.geometry("340x230")
        self.resizable(False, False)
        self.grab_set()

        ctk.CTkLabel(self, text="Dodaj znajomego",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(24, 4))

        ctk.CTkLabel(self, text="Podaj nazwę użytkownika:",
                     font=ctk.CTkFont(size=12),
                     text_color="#AAAAAA").pack(pady=(8, 4))

        self._entry = ctk.CTkEntry(self, placeholder_text="np. bob",
                                   height=38, font=ctk.CTkFont(size=13))
        self._entry.pack(fill="x", padx=32, pady=(0, 4))

        if prefill:
            self._entry.insert(0, prefill)

        self._error = ctk.CTkLabel(self, text="", text_color="#CC4444",
                                   font=ctk.CTkFont(size=11))
        self._error.pack()

        ctk.CTkButton(
            self, text="Wyślij zaproszenie", height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._send,
        ).pack(fill="x", padx=32, pady=(8, 20))

        self._entry.bind("<Return>", lambda e: self._send())
        self.after(100, lambda: self._entry.focus())
        if prefill:
            self._entry.select_range(0, "end")

    def _send(self):
        username = self._entry.get().strip()
        if not username:
            self._error.configure(text="Podaj nazwę użytkownika.")
            return
        self.on_send(username)
        self.destroy()


class DMWindow(ctk.CTkToplevel):
    """Okno wiadomości prywatnych (Direct Message)."""

    def __init__(self, parent, my_username: str, friend_username: str, on_send):
        super().__init__(parent)
        self.my_username = my_username
        self.friend_username = friend_username
        self.on_send = on_send

        self.title(f"DM — {friend_username}")
        self.geometry("480x520")
        self.minsize(360, 400)

        # Nagłówek
        header = ctk.CTkFrame(self, height=48, corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(header, text=f"💬  {friend_username}",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left", padx=14)

        self._status_label = ctk.CTkLabel(header, text="● online",
                                          font=ctk.CTkFont(size=11),
                                          text_color="#1D9E75")
        self._status_label.pack(side="right", padx=14)

        # Wiadomości
        self._messages = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._messages.pack(fill="both", expand=True, padx=4, pady=4)

        # Input
        input_frame = ctk.CTkFrame(self, height=54, corner_radius=0)
        input_frame.pack(fill="x")
        input_frame.pack_propagate(False)

        self._input = ctk.CTkEntry(
            input_frame,
            placeholder_text="Napisz wiadomość...",
            font=ctk.CTkFont(size=13), height=36,
        )
        self._input.pack(side="left", fill="x", expand=True, padx=(10, 4), pady=9)
        self._input.bind("<Return>", lambda e: self._send())

        ctk.CTkButton(
            input_frame, text="➤", width=40, height=36,
            font=ctk.CTkFont(size=16),
            command=self._send,
        ).pack(side="right", padx=(0, 10), pady=9)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def add_message(self, username: str, body: str, ts: int = None, own: bool = False):
        import time as t
        ts = ts or int(t.time() * 1000)
        from client.gui.widgets import MessageBubble
        bubble = MessageBubble(
            self._messages, username=username,
            body=body, ts=ts, own=own,
        )
        bubble.pack(fill="x", pady=1)
        self._messages.after(
            50, lambda: self._messages._parent_canvas.yview_moveto(1.0))

    def set_status(self, status: str):
        color = "#1D9E75" if status == "online" else "#888888"
        self._status_label.configure(
            text=f"● {status}", text_color=color)

    def _send(self):
        body = self._input.get().strip()
        if not body:
            return
        self._input.delete(0, "end")
        self.add_message(self.my_username, body, own=True)
        self.on_send(body)

    def _on_close(self):
        self.withdraw()

class ConfirmDialog(ctk.CTkToplevel):
    """Okno potwierdzenia akcji."""

    def __init__(self, parent, title: str, message: str, on_confirm):
        super().__init__(parent)
        self.on_confirm = on_confirm

        self.title(title)
        self.geometry("340x160")
        self.resizable(False, False)
        self.grab_set()

        ctk.CTkLabel(
            self, text=message,
            font=ctk.CTkFont(size=13),
            wraplength=300,
            justify="center",
        ).pack(expand=True, pady=(24, 12))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=(0, 20))

        ctk.CTkButton(
            btn_frame, text="Usuń", width=110, height=34,
            fg_color="#CC4444", hover_color="#AA2222",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._confirm,
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            btn_frame, text="Anuluj", width=110, height=34,
            fg_color="#444444", hover_color="#555555",
            font=ctk.CTkFont(size=13),
            command=self.destroy,
        ).pack(side="left", padx=8)

    def _confirm(self):
        self.on_confirm()
        self.destroy()


class CreateRoomDialog(ctk.CTkToplevel):
    """Okno tworzenia nowego pokoju (tylko admin)."""

    def __init__(self, parent, on_create):
        super().__init__(parent)
        self.on_create = on_create

        self.title("Utwórz pokój")
        self.geometry("340x240")
        self.resizable(False, False)
        self.grab_set()

        ctk.CTkLabel(self, text="Nowy pokój",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(24, 4))

        ctk.CTkLabel(self, text="Nazwa pokoju:",
                     font=ctk.CTkFont(size=12),
                     text_color="#AAAAAA").pack(pady=(8, 4))

        self._entry = ctk.CTkEntry(self, placeholder_text="np. off-topic",
                                   height=38, font=ctk.CTkFont(size=13))
        self._entry.pack(fill="x", padx=32, pady=(0, 8))

        self._private_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            self, text="Pokój prywatny",
            variable=self._private_var,
            font=ctk.CTkFont(size=12),
        ).pack(pady=(0, 4))

        self._error = ctk.CTkLabel(self, text="", text_color="#CC4444",
                                   font=ctk.CTkFont(size=11))
        self._error.pack()

        ctk.CTkButton(
            self, text="Utwórz", height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._create,
        ).pack(fill="x", padx=32, pady=(8, 20))

        self._entry.bind("<Return>", lambda e: self._create())
        self.after(100, self._entry.focus)

    def _create(self):
        name = self._entry.get().strip()
        if not name:
            self._error.configure(text="Podaj nazwę pokoju.")
            return
        if len(name) > 64:
            self._error.configure(text="Nazwa za długa (max 64 znaki).")
            return
        self.on_create(name, self._private_var.get())
        self.destroy()