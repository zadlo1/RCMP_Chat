import customtkinter as ctk
import time
from client.gui.widgets import MessageBubble, SystemMessage, RoomListItem, UserListItem, StatusBar


class ChatWindow(ctk.CTkFrame):
    """Główny widok czatu — sidebar + obszar wiadomości."""

    def __init__(self, parent, username: str, on_send, on_join_room, on_leave_room, **kwargs):
        super().__init__(parent, corner_radius=0, **kwargs)

        self.username = username
        self.on_send = on_send
        self.on_join_room = on_join_room
        self.on_leave_room = on_leave_room

        self._current_room_id = None
        self._current_room_name = None
        self._room_items: dict[int, RoomListItem] = {}

        self._build_ui()

    # ------------------------------------------------------------------
    # Budowa UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.pack(fill="both", expand=True)

        # Główny podział: sidebar + chat
        self._sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        self._chat_area = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self._chat_area.pack(side="left", fill="both", expand=True)

        self._build_sidebar()
        self._build_chat()

    def _build_sidebar(self):
        # Nagłówek
        header = ctk.CTkFrame(self._sidebar, corner_radius=0, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(header, text="RCMP Chat",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(
            side="left", padx=14, pady=14)

        # Pokoje
        ctk.CTkLabel(self._sidebar, text="POKOJE",
                     font=ctk.CTkFont(size=10), text_color="#888888").pack(
            anchor="w", padx=14, pady=(10, 2))

        self._rooms_frame = ctk.CTkScrollableFrame(
            self._sidebar, height=180, fg_color="transparent")
        self._rooms_frame.pack(fill="x", padx=4)

        # Użytkownicy
        ctk.CTkLabel(self._sidebar, text="ONLINE",
                     font=ctk.CTkFont(size=10), text_color="#888888").pack(
            anchor="w", padx=14, pady=(10, 2))

        self._users_frame = ctk.CTkScrollableFrame(
            self._sidebar, fg_color="transparent")
        self._users_frame.pack(fill="both", expand=True, padx=4)

        # Stopka z nazwą użytkownika
        footer = ctk.CTkFrame(self._sidebar, corner_radius=0, height=48)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)

        ctk.CTkLabel(footer, text=f"👤  {self.username}",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            side="left", padx=14)

    def _build_chat(self):
        # Nagłówek czatu
        self._chat_header = ctk.CTkFrame(
            self._chat_area, height=48, corner_radius=0)
        self._chat_header.pack(fill="x")
        self._chat_header.pack_propagate(False)

        self._room_label = ctk.CTkLabel(
            self._chat_header, text="Wybierz pokój",
            font=ctk.CTkFont(size=14, weight="bold"))
        self._room_label.pack(side="left", padx=14)

        self._leave_btn = ctk.CTkButton(
            self._chat_header, text="Opuść", width=70, height=28,
            fg_color="#555555", hover_color="#CC4444",
            font=ctk.CTkFont(size=12),
            command=self._leave_current_room)
        self._leave_btn.pack(side="right", padx=10)
        self._leave_btn.pack_forget()

        # Obszar wiadomości
        self._messages_frame = ctk.CTkScrollableFrame(
            self._chat_area, fg_color="transparent")
        self._messages_frame.pack(fill="both", expand=True, padx=4, pady=4)

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

        # Pasek statusu
        self._status_bar = StatusBar(self._chat_area)
        self._status_bar.pack(fill="x", side="bottom")

    # ------------------------------------------------------------------
    # Pokoje
    # ------------------------------------------------------------------

    def add_room(self, room_id: int, room_name: str, is_private: bool = False):
        """Dodaje pokój do listy w sidebarze."""
        if room_id in self._room_items:
            return
        item = RoomListItem(
            self._rooms_frame,
            room_name=room_name,
            room_id=room_id,
            is_private=is_private,
            on_click=self._join_room,
        )
        item.pack(fill="x", pady=1)
        self._room_items[room_id] = item

    def _join_room(self, room_id: int):
        if room_id == self._current_room_id:
            return
        # Wizualnie zaznacz aktywny pokój
        for rid, item in self._room_items.items():
            item.set_active(rid == room_id)
        self.on_join_room(room_id)

    def _leave_current_room(self):
        if self._current_room_id:
            self.on_leave_room(self._current_room_id)

    def set_active_room(self, room_id: int, room_name: str):
        self._current_room_id = room_id
        self._current_room_name = room_name
        self._room_label.configure(text=f"# {room_name}")
        self._leave_btn.pack(side="right", padx=10)
        for rid, item in self._room_items.items():
            item.set_active(rid == room_id)

    # ------------------------------------------------------------------
    # Wiadomości
    # ------------------------------------------------------------------

    def add_message(self, username: str, body: str, ts: int = None, own: bool = False):
        """Dodaje bąbelek wiadomości do obszaru czatu."""
        ts = ts or int(time.time() * 1000)
        bubble = MessageBubble(
            self._messages_frame,
            username=username,
            body=body,
            ts=ts,
            own=own,
        )
        bubble.pack(fill="x", pady=1)
        self._scroll_to_bottom()

    def add_system_message(self, text: str):
        """Dodaje wiadomość systemową (np. ktoś dołączył)."""
        msg = SystemMessage(self._messages_frame, text=text)
        msg.pack(pady=4)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        self._messages_frame.after(50, lambda: self._messages_frame._parent_canvas.yview_moveto(1.0))

    # ------------------------------------------------------------------
    # Użytkownicy
    # ------------------------------------------------------------------

    def set_users(self, users: list[dict]):
        """Odświeża listę użytkowników online."""
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
    # Wysyłanie
    # ------------------------------------------------------------------

    def _send(self):
        body = self._input.get().strip()
        if not body or not self._current_room_id:
            return
        self._input.delete(0, "end")
        self.on_send(self._current_room_id, body)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def set_connected(self, connected: bool):
        self._status_bar.set_connected(connected)

    def set_jwt_ttl(self, seconds: int):
        self._status_bar.set_jwt_ttl(seconds)