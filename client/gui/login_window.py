import customtkinter as ctk
from typing import Callable


class LoginWindow(ctk.CTkToplevel):
    """Okno logowania / rejestracji."""

    def __init__(
        self,
        parent,
        on_login: Callable[[str, str], None],
        on_register: Callable[[str, str], None],
    ):
        super().__init__(parent)
        self.on_login = on_login
        self.on_register = on_register
        self._mode = "login"  # "login" | "register"

        self.title("RCMP Chat")
        self.geometry("400x500")
        self.resizable(False, False)
        self.grab_set()

        self._build_ui()
        self.after(100, lambda: self._entry_username.focus())

    # ------------------------------------------------------------------
    # Budowa UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Nagłówek
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(pady=(28, 0))

        ctk.CTkLabel(
            header, text="RCMP Chat",
            font=ctk.CTkFont(size=26, weight="bold"),
        ).pack()

        ctk.CTkLabel(
            header, text="Secure Real-Time Messaging",
            font=ctk.CTkFont(size=12),
            text_color="#888888",
        ).pack(pady=(2, 0))

        # Przełącznik Login / Rejestracja
        tab_frame = ctk.CTkFrame(self, fg_color="transparent")
        tab_frame.pack(pady=(18, 0))

        self._btn_tab_login = ctk.CTkButton(
            tab_frame, text="Logowanie", width=150, height=34,
            font=ctk.CTkFont(size=13),
            command=self._switch_login,
        )
        self._btn_tab_login.pack(side="left", padx=(0, 4))

        self._btn_tab_register = ctk.CTkButton(
            tab_frame, text="Rejestracja", width=150, height=34,
            font=ctk.CTkFont(size=13),
            fg_color="#444444",
            command=self._switch_register,
        )
        self._btn_tab_register.pack(side="left")

        # Formularz
        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=40, pady=16)

        ctk.CTkLabel(form, text="Nazwa użytkownika", anchor="w",
                     font=ctk.CTkFont(size=12)).pack(fill="x", pady=(0, 2))
        self._entry_username = ctk.CTkEntry(
            form, placeholder_text="np. alice", height=38,
            font=ctk.CTkFont(size=13),
        )
        self._entry_username.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(form, text="Hasło", anchor="w",
                     font=ctk.CTkFont(size=12)).pack(fill="x", pady=(0, 2))
        self._entry_password = ctk.CTkEntry(
            form, placeholder_text="••••••••", show="•", height=38,
            font=ctk.CTkFont(size=13),
        )
        self._entry_password.pack(fill="x", pady=(0, 6))

        # Pole potwierdzenia hasła (tylko rejestracja)
        self._lbl_confirm = ctk.CTkLabel(form, text="Potwierdź hasło", anchor="w",
                                          font=ctk.CTkFont(size=12))
        self._entry_confirm = ctk.CTkEntry(
            form, placeholder_text="••••••••", show="•", height=38,
            font=ctk.CTkFont(size=13),
        )

        # Komunikat błędu / sukcesu
        self._msg_label = ctk.CTkLabel(
            form, text="", font=ctk.CTkFont(size=12),
            wraplength=300,
        )
        self._msg_label.pack(fill="x", pady=(4, 0))

        # Przycisk akcji
        self._btn_action = ctk.CTkButton(
            form, text="Zaloguj się", height=42,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._submit,
        )
        self._btn_action.pack(fill="x", pady=(14, 0))

        # Podpowiedź testowa
        self._hint_label = ctk.CTkLabel(
            self,
            text="Testowi użytkownicy: alice, bob, carol, admin\nHasło: <nazwa>123",
            font=ctk.CTkFont(size=11),
            text_color="#666666",
            justify="center",
        )
        self._hint_label.pack(pady=(10, 0))

        # Bindings
        self._entry_username.bind("<Return>", lambda e: self._entry_password.focus())
        self._entry_password.bind("<Return>", lambda e: (
            self._entry_confirm.focus() if self._mode == "register" else self._submit()
        ))
        self._entry_confirm.bind("<Return>", lambda e: self._submit())

    # ------------------------------------------------------------------
    # Przełączanie trybów
    # ------------------------------------------------------------------

    def _switch_login(self):
        self._mode = "login"
        self._btn_tab_login.configure(fg_color=["#3B8ED0", "#1F6AA5"])
        self._btn_tab_register.configure(fg_color="#444444")
        self._btn_action.configure(text="Zaloguj się")
        self._lbl_confirm.pack_forget()
        self._entry_confirm.pack_forget()
        self._hint_label.pack(pady=(10, 0))
        self.geometry("400x500")
        self._clear_msg()

    def _switch_register(self):
        self._mode = "register"
        self._btn_tab_register.configure(fg_color=["#3B8ED0", "#1F6AA5"])
        self._btn_tab_login.configure(fg_color="#444444")
        self._btn_action.configure(text="Utwórz konto")
        # Wstaw pola potwierdzenia przed przyciskiem
        self._lbl_confirm.pack(fill="x", pady=(0, 2),
                                before=self._msg_label)
        self._entry_confirm.pack(fill="x", pady=(0, 6),
                                  before=self._msg_label)
        self._hint_label.pack_forget()
        self.geometry("400x540")
        self._clear_msg()

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    def _submit(self):
        username = self._entry_username.get().strip()
        password = self._entry_password.get()

        if not username or not password:
            self.show_error("Podaj nazwę użytkownika i hasło.")
            return

        if self._mode == "register":
            confirm = self._entry_confirm.get()
            if password != confirm:
                self.show_error("Hasła nie są identyczne.")
                return
            if len(password) < 6:
                self.show_error("Hasło musi mieć co najmniej 6 znaków.")
                return
            self._btn_action.configure(state="disabled", text="Tworzenie konta...")
            self._clear_msg()
            self.on_register(username, password)
        else:
            self._btn_action.configure(state="disabled", text="Łączenie...")
            self._clear_msg()
            self.on_login(username, password)

    # ------------------------------------------------------------------
    # Komunikaty
    # ------------------------------------------------------------------

    def show_error(self, message: str):
        self._msg_label.configure(text=message, text_color="#CC4444")
        action_text = "Zaloguj się" if self._mode == "login" else "Utwórz konto"
        self._btn_action.configure(state="normal", text=action_text)

    def show_success(self, message: str):
        self._msg_label.configure(text=message, text_color="#1D9E75")
        action_text = "Zaloguj się" if self._mode == "login" else "Utwórz konto"
        self._btn_action.configure(state="normal", text=action_text)

    def _clear_msg(self):
        self._msg_label.configure(text="")

    def close(self):
        self.destroy()