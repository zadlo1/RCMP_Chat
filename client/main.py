from client.gui.app import RCMPApp


def main():
    app = RCMPApp()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()


if __name__ == "__main__":
    main()