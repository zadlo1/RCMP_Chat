import customtkinter as ctk
import time
from client.gui.widgets import MessageBubble, SystemMessage, RoomListItem, UserListItem, StatusBar, FriendListItem


class ChatWindow(ctk.CTkFrame):

    def __init__(self, parent, username: str, on_send, on_join_room,
                 on_leave_room, on_add_friend=None, on_open_dm=None,
                 on_remove_friend=None, is_admin=False, on_create_room=None,
                 on_view_members=None, **kwargs):
        super().__init__(parent, corner_radius=0, **kwargs)

        self.username = username
        self.on_send = on_send
        self.on_join_room = on_join_room
        self.on_leave_room = on_leave_room
        self.on_add_friend = on_add_friend
        self.on_open_dm = on_open_dm
        self.on_remove_friend = on_remove_friend
        self.is_admin = is_admin
        self.on_create_room = on_create_room
        self.on_view_members = on_view_members
        self._friend_items: dict[str, object] = {}

        self._current_room_id = None
        self._current_room_name = None
        self._room_items: dict[int, RoomListItem] = {}

        # Historia wiadomości per pokój: {room_id: [(username, body, ts, own)]}
        self._room_histories: dict[int, list] = {}

        self._build_ui()

    # ------------------------------------------------------------------
    # Budowa UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.pack(fill="both", expand=True)

        self._sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        self._chat_area = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self._chat_area.pack(side="left", fill="both", expand=True)

        self._build_sidebar()
        self._build_chat()

    def _build_sidebar(self):
        header = ctk.CTkFrame(self._sidebar, corner_radius=0, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(header, text="RCMP Chat",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(
            side="left", padx=14, pady=14)

        rooms_hdr = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        rooms_hdr.pack(fill="x", padx=14, pady=(10, 2))

        ctk.CTkLabel(rooms_hdr, text="POKOJE",
                     font=ctk.CTkFont(size=10),
                     text_color="#888888").pack(side="left")

        if self.is_admin:
            ctk.CTkButton(
                rooms_hdr, text="+", width=24, height=20,
                font=ctk.CTkFont(size=14, weight="bold"),
                fg_color="#3B3FA6", hover_color="#5558CC",
                command=lambda: self.on_create_room() if self.on_create_room else None,
            ).pack(side="right")

        self._rooms_frame = ctk.CTkScrollableFrame(
            self._sidebar, height=180, fg_color="transparent")
        self._rooms_frame.pack(fill="x", padx=4)

        ctk.CTkLabel(self._sidebar, text="ONLINE",
                     font=ctk.CTkFont(size=10), text_color="#888888").pack(
            anchor="w", padx=14, pady=(10, 2))

        # Nagłówek ZNAJOMI z przyciskiem +
        friends_header = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        friends_header.pack(fill="x", padx=14, pady=(10, 2))

        ctk.CTkLabel(friends_header, text="ZNAJOMI",
                     font=ctk.CTkFont(size=10),
                     text_color="#888888").pack(side="left")

        ctk.CTkButton(
            friends_header, text="+", width=24, height=20,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#3B3FA6", hover_color="#5558CC",
            command=lambda: self.on_add_friend(None) if self.on_add_friend else None,
        ).pack(side="right")

        self._friends_frame = ctk.CTkScrollableFrame(
            self._sidebar, fg_color="transparent")
        self._friends_frame.pack(fill="both", expand=True, padx=4)

        footer = ctk.CTkFrame(self._sidebar, corner_radius=0, height=48)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)

        ctk.CTkLabel(footer, text=f"👤  {self.username}",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            side="left", padx=14)

    def _build_chat(self):
        # Nagłówek
        self._chat_header = ctk.CTkFrame(self._chat_area, height=48, corner_radius=0)
        self._chat_header.pack(fill="x")
        self._chat_header.pack_propagate(False)

        self._room_label = ctk.CTkLabel(
            self._chat_header, text="Brak pokoju",
            font=ctk.CTkFont(size=14, weight="bold"))
        self._room_label.pack(side="left", padx=14)

        self._leave_btn = ctk.CTkButton(
            self._chat_header, text="Opuść", width=70, height=28,
            fg_color="#555555", hover_color="#CC4444",
            font=ctk.CTkFont(size=12),
            command=self._leave_current_room)
        self._leave_btn.pack(side="right", padx=10)
        self._leave_btn.pack_forget()

        self._members_btn = ctk.CTkButton(
            self._chat_header, text="👥 Uczestnicy", width=110, height=28,
            fg_color="#555555", hover_color="#3B3FA6",
            font=ctk.CTkFont(size=12),
            command=self._view_members)
        self._members_btn.pack(side="right", padx=(10, 0))
        self._members_btn.pack_forget()

        # Obszar wiadomości — stack: placeholder + scrollable
        self._content_frame = ctk.CTkFrame(self._chat_area, fg_color="transparent")
        self._content_frame.pack(fill="both", expand=True)

        # Placeholder "brak pokoju"
        self._placeholder = ctk.CTkFrame(self._content_frame, fg_color="transparent")
        self._placeholder.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            self._placeholder,
            text="💬",
            font=ctk.CTkFont(size=52),
        ).pack()

        ctk.CTkLabel(
            self._placeholder,
            text="Nie jesteś w żadnym pokoju",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(pady=(8, 4))

        ctk.CTkLabel(
            self._placeholder,
            text="Wybierz pokój z listy po lewej stronie,\naby rozpocząć rozmowę.",
            font=ctk.CTkFont(size=13),
            text_color="#888888",
            justify="center",
        ).pack()

        # Scrollable frame na wiadomości (początkowo ukryty)
        self._messages_frame = ctk.CTkScrollableFrame(
            self._content_frame, fg_color="transparent")

        # Pole wpisywania
        input_frame = ctk.CTkFrame(self._chat_area, height=54, corner_radius=0)
        input_frame.pack(fill="x", side="bottom")
        input_frame.pack_propagate(False)

        self._input = ctk.CTkEntry(
            input_frame,
            placeholder_text="Napisz wiadomość... (Enter aby wysłać)",
            font=ctk.CTkFont(size=13),
            height=36,
        )
        self._input.pack(side="left", fill="x", expand=True, padx=(10, 4), pady=9)
        self._input.bind("<Return>", lambda e: self._send())

        send_btn = ctk.CTkButton(
            input_frame, text="➤", width=40, height=36,
            font=ctk.CTkFont(size=16),
            command=self._send)
        send_btn.pack(side="right", padx=(0, 10), pady=9)

        self._status_bar = StatusBar(self._chat_area)
        self._status_bar.pack(fill="x", side="bottom")

    # ------------------------------------------------------------------
    # Pokoje
    # ------------------------------------------------------------------

    def add_room(self, room_id: int, room_name: str, is_private: bool = False):
        if room_id in self._room_items:
            return
        self._room_histories[room_id] = []
        item = RoomListItem(
            self._rooms_frame,
            room_name=room_name,
            room_id=room_id,
            is_private=is_private,
            on_click=self._join_room,
        )
        item.pack(fill="x", pady=1)
        self._room_items[room_id] = item

    def remove_room(self, room_id: int):
        """Usuwa pokój z listy w sidebarze (np. po zbanowaniu)."""
        item = self._room_items.pop(room_id, None)
        if item:
            item.destroy()
        self._room_histories.pop(room_id, None)

    def _join_room(self, room_id: int):
        if room_id == self._current_room_id:
            return
        for rid, item in self._room_items.items():
            item.set_active(rid == room_id)
        self.on_join_room(room_id)

    def _leave_current_room(self):
        if self._current_room_id:
            self.on_leave_room(self._current_room_id)

    def _view_members(self):
        if self._current_room_id is not None and self.on_view_members:
            self.on_view_members(self._current_room_id)

    def set_active_room(self, room_id: int, room_name: str):
        self._current_room_id = room_id
        self._current_room_name = room_name
        self._room_label.configure(text=f"# {room_name}")
        self._leave_btn.pack(side="right", padx=10)
        self._members_btn.pack(side="right", padx=(10, 0))

        # Ukryj placeholder, pokaż wiadomości
        self._placeholder.place_forget()
        self._messages_frame.pack(fill="both", expand=True, padx=4, pady=4)

        for rid, item in self._room_items.items():
            item.set_active(rid == room_id)

        # Załaduj historię tego pokoju
        self._reload_messages()

    def room_left(self, room_id: int):
        """Wywołaj gdy użytkownik opuści pokój."""
        self._current_room_id = None
        self._current_room_name = None
        self._room_label.configure(text="Brak pokoju")
        self._leave_btn.pack_forget()
        self._members_btn.pack_forget()

        # Pokaż placeholder, ukryj wiadomości
        self._messages_frame.pack_forget()
        self._placeholder.place(relx=0.5, rely=0.5, anchor="center")

        for rid, item in self._room_items.items():
            item.set_active(False)

    # ------------------------------------------------------------------
    # Wiadomości
    # ------------------------------------------------------------------

    def _reload_messages(self):
        """Czyści obszar czatu i ładuje historię aktualnego pokoju."""
        for widget in self._messages_frame.winfo_children():
            widget.destroy()

        if self._current_room_id not in self._room_histories:
            return

        for entry in self._room_histories[self._current_room_id]:
            kind = entry["kind"]
            if kind == "message":
                self._render_bubble(
                    entry["username"], entry["body"],
                    entry["ts"], entry["own"]
                )
            elif kind == "system":
                self._render_system(entry["text"])
            elif kind == "failed":
                self._render_failed(entry["body"])

        self._scroll_to_bottom()

    def add_message(self, username: str, body: str, ts: int = None, own: bool = False):
        ts = ts or int(time.time() * 1000)

        # Zapisz do historii pokoju
        if self._current_room_id is not None:
            self._room_histories.setdefault(self._current_room_id, []).append({
                "kind": "message",
                "username": username,
                "body": body,
                "ts": ts,
                "own": own,
            })
            self._render_bubble(username, body, ts, own)
            self._scroll_to_bottom()

    def add_system_message(self, text: str, room_id: int = None):
        target = room_id or self._current_room_id
        if target is not None:
            self._room_histories.setdefault(target, []).append({
                "kind": "system",
                "text": text,
            })
        if target == self._current_room_id:
            self._render_system(text)
            self._scroll_to_bottom()

    def add_failed_message(self, body: str):
        """Dodaje wiadomość która nie została wysłana (brak pokoju)."""
        if self._current_room_id is not None:
            self._room_histories.setdefault(self._current_room_id, []).append({
                "kind": "failed",
                "body": body,
            })
            self._render_failed(body)
            self._scroll_to_bottom()

    def _render_bubble(self, username: str, body: str, ts: int, own: bool):
        bubble = MessageBubble(
            self._messages_frame,
            username=username,
            body=body,
            ts=ts,
            own=own,
            on_username_click=self._on_username_click if not own else None,
        )
        bubble.pack(fill="x", pady=1)

    def _on_username_click(self, username: str):
        """Kliknięcie pseudonimu w czacie — opcja dodania do znajomych."""
        if username == self.username:
            return
        if self.on_add_friend:
            self.on_add_friend(username)

    def _render_system(self, text: str):
        msg = SystemMessage(self._messages_frame, text=text)
        msg.pack(pady=4)

    def _render_failed(self, body: str):
        """Wiadomość niewyslana — szare tło z ikoną błędu."""
        frame = ctk.CTkFrame(
            self._messages_frame,
            fg_color="#4A2020",
            corner_radius=10,
        )
        frame.pack(anchor="e", padx=(40, 4), pady=2, fill="x")

        ctk.CTkLabel(
            frame,
            text="⚠  Nie wysłano — opuściłeś pokój",
            font=ctk.CTkFont(size=10),
            text_color="#FF8888",
        ).pack(anchor="e", padx=10, pady=(6, 0))

        ctk.CTkLabel(
            frame,
            text=body,
            font=ctk.CTkFont(size=13),
            text_color="#888888",
            wraplength=380,
            justify="left",
        ).pack(anchor="w", padx=10, pady=(2, 8))

    def _scroll_to_bottom(self):
        self._messages_frame.after(
            50, lambda: self._messages_frame._parent_canvas.yview_moveto(1.0))

    # ------------------------------------------------------------------
    # Użytkownicy
    # ------------------------------------------------------------------

    def set_users(self, users: list[dict]):
        for widget in self._users_frame.winfo_children():
            widget.destroy()
        for user in users:
            item = UserListItem(
                self._users_frame,
                username=user["username"],
                status=user.get("status", "online"),
            )
            item.pack(fill="x", pady=1)

    # ------------------------------------------------------------------
    # Znajomi
    # ------------------------------------------------------------------

    def set_friends(self, friends: list):
        """Odświeża listę znajomych."""
        print(f"[CHAT_WINDOW] set_friends wywołane z {len(friends)} znajomymi")
        for widget in self._friends_frame.winfo_children():
            widget.destroy()
        self._friend_items.clear()

        if not friends:
            ctk.CTkLabel(self._friends_frame, text="Brak znajomych",
                         font=ctk.CTkFont(size=11),
                         text_color="#555555").pack(pady=8)
            return

        for f in friends:
            uname = f["username"]
            status = f.get("status", "offline")
            pending = f.get("friendship_status") == "pending"
            print(f"[CHAT_WINDOW] Dodawanie znajomego: {uname}, status={status}, pending={pending}")
            item = FriendListItem(
                self._friends_frame,
                username=uname,
                status=status,
                pending=pending,
                on_click=self._on_friend_click,
                on_remove=self._on_friend_remove if not pending else None,
            )
            item.pack(fill="x", pady=1)
            self._friend_items[uname] = item
        print(f"[CHAT_WINDOW] Dodano {len(self._friend_items)} znajomych do GUI")

    def update_friend_status(self, username: str, status: str):
        item = self._friend_items.get(username)
        if item:
            item.update_status(status)

    def _on_friend_click(self, username: str):
        if self.on_open_dm:
            self.on_open_dm(username)

    def _on_friend_remove(self, username: str):
        if self.on_remove_friend:
            self.on_remove_friend(username)

    # ------------------------------------------------------------------
    # Wysyłanie
    # ------------------------------------------------------------------

    def _send(self):
        body = self._input.get().strip()
        if not body:
            return

        if not self._current_room_id:
            # Pokaż placeholder z animacją — nie ma pokoju
            self._flash_no_room()
            return

        self._input.delete(0, "end")
        self.on_send(self._current_room_id, body)

    def _flash_no_room(self):
        """Podświetla placeholder żeby zwrócić uwagę."""
        original = self._room_label.cget("text")
        self._room_label.configure(text="⚠  Wybierz pokój!", text_color="#FF6666")
        self.after(2000, lambda: self._room_label.configure(
            text=original, text_color="white"))

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def set_connected(self, connected: bool):
        self._status_bar.set_connected(connected)

    def set_jwt_ttl(self, seconds: int):
        self._status_bar.set_jwt_ttl(seconds)

    def set_tls_version(self, version: str):
        self._status_bar.set_tls_version(version)