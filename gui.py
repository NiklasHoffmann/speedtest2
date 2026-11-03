"""Desktop window for the speedtest logger.

The window is a viewer and a remote control, not the measuring part. The log file
is the single source of truth: the background task writes it, this reads it. That
way closing the window never interrupts the measurement series.
"""

import logging
import os
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont, messagebox, ttk

from speedtest_logger import autostart
from speedtest_logger.config import Settings, configure_logging, load_settings
from speedtest_logger.core import STATUS_OK
from speedtest_logger.runner import run_once
from speedtest_logger.storage import LogEntry, get_text_log_path, read_log_entries

logger = logging.getLogger("gui")

REFRESH_MS = 5_000
DRAIN_MS = 250
HISTORY_ROWS = 12

BG = "#f6f6f7"
CARD = "#ffffff"
TEXT = "#1b1b1f"
MUTED = "#6c6c74"
LINE = "#e3e3e6"
OK = "#1a7f37"
WARN = "#9a6700"
BAD = "#b42318"


class App:
    """The window. Owns no measurement state beyond what it reads from the log."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.measuring = False

        self.root = tk.Tk()
        self.root.title("Speedtest Logger")
        self.root.configure(bg=BG)
        self.root.minsize(520, 620)

        self.f_label = tkfont.Font(family="Segoe UI", size=9)
        self.f_body = tkfont.Font(family="Segoe UI", size=10)
        self.f_strong = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.f_value = tkfont.Font(family="Segoe UI", size=26, weight="bold")
        self.f_mono = tkfont.Font(family="Consolas", size=9)

        self.values: dict[str, tk.Label] = {}
        self._build()
        self._refresh_from_log()
        self._schedule_refresh()
        self._drain_events()

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=BG, padx=20, pady=18)
        outer.pack(fill="both", expand=True)

        self.status_text = tk.StringVar(value="Lese Log …")
        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x")
        tk.Label(
            header, text="Speedtest Logger", font=self.f_strong, bg=BG, fg=TEXT
        ).pack(side="left")
        self.status_dot = tk.Label(header, text="●", font=self.f_body, bg=BG, fg=MUTED)
        self.status_dot.pack(side="right", padx=(8, 0))
        tk.Label(
            header, textvariable=self.status_text, font=self.f_label, bg=BG, fg=MUTED
        ).pack(side="right")

        self._build_values(outer)
        self._build_meta(outer)
        self._build_actions(outer)
        self._build_history(outer)

        footer = tk.Frame(outer, bg=BG)
        footer.pack(fill="x", pady=(14, 0))
        tk.Label(
            footer,
            text=f"Intervall {self.settings.interval}s   ·   {self.settings.log_dir}",
            font=self.f_label,
            bg=BG,
            fg=MUTED,
            wraplength=460,
            justify="left",
        ).pack(side="left")

    def _build_values(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=CARD, padx=18, pady=16)
        card.pack(fill="x", pady=(16, 10))

        specs = [
            ("download", "Download", "Mbit/s"),
            ("upload", "Upload", "Mbit/s"),
            ("ping", "Ping", "ms"),
            ("loss", "Paketverlust", "%"),
        ]
        for column, (key, caption, unit) in enumerate(specs):
            cell = tk.Frame(card, bg=CARD)
            cell.grid(row=0, column=column, sticky="w", padx=(0, 18))
            value = tk.Label(cell, text="–", font=self.f_value, bg=CARD, fg=TEXT)
            value.pack(anchor="w")
            tk.Label(
                cell, text=f"{caption} ({unit})", font=self.f_label, bg=CARD, fg=MUTED
            ).pack(anchor="w")
            self.values[key] = value
            card.grid_columnconfigure(column, weight=1)

    def _build_meta(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=CARD, padx=18, pady=14)
        card.pack(fill="x", pady=(0, 10))

        self.meta_server = tk.StringVar(value="–")
        self.meta_time = tk.StringVar(value="–")
        self.meta_verdict = tk.StringVar(value="–")

        rows = [
            ("Server", self.meta_server),
            ("Letzte Messung", self.meta_time),
            ("Bewertung", self.meta_verdict),
        ]
        for row, (caption, variable) in enumerate(rows):
            tk.Label(card, text=caption, font=self.f_label, bg=CARD, fg=MUTED).grid(
                row=row, column=0, sticky="w", pady=2
            )
            label = tk.Label(
                card,
                textvariable=variable,
                font=self.f_body,
                bg=CARD,
                fg=TEXT,
                anchor="w",
                justify="left",
                wraplength=340,
            )
            label.grid(row=row, column=1, sticky="w", padx=(16, 0), pady=2)
            if caption == "Bewertung":
                self.verdict_label = label
        card.grid_columnconfigure(1, weight=1)

    def _build_actions(self, parent: tk.Frame) -> None:
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", pady=(0, 12))

        self.measure_button = ttk.Button(
            bar, text="Jetzt messen", command=self._measure_now
        )
        self.measure_button.pack(side="left")

        self.background_button = ttk.Button(bar, text="…", command=self._toggle_background)
        self.background_button.pack(side="left", padx=(8, 0))

        self.autostart_button = ttk.Button(bar, text="…", command=self._toggle_autostart)
        self.autostart_button.pack(side="left", padx=(8, 0))

        ttk.Button(bar, text="Log-Ordner", command=self._open_log_dir).pack(
            side="right"
        )
        self._refresh_control_buttons()

    def _build_history(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=CARD, padx=2, pady=2)
        card.pack(fill="both", expand=True)

        self.history = tk.Listbox(
            card,
            font=self.f_mono,
            bg=CARD,
            fg=TEXT,
            borderwidth=0,
            highlightthickness=0,
            activestyle="none",
            selectbackground=LINE,
            selectforeground=TEXT,
            height=HISTORY_ROWS,
        )
        self.history.pack(side="left", fill="both", expand=True, padx=14, pady=12)
        scrollbar = ttk.Scrollbar(card, orient="vertical", command=self.history.yview)
        scrollbar.pack(side="right", fill="y")
        self.history.configure(yscrollcommand=scrollbar.set)

    # ------------------------------------------------------------------ actions

    def _measure_now(self) -> None:
        if self.measuring:
            return

        self.measuring = True
        self.measure_button.configure(state="disabled", text="Messung läuft …")
        self.status_text.set("Messung läuft …")
        self.status_dot.configure(fg=WARN)

        def work() -> None:
            try:
                result = run_once(self.settings)
                self.events.put(("done", result))
            except Exception as error:  # noqa: BLE001 - must not kill the window
                self.events.put(("failed", str(error)))

        threading.Thread(target=work, daemon=True).start()

    def _toggle_autostart(self) -> None:
        try:
            if autostart.autostart_enabled():
                if not messagebox.askyesno(
                    "Autostart entfernen",
                    "Die Verknüpfung im Autostart-Ordner wird gelöscht. Nach der "
                    "nächsten Anmeldung wird dann nicht mehr automatisch gemessen. "
                    "Fortfahren?",
                ):
                    return
                autostart.disable_autostart()
            else:
                autostart.enable_autostart()
                messagebox.showinfo(
                    "Autostart eingerichtet",
                    "Der Logger startet künftig bei jeder Anmeldung.\n\n"
                    f"{autostart.read_autostart_target()}",
                )
        except autostart.AutostartError as error:
            messagebox.showerror("Autostart", str(error))
        finally:
            self._refresh_control_buttons()

    def _toggle_background(self) -> None:
        try:
            if autostart.background_pid(self.settings) is not None:
                autostart.stop_background(self.settings)
                self.status_text.set("Logger gestoppt")
            else:
                autostart.start_background(self.settings)
                self.status_text.set("Logger gestartet")
        except autostart.AutostartError as error:
            messagebox.showerror("Hintergrundprozess", str(error))
        finally:
            self._refresh_control_buttons()

    def _open_log_dir(self) -> None:
        self.settings.log_dir.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(self.settings.log_dir)  # noqa: S606 - Windows only
        else:
            messagebox.showinfo("Log-Ordner", str(self.settings.log_dir))

    def _refresh_control_buttons(self) -> None:
        running = autostart.background_pid(self.settings) is not None
        self.background_button.configure(
            text="Logger stoppen" if running else "Logger starten"
        )
        self.autostart_button.configure(
            text="Autostart aus" if autostart.autostart_enabled() else "Autostart ein"
        )

    # ------------------------------------------------------------------ updates

    def _schedule_refresh(self) -> None:
        self.root.after(REFRESH_MS, self._tick_refresh)

    def _tick_refresh(self) -> None:
        self._refresh_from_log()
        self._refresh_control_buttons()
        self._schedule_refresh()

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "failed":
                    messagebox.showerror("Messung fehlgeschlagen", str(payload))
                elif kind == "done" and payload is None:
                    self.status_text.set("Übersprungen – es wird bereits gemessen")
                self.measuring = False
                self.measure_button.configure(state="normal", text="Jetzt messen")
                self._refresh_from_log()
        except queue.Empty:
            pass
        self.root.after(DRAIN_MS, self._drain_events)

    def _refresh_from_log(self) -> None:
        entries = read_log_entries(self.settings)
        measurements = [entry for entry in entries if entry.kind == "measurement"]

        if measurements:
            self._show_measurement(measurements[-1])
        elif not entries:
            self.status_text.set(f"Kein Log für heute in {get_text_log_path(self.settings).name}")
            self.status_dot.configure(fg=MUTED)

        self._show_history(entries)
        if not self.measuring and measurements:
            self._show_liveness(measurements[-1])

    def _show_measurement(self, entry: LogEntry) -> None:
        def text(value: float | None, digits: int = 2) -> str:
            return "–" if value is None else f"{value:.{digits}f}"

        self.values["download"].configure(text=text(entry.download_mbit))
        self.values["upload"].configure(text=text(entry.upload_mbit))
        self.values["ping"].configure(text=text(entry.ping_ms, 1))
        self.values["loss"].configure(text=text(entry.packet_loss_pct, 0))

        self.meta_server.set(entry.server or "–")
        self.meta_time.set(
            entry.taken_at.strftime("%d.%m.%Y %H:%M:%S") if entry.taken_at else "–"
        )

        meets = entry.status == STATUS_OK
        self.meta_verdict.set(
            "erfüllt die Mindestwerte"
            if meets
            else f"unter Mindestwert ({self.settings.min_download}/{self.settings.min_upload} Mbit/s)"
        )
        self.verdict_label.configure(fg=OK if meets else BAD)

    def _show_liveness(self, latest: LogEntry) -> None:
        if latest.taken_at is None:
            return

        running = autostart.background_pid(self.settings) is not None
        age = (datetime.now() - latest.taken_at).total_seconds()

        if running and age <= self.settings.interval * 2:
            self.status_text.set("Messreihe läuft")
            self.status_dot.configure(fg=OK)
        elif running:
            self.status_text.set("Logger läuft, wartet auf die nächste Messung")
            self.status_dot.configure(fg=OK)
        else:
            minutes = int(age // 60)
            self.status_text.set(f"Logger aus · letzte Messung vor {minutes} min")
            self.status_dot.configure(fg=WARN)

    def _show_history(self, entries: list[LogEntry]) -> None:
        selection = self.history.yview()
        self.history.delete(0, "end")

        for entry in entries[-60:]:
            stamp = entry.taken_at.strftime("%H:%M") if entry.taken_at else "--:--"
            if entry.kind == "measurement":
                line = (
                    f"{stamp}  {entry.download_mbit or 0:7.2f} / {entry.upload_mbit or 0:6.2f}"
                    f"   {entry.ping_ms or 0:5.1f} ms"
                )
            elif entry.kind == "average":
                short = "Stunde" if entry.label.startswith("HOURLY") else "Tag"
                line = (
                    f"{stamp}  {short}:  {entry.download_mbit or 0:7.2f} / "
                    f"{entry.upload_mbit or 0:6.2f}"
                )
            else:
                line = f"{stamp}  Fehler"
            self.history.insert("end", line)

            index = self.history.size() - 1
            if entry.kind == "error":
                self.history.itemconfigure(index, foreground=BAD)
            elif entry.kind == "average":
                self.history.itemconfigure(index, foreground=MUTED)

        if selection and selection[1] >= 0.999:
            self.history.yview_moveto(1.0)

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    settings = load_settings()
    configure_logging(settings, console=False)
    logger.info("Window started, reading %s", settings.log_dir)
    App(settings).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
