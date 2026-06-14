import customtkinter as ctk
from datetime import datetime


class MessageBubble(ctk.CTkFrame):
    """Pojedyncza wiadomość w oknie czatu."""

    def __init__(self, parent, username: str, body: str, ts: int,
                 own: bool = False, on_username_click=None, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)

        time_str = datetime.fromtimestamp(ts / 1000).strftime("%H:%M")

        anchor = "e" if own else "w"
        padx = (40, 4) if own else (4, 40)

        bubble_color = "#3B3FA6" if own else "#2B2D42"
        text_color = "#FFFFFF"

        frame = ctk.CTkFrame(self, fg_color=bubble_color, corner_radius=12)
        frame.pack(anchor=anchor, padx=padx, pady=2)

        # Pseudonim — klikalny jeśli nie własna wiadomość
        if not own and on_username_click:
            meta = ctk.CTkLabel(
                frame,
                text=f"{username}  {time_str}",
                font=ctk.CTkFont(size=10, underline=True),
                text_color="#AAAACC",
                cursor="hand2",
            )
            meta.bind("<Button-1>", lambda e: on_username_click(username))
            meta.bind("<Enter>", lambda e: meta.configure(text_color="#7777FF"))
            meta.bind("<Leave>", lambda e: meta.configure(text_color="#AAAACC"))
        else:
            meta = ctk.CTkLabel(
                frame,
                text=f"{username}  {time_str}",
                font=ctk.CTkFont(size=10),
                text_color="#AAAACC",
            )
        meta.pack(anchor="w", padx=10, pady=(6, 0))

        msg = ctk.CTkLabel(
            frame,
            text=body,
            font=ctk.CTkFont(size=13),
            text_color=text_color,
            wraplength=380,
            justify="left",
        )
        msg.pack(anchor="w", padx=10, pady=(2, 8))


class SystemMessage(ctk.CTkLabel):
    """Wiadomość systemowa (np. ktoś dołączył do pokoju)."""

    def __init__(self, parent, text: str, **kwargs):
        super().__init__(
            parent,
            text=f"— {text} —",
            font=ctk.CTkFont(size=11),
            text_color="#888888",
            **kwargs
        )


class RoomListItem(ctk.CTkFrame):
    """Element listy pokojów w sidebarze."""

    def __init__(self, parent, room_name: str, room_id: int,
                 is_private: bool = False, on_click=None, **kwargs):
        super().__init__(parent, fg_color="transparent", cursor="hand2", **kwargs)

        icon = "🔒" if is_private else "#"
        self._active = False
        self._on_click = on_click
        self._room_id = room_id

        self._label = ctk.CTkLabel(
            self,
            text=f"  {icon}  {room_name}",
            font=ctk.CTkFont(size=13),
            anchor="w",
        )
        self._label.pack(fill="x", padx=4, pady=4)

        self.bind("<Button-1>", self._click)
        self._label.bind("<Button-1>", self._click)

    def _click(self, event=None):
        if self._on_click:
            self._on_click(self._room_id)

    def set_active(self, active: bool):
        self._active = active
        color = "#3B3FA6" if active else "transparent"
        self.configure(fg_color=color)


class UserListItem(ctk.CTkFrame):
    """Element listy użytkowników online."""

    STATUS_COLORS = {
        "online": "#1D9E75",
        "away":   "#EF9F27",
        "offline": "#888888",
    }

    def __init__(self, parent, username: str, status: str = "online", **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)

        dot_color = self.STATUS_COLORS.get(status, "#888888")

        dot = ctk.CTkLabel(self, text="●", font=ctk.CTkFont(size=10),
                           text_color=dot_color, width=16)
        dot.pack(side="left", padx=(8, 2), pady=4)

        label = ctk.CTkLabel(self, text=username, font=ctk.CTkFont(size=13),
                             anchor="w")
        label.pack(side="left", fill="x", expand=True, pady=4)


class StatusBar(ctk.CTkFrame):
    """Pasek statusu na dole okna czatu."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, height=26, corner_radius=0, **kwargs)
        self.pack_propagate(False)

        self._ping_label = ctk.CTkLabel(self, text="⬤  połączono",
                                        font=ctk.CTkFont(size=11),
                                        text_color="#1D9E75")
        self._ping_label.pack(side="left", padx=12)

        self._tls_label = ctk.CTkLabel(self, text="🔒 TLS",
                                       font=ctk.CTkFont(size=11),
                                       text_color="#AAAAAA")
        self._tls_label.pack(side="left", padx=8)

        self._jwt_label = ctk.CTkLabel(self, text="",
                                       font=ctk.CTkFont(size=11),
                                       text_color="#AAAAAA")
        self._jwt_label.pack(side="right", padx=12)

    def set_tls_version(self, version: str):
        self._tls_label.configure(text=f"🔒 {version}")

    def set_connected(self, connected: bool):
        if connected:
            self._ping_label.configure(text="⬤  połączono", text_color="#1D9E75")
        else:
            self._ping_label.configure(text="⬤  rozłączono", text_color="#CC4444")

    def set_jwt_ttl(self, seconds: int):
        minutes = seconds // 60
        self._jwt_label.configure(text=f"JWT: {minutes} min")

class FriendListItem(ctk.CTkFrame):
    """Element listy znajomych."""

    STATUS_COLORS = {
        "online":  "#1D9E75",
        "away":    "#EF9F27",
        "offline": "#555555",
    }

    def __init__(self, parent, username: str, status: str = "offline",
                 on_click=None, on_remove=None, pending: bool = False, **kwargs):
        super().__init__(parent, fg_color="transparent", cursor="hand2", **kwargs)

        self._on_click = on_click
        self._on_remove = on_remove
        self._username = username

        dot_color = self.STATUS_COLORS.get(status, "#555555")

        dot = ctk.CTkLabel(self, text="●", font=ctk.CTkFont(size=10),
                           text_color=dot_color, width=16)
        dot.pack(side="left", padx=(8, 2), pady=4)

        label = ctk.CTkLabel(
            self, text=username,
            font=ctk.CTkFont(size=13),
            text_color="#AAAAAA" if status == "offline" else "#FFFFFF",
            anchor="w",
        )
        label.pack(side="left", fill="x", expand=True, pady=4)

        if pending:
            ctk.CTkLabel(self, text="oczekuje",
                         font=ctk.CTkFont(size=10),
                         text_color="#EF9F27").pack(side="right", padx=6)
        elif on_remove:
            remove_btn = ctk.CTkLabel(
                self, text="✕",
                font=ctk.CTkFont(size=11),
                text_color="#555555",
                cursor="hand2",
                width=20,
            )
            remove_btn.pack(side="right", padx=(0, 6))
            remove_btn.bind("<Button-1>", lambda e: self._remove(e))
            remove_btn.bind("<Enter>", lambda e: remove_btn.configure(text_color="#CC4444"))
            remove_btn.bind("<Leave>", lambda e: remove_btn.configure(text_color="#555555"))

        self.bind("<Button-1>", self._click)
        label.bind("<Button-1>", self._click)
        dot.bind("<Button-1>", self._click)

    def update_status(self, status: str):
        dot_color = self.STATUS_COLORS.get(status, "#555555")
        for w in self.winfo_children():
            if isinstance(w, ctk.CTkLabel) and w.cget("text") == "●":
                w.configure(text_color=dot_color)

    def _click(self, event=None):
        if self._on_click:
            self._on_click(self._username)

    def _remove(self, event=None):
        if event:
            event.widget.tk.call("break")
        if self._on_remove:
            self._on_remove(self._username)


class MemberListItem(ctk.CTkFrame):
    """Element listy uczestników pokoju (widoczny dla wszystkich)."""

    STATUS_COLORS = {
        "online":  "#1D9E75",
        "away":    "#EF9F27",
        "offline": "#888888",
    }

    def __init__(self, parent, username: str, status: str = "online",
                 role: str = "user", is_self: bool = False,
                 can_moderate: bool = False,
                 on_kick=None, on_ban=None, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)

        dot_color = self.STATUS_COLORS.get(status, "#888888")
        dot = ctk.CTkLabel(self, text="●", font=ctk.CTkFont(size=10),
                           text_color=dot_color, width=16)
        dot.pack(side="left", padx=(8, 2), pady=4)

        label_text = username + ("  (Ty)" if is_self else "")
        label = ctk.CTkLabel(
            self, text=label_text,
            font=ctk.CTkFont(size=13),
            anchor="w",
        )
        label.pack(side="left", fill="x", expand=True, pady=4)

        if role == "admin":
            ctk.CTkLabel(
                self, text="ADMIN",
                font=ctk.CTkFont(size=9, weight="bold"),
                text_color="#3B3FA6",
            ).pack(side="right", padx=(0, 8))

        if can_moderate:
            if on_ban:
                ban_btn = ctk.CTkButton(
                    self, text="Zbanuj", width=56, height=22,
                    font=ctk.CTkFont(size=10),
                    fg_color="#8B2222", hover_color="#CC4444",
                    command=lambda: on_ban(),
                )
                ban_btn.pack(side="right", padx=(4, 6))

            if on_kick:
                kick_btn = ctk.CTkButton(
                    self, text="Usuń", width=50, height=22,
                    font=ctk.CTkFont(size=10),
                    fg_color="#555555", hover_color="#CC8844",
                    command=lambda: on_kick(),
                )
                kick_btn.pack(side="right", padx=(4, 0))


class BannedUserItem(ctk.CTkFrame):
    """Element listy zbanowanych użytkowników (widoczny tylko dla admina)."""

    def __init__(self, parent, username: str, on_unban=None, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)

        ctk.CTkLabel(
            self, text=f"🚫  {username}",
            font=ctk.CTkFont(size=13),
            text_color="#AAAAAA",
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=(8, 0), pady=4)

        if on_unban:
            ctk.CTkButton(
                self, text="Odbanuj", width=64, height=22,
                font=ctk.CTkFont(size=10),
                fg_color="#555555", hover_color="#1D9E75",
                command=lambda: on_unban(),
            ).pack(side="right", padx=(4, 6))

class AdminUserListItem(ctk.CTkFrame):
    """Element listy użytkowników w panelu administratora.

    Pokazuje nazwę, status (online/offline) i rolę. Admin ma dodatkowo
    przyciski: zmiana roli (awans/degradacja) i usunięcie konta.
    """

    STATUS_COLORS = {
        "online":  "#1D9E75",
        "away":    "#EF9F27",
        "offline": "#555555",
    }

    def __init__(self, parent, user_id: int, username: str,
                 role: str = "user", status: str = "offline",
                 is_self: bool = False,
                 on_delete=None, on_toggle_role=None, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)

        self.user_id = user_id
        self.username = username
        self.role = role

        dot_color = self.STATUS_COLORS.get(status, "#555555")
        ctk.CTkLabel(self, text="●", font=ctk.CTkFont(size=10),
                     text_color=dot_color, width=16).pack(
            side="left", padx=(8, 2), pady=4)

        label_text = username + ("  (Ty)" if is_self else "")
        ctk.CTkLabel(
            self, text=label_text,
            font=ctk.CTkFont(size=13),
            anchor="w",
        ).pack(side="left", fill="x", expand=True, pady=4)

        if role == "admin":
            ctk.CTkLabel(
                self, text="ADMIN",
                font=ctk.CTkFont(size=9, weight="bold"),
                text_color="#3B3FA6",
            ).pack(side="right", padx=(0, 8))

        if is_self:
            # Nie pozwalamy administratorowi modyfikować własnego konta z panelu
            ctk.CTkLabel(
                self, text="—",
                font=ctk.CTkFont(size=11),
                text_color="#555555",
            ).pack(side="right", padx=(4, 6))
            return

        if on_delete:
            ctk.CTkButton(
                self, text="Usuń konto", width=84, height=22,
                font=ctk.CTkFont(size=10),
                fg_color="#8B2222", hover_color="#CC4444",
                command=lambda: on_delete(self.user_id, self.username),
            ).pack(side="right", padx=(4, 6))

        if on_toggle_role:
            if role == "admin":
                ctk.CTkButton(
                    self, text="Odbierz admina", width=104, height=22,
                    font=ctk.CTkFont(size=10),
                    fg_color="#555555", hover_color="#CC8844",
                    command=lambda: on_toggle_role(self.user_id, self.username, "user"),
                ).pack(side="right", padx=(4, 0))
            else:
                ctk.CTkButton(
                    self, text="Nadaj admina", width=104, height=22,
                    font=ctk.CTkFont(size=10),
                    fg_color="#555555", hover_color="#3B3FA6",
                    command=lambda: on_toggle_role(self.user_id, self.username, "admin"),
                ).pack(side="right", padx=(4, 0))


class AdminRoomListItem(ctk.CTkFrame):
    """Element listy pokojów w panelu administratora — z przyciskiem usuwania."""

    def __init__(self, parent, room_id: int, room_name: str,
                 is_private: bool = False, on_delete=None, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)

        self.room_id = room_id
        self.room_name = room_name

        icon = "🔒" if is_private else "#"
        ctk.CTkLabel(
            self, text=f"  {icon}  {room_name}",
            font=ctk.CTkFont(size=13),
            anchor="w",
        ).pack(side="left", fill="x", expand=True, pady=4)

        if on_delete:
            ctk.CTkButton(
                self, text="Usuń", width=64, height=22,
                font=ctk.CTkFont(size=10),
                fg_color="#8B2222", hover_color="#CC4444",
                command=lambda: on_delete(self.room_id, self.room_name),
            ).pack(side="right", padx=(4, 6))
