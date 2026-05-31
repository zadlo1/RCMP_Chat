import customtkinter as ctk
from typing import Callable


class LoginWindow(ctk.CTkToplevel):
    """Okno logowania do serwera RCMP."""

    def __init__(self, parent, on_login: Callable[[str, str], None]):
        super().__init__(parent)
        self.on_login = on_login

        self.title("RCMP Chat — Logowanie")
        self.geometry("380x460")
        self.resizable(False, False)
        self.grab_set()  # Modal

        self._build_ui()
        self.after(100, lambda: self._entry_username.focus())

    def _build_ui(self):
        # Nagłówek
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(pady=(32, 8))

        ctk.CTkLabel(
            header,
            text="RCMP Chat",
            font=ctk.CTkFont(size=28, weight="bold"),
        ).pack()

        ctk.CTkLabel(
            header,
            text="Secure Real-Time Messaging",
            font=ctk.CTkFont(size=12),
            text_color="#888888",
        ).pack(pady=(2, 0))

        # Formularz
        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=40, pady=16)

        ctk.CTkLabel(form, text="Nazwa użytkownika", anchor="w",
                     font=ctk.CTkFont(size=12)).pack(fill="x", pady=(0, 2))
        self._entry_username = ctk.CTkEntry(
            form, placeholder_text="np. alice", height=38,
            font=ctk.CTkFont(size=13)
        )
        self._entry_username.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(form, text="Hasło", anchor="w",
                     font=ctk.CTkFont(size=12)).pack(fill="x", pady=(0, 2))
        self._entry_password = ctk.CTkEntry(
            form, placeholder_text="••••••••", show="•", height=38,
            font=ctk.CTkFont(size=13)
        )
        self._entry_password.pack(fill="x", pady=(0, 4))

        # Błąd
        self._error_label = ctk.CTkLabel(
            form, text="", text_color="#CC4444",
            font=ctk.CTkFont(size=12), wraplength=300
        )
        self._error_label.pack(fill="x", pady=(4, 0))

        # Przycisk
        self._btn_login = ctk.CTkButton(
            form,
            text="Zaloguj się",
            height=42,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._submit,
        )
        self._btn_login.pack(fill="x", pady=(16, 0))

        # Info testowe
        ctk.CTkLabel(
            self,
            text="Testowi użytkownicy: alice, bob, carol, admin\nHasło: <nazwa>123",
            font=ctk.CTkFont(size=11),
            text_color="#666666",
            justify="center",
        ).pack(pady=(12, 0))

        # Enter = submit
        self._entry_username.bind("<Return>", lambda e: self._entry_password.focus())
        self._entry_password.bind("<Return>", lambda e: self._submit())

    def _submit(self):
        username = self._entry_username.get().strip()
        password = self._entry_password.get()

        if not username or not password:
            self.show_error("Podaj nazwę użytkownika i hasło.")
            return

        self._btn_login.configure(state="disabled", text="Łączenie...")
        self._error_label.configure(text="")
        self.on_login(username, password)

    def show_error(self, message: str):
        self._error_label.configure(text=message)
        self._btn_login.configure(state="normal", text="Zaloguj się")

    def close(self):
        self.destroy()