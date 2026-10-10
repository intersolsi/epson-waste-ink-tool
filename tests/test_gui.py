"""
Drive the GUI against the simulated printer: real window (hidden), simulated
button presses, dialogs answered automatically.
"""

import json
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gui  # noqa: E402
import waste_ink  # noqa: E402
from test_simulated_printer import FakePrinter, REAL_BYTES, SERIAL  # noqa: E402


class GuiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.backup_dir = pathlib.Path(self.tmp.name)
        FakePrinter.eeprom = {1604 + i: ord(c) for i, c in enumerate(SERIAL)} | REAL_BYTES
        FakePrinter.writes = []
        FakePrinter.snmp = {
            "Model": "EPSON L3150 Series", "Epson Printer Name": "L3156",
            "EEPS2 firmware version": "EEPS2 Hard Ver.1.00 Firm Ver.0.39", "Serial Number": SERIAL,
        }
        self.patches = [
            mock.patch.object(waste_ink, "EpsonPrinter", FakePrinter),
            mock.patch.object(waste_ink, "BACKUP_DIR", self.backup_dir),
            mock.patch.object(gui.messagebox, "showinfo"),
            mock.patch.object(gui.messagebox, "showwarning"),
            mock.patch.object(gui.messagebox, "showerror"),
        ]
        for p in self.patches:
            p.start()
        self.app = gui.App()
        self.app.withdraw()

    def tearDown(self):
        self.app.destroy()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def settle(self, timeout=5.0):
        """Process events until background work has finished."""
        end = time.time() + timeout
        while time.time() < end:
            self.app.update()
            if not self.app.busy and self.app.events.empty():
                self.app.update()
                return
            time.sleep(0.01)
        self.fail("GUI did not become idle")

    def read_printer(self):
        self.app.manual_ip.set("192.0.2.1")
        self.app.add_manual()
        self.app.read_counters()
        self.settle()
        self.settle()  # identity, then levels

    def log_text(self):
        return self.app.log_text.get("1.0", "end")

    def test_buttons_are_disabled_until_a_printer_is_verified(self):
        self.assertEqual(str(self.app.read_button["state"]), "disabled")
        self.assertEqual(str(self.app.set_button["state"]), "disabled")
        self.app.manual_ip.set("192.0.2.1")
        self.app.add_manual()
        self.assertEqual(str(self.app.read_button["state"]), "normal")
        self.assertEqual(str(self.app.set_button["state"]), "disabled")

    def test_read_shows_identity_and_counters(self):
        self.read_printer()
        self.assertEqual(self.app.serial, SERIAL)
        self.assertIn("L3156", self.app.identity["text"])
        self.assertEqual(self.app.percents["main_waste"]["text"], "94.37 %")
        self.assertEqual(self.app.percents["borderless_waste"]["text"], "0.61 %")
        self.assertAlmostEqual(float(self.app.bars["main_waste"]["value"]), 94.37, places=2)
        self.assertEqual(str(self.app.set_button["state"]), "normal")
        self.assertEqual(FakePrinter.writes, [])

    def test_serial_mismatch_keeps_changes_disabled(self):
        FakePrinter.snmp["Serial Number"] = "DIFFERENT1"
        self.read_printer()
        self.assertIsNone(self.app.serial)
        self.assertEqual(str(self.app.set_button["state"]), "disabled")
        gui.messagebox.showwarning.assert_called()

    def test_set_main_with_confirmation_writes_and_backs_up(self):
        self.read_printer()
        self.app.target.set("50")
        with mock.patch.object(gui.messagebox, "askyesno", return_value=True) as ask:
            self.app.set_main()
            self.settle()
            self.settle()
        self.assertIn("address 49: 23 → 12", ask.call_args.args[1])
        self.assertEqual(FakePrinter.writes, [(49, 12)])
        self.assertEqual(self.app.percents["main_waste"]["text"], "50.00 %")
        backups = list(self.backup_dir.glob("*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text())["eeprom"], {"48": 101, "49": 23, "47": 0})

    def test_declining_writes_nothing(self):
        self.read_printer()
        with mock.patch.object(gui.messagebox, "askyesno", return_value=False):
            self.app.full_reset()
            self.settle()
        self.assertEqual(FakePrinter.writes, [])
        self.assertIn("cancelled", self.log_text())

    def test_restore_from_backup(self):
        self.read_printer()
        with mock.patch.object(gui.messagebox, "askyesno", return_value=True):
            self.app.set_main()
            self.settle()
            self.settle()
            backup = next(self.backup_dir.glob("*.json"))
            with mock.patch.object(gui.filedialog, "askopenfilename", return_value=str(backup)):
                self.app.restore()
                self.settle()
                self.settle()
        self.assertEqual(FakePrinter.eeprom[49], 23)
        self.assertEqual(self.app.percents["main_waste"]["text"], "94.37 %")

    def test_scan_lists_found_printers(self):
        found = [waste_ink.FoundPrinter("192.168.223.1", "EPSON Built-in 11b/g/n Print Server", "EPSON L3150 Series", "L3156")]
        with mock.patch.object(waste_ink, "discover", return_value=found), \
                mock.patch.object(waste_ink, "local_networks", return_value=[]):
            self.app.scan()
            self.settle()
        rows = [self.app.tree.item(i, "values") for i in self.app.tree.get_children()]
        self.assertEqual(rows, [("192.168.223.1", "EPSON L3150 Series", "L3156")])
        self.assertEqual(self.app.selected_host(), "192.168.223.1")


if __name__ == "__main__":
    unittest.main()
