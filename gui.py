"""
Graphical interface for the Epson L3150-family waste ink counter tool.

    .venv\\Scripts\\python gui.py

Find the printer on the network (or type its IP address), read the counters,
then set the main counter, do a full reset, or restore a backup. The same
safety rules as the command line tool apply: the serial number check must
pass, a backup is saved before every change, and nothing is written without
confirmation.
"""

from __future__ import annotations

import logging
import pathlib
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import waste_ink
from waste_ink import Plan, Stopped, WriteFailed

PAD_WARNING = ("Resetting changes the counter only. The waste ink pads stay as full as they are: "
               "replace them or fit an external waste ink tank.")


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Epson waste ink counters")
        self.minsize(640, 600)
        self.printer = None
        self.serial: str | None = None
        self.busy = False
        self.events: queue.Queue = queue.Queue()
        self._build()
        self._refresh_buttons()
        self.after(100, self._poll)

    # Layout

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}

        found = ttk.LabelFrame(self, text="1. Printer")
        found.pack(fill="x", **pad)
        self.tree = ttk.Treeview(found, columns=("host", "model", "name"), show="headings", height=4)
        for col, title, width in (("host", "IP address", 130), ("model", "Model", 220), ("name", "Name", 120)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="x", padx=8, pady=(8, 4))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._refresh_buttons())
        self.tree.bind("<Double-1>", lambda e: self.read_counters())

        row = ttk.Frame(found)
        row.pack(fill="x", padx=8, pady=(0, 8))
        self.scan_button = ttk.Button(row, text="Scan network", command=self.scan)
        self.scan_button.pack(side="left")
        self.scan_progress = ttk.Progressbar(row, length=140, mode="determinate")
        self.scan_progress.pack(side="left", padx=8)
        ttk.Label(row, text="or IP address:").pack(side="left", padx=(16, 4))
        self.manual_ip = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.manual_ip, width=16)
        entry.pack(side="left")
        entry.bind("<Return>", lambda e: self.add_manual())
        self.add_button = ttk.Button(row, text="Add", command=self.add_manual)
        self.add_button.pack(side="left", padx=4)

        counters = ttk.LabelFrame(self, text="2. Counters")
        counters.pack(fill="x", **pad)
        self.read_button = ttk.Button(counters, text="Read counters", command=self.read_counters)
        self.read_button.grid(row=0, column=0, sticky="w", padx=8, pady=8)
        self.identity = ttk.Label(counters, text="No printer read yet.", justify="left")
        self.identity.grid(row=0, column=1, columnspan=2, sticky="w", padx=8)

        self.bars, self.percents = {}, {}
        for i, (key, title) in enumerate((("main_waste", "Main waste ink"), ("borderless_waste", "Borderless waste ink")), 1):
            ttk.Label(counters, text=title).grid(row=i, column=0, sticky="w", padx=8, pady=2)
            self.bars[key] = ttk.Progressbar(counters, length=320, maximum=100)
            self.bars[key].grid(row=i, column=1, sticky="w", padx=8)
            self.percents[key] = ttk.Label(counters, text="-", width=9)
            self.percents[key].grid(row=i, column=2, sticky="w")

        actions = ttk.LabelFrame(self, text="3. Change")
        actions.pack(fill="x", **pad)
        row = ttk.Frame(actions)
        row.pack(fill="x", padx=8, pady=8)
        ttk.Label(row, text="Set main counter to").pack(side="left")
        self.target = tk.StringVar(value="50")
        ttk.Spinbox(row, from_=0, to=100, increment=5, textvariable=self.target, width=5).pack(side="left", padx=4)
        ttk.Label(row, text="%").pack(side="left")
        self.set_button = ttk.Button(row, text="Apply…", command=self.set_main)
        self.set_button.pack(side="left", padx=8)
        self.reset_button = ttk.Button(row, text="Full reset…", command=self.full_reset)
        self.reset_button.pack(side="left", padx=8)
        self.restore_button = ttk.Button(row, text="Restore backup…", command=self.restore)
        self.restore_button.pack(side="left", padx=8)
        ttk.Label(actions, text=PAD_WARNING, wraplength=600, foreground="#a15c00").pack(fill="x", padx=8, pady=(0, 8))

        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

    # Helpers

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def selected_host(self) -> str | None:
        selection = self.tree.selection()
        return self.tree.item(selection[0], "values")[0] if selection else None

    def _refresh_buttons(self) -> None:
        idle = "disabled" if self.busy else "normal"
        ready = "normal" if self.serial and not self.busy else "disabled"
        self.scan_button.configure(state=idle)
        self.add_button.configure(state=idle)
        self.read_button.configure(state="normal" if self.selected_host() and not self.busy else "disabled")
        for button in (self.set_button, self.reset_button, self.restore_button):
            button.configure(state=ready)

    def background(self, work, on_done) -> None:
        """Run work() in a thread, then on_done(result) in the UI thread."""
        self.busy = True
        self._refresh_buttons()

        def run():
            try:
                self.events.put((on_done, work(), None))
            except Exception as e:  # reported in the UI thread
                self.events.put((on_done, None, e))

        threading.Thread(target=run, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                item = self.events.get_nowait()
                if item[0] == "progress":
                    _, done, total = item
                    self.scan_progress.configure(maximum=total, value=done)
                    continue
                on_done, result, error = item
                self.busy = False
                if error is None:
                    on_done(result)
                else:
                    self._show_error(error)
                self._refresh_buttons()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _show_error(self, error: Exception) -> None:
        if isinstance(error, WriteFailed):
            self.log(f"WRITE FAILED: {error}")
            messagebox.showerror("Write failed", f"{error}\n\nUse 'Restore backup…' to write the backup back.")
        elif isinstance(error, (Stopped, TimeoutError)):
            message = str(error) if isinstance(error, Stopped) else "The printer did not respond."
            self.log(f"Stopped: {message} Nothing was written.")
            messagebox.showwarning("Stopped", f"{message}\n\nNothing was written to the printer.")
        else:
            self.log(f"Error: {type(error).__name__}: {error}")
            messagebox.showerror("Error", f"{type(error).__name__}: {error}")

    # Finding printers

    def scan(self) -> None:
        networks = waste_ink.local_networks()
        self.log("Scanning " + ", ".join(str(n) for n in networks) + " …")
        self.scan_progress.configure(value=0)

        def progress(done, total):
            self.events.put(("progress", done, total))

        self.background(lambda: waste_ink.discover(networks, on_progress=progress), self._scan_done)

    def _scan_done(self, found: list[waste_ink.FoundPrinter]) -> None:
        known = {self.tree.item(i, "values")[0] for i in self.tree.get_children()}
        for p in found:
            if p.host not in known:
                self.tree.insert("", "end", values=(p.host, p.model or p.description, p.name or ""))
        self.log(f"Found {len(found)} Epson printer(s)." if found else
                 "No Epson printers found. Check that the printer is on and on the same network, or type its IP address.")
        if found and not self.tree.selection():
            self.tree.selection_set(self.tree.get_children()[0])
        self._refresh_buttons()

    def add_manual(self) -> None:
        host = self.manual_ip.get().strip()
        if not host:
            return
        item = self.tree.insert("", "end", values=(host, "(entered by hand)", ""))
        self.tree.selection_set(item)
        self.manual_ip.set("")
        self._refresh_buttons()

    # Reading

    def read_counters(self) -> None:
        host = self.selected_host()
        if not host or self.busy:
            return
        self.serial = None
        self.printer = None
        self.log(f"Reading {host} …")

        def work():
            printer = waste_ink.connect(host)
            return printer, waste_ink.read_identity(printer)

        self.background(work, self._identity_read)

    def _identity_read(self, result) -> None:
        printer, info = result
        self.identity.configure(text=(
            f"{info.model or '?'}  ({info.name or '?'})\n"
            f"{info.firmware or ''}\n"
            f"Serial: EEPROM {info.eeprom_serial or '?'} / network {info.network_serial or 'not reported'}"))
        try:
            serial = waste_ink.verify(info, lambda s: messagebox.askyesno(
                "Confirm the serial number",
                f"The printer doesn't report its serial number over the network.\n\n"
                f"Is '{s}' the serial number on the label at the back of the printer?"))
        except Stopped as e:
            self._show_error(e)
            return
        self.printer, self.serial = printer, serial
        self.identity.configure(text=self.identity["text"] + "  ✓ verified")
        self.log(f"Serial number {serial} verified: the L3150 definition fits this printer.")
        self.update_levels()

    def update_levels(self) -> None:
        self.background(lambda: waste_ink.levels(self.printer), self._show_levels)

    def _show_levels(self, levels: dict[str, float]) -> None:
        for key, bar in self.bars.items():
            value = levels.get(key)
            bar.configure(value=min(value or 0, 100))
            self.percents[key].configure(text=f"{value:.2f} %" if value is not None else "-")
        self.log("Counters: " + ", ".join(f"{k.replace('_', ' ')} {v:.2f} %" for k, v in levels.items()))

    # Changing

    def set_main(self) -> None:
        try:
            percent = float(self.target.get().replace(",", "."))
        except ValueError:
            messagebox.showwarning("Invalid value", "Enter a number between 0 and 100.")
            return
        self.background(lambda: waste_ink.plan_set_main(self.printer, percent), lambda plan: self._confirm(plan, True))

    def full_reset(self) -> None:
        self.background(lambda: waste_ink.plan_reset(self.printer), lambda plan: self._confirm(plan, True))

    def restore(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose a backup", initialdir=waste_ink.BACKUP_DIR, filetypes=[("Backups", "*.json")])
        if not path:
            return
        self.background(lambda: waste_ink.plan_restore(self.printer, pathlib.Path(path), self.serial),
                        lambda plan: self._confirm(plan, False))

    def _confirm(self, plan: Plan, backup: bool) -> None:
        if not plan.changes:
            self.log(f"{plan.description}: the printer already has these values. Nothing to write.")
            messagebox.showinfo("Nothing to write", "The printer already has these values.")
            return
        lines = "\n".join(f"  address {oid}: {old} → {new}" for oid, (old, new) in plan.changes.items())
        backup_note = ""
        if backup:
            path = waste_ink.save_backup(self.printer.hostname, self.serial, plan.current)
            self.log(f"Backup saved: {path}")
            backup_note = f"\n\nA backup of the current values was saved to:\n{path}"
        if not messagebox.askyesno(
                "Write to the printer?",
                f"{plan.description.capitalize()} on printer {self.serial}.\n\n"
                f"These EEPROM bytes will be written:\n{lines}{backup_note}\n\n{PAD_WARNING}\n\nWrite them now?",
                icon="warning"):
            self.log(f"{plan.description}: cancelled. Nothing was written.")
            return
        self.log(f"Writing: {plan.description} …")

        def work():
            waste_ink.apply(self.printer, plan)
            return waste_ink.levels(self.printer)

        self.background(work, self._written)

    def _written(self, levels: dict[str, float]) -> None:
        self._show_levels(levels)
        self.log("Done. Switch the printer off and on again.")
        messagebox.showinfo("Done", "The counters were written.\n\nSwitch the printer off and on again.")


def main() -> None:
    logging.basicConfig(level=logging.CRITICAL)
    App().mainloop()


if __name__ == "__main__":
    main()
