"""
Tests for reset_waste_ink.py against a simulated L3150-family printer.
No printer or network needed:

    .venv\\Scripts\\python -m unittest discover tests
"""

import builtins
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

import reset_waste_ink as tool  # noqa: E402
from epson_print_conf import EpsonPrinter  # noqa: E402

SERIAL = "X5NY012345"
# Byte values read from a real L3156 at 94.37 % main / 0.61 % borderless.
REAL_BYTES = {47: 0, 48: 101, 49: 23, 50: 21, 51: 0, 52: 125, 53: 39, 54: 99, 55: 94, 28: 2}
FULL_RESET = {48: 0, 49: 0, 47: 0, 52: 0, 53: 0, 54: 94, 50: 0, 51: 0, 55: 94, 28: 0}


class FakePrinter(EpsonPrinter):
    """An EpsonPrinter whose SNMP and EEPROM live in memory."""

    eeprom: dict = {}
    writes: list = []
    snmp = {}

    def get_snmp_info(self, mib_name=None, advanced=False):
        return {mib_name: self.snmp.get(mib_name)} if mib_name else dict(self.snmp)

    def read_eeprom_many(self, oids, label="x"):
        return [format(self.eeprom.get(o, 0), "02x") for o in oids]

    def write_eeprom(self, oid, value, label="x", report=None):
        FakePrinter.writes.append((oid, value))
        self.eeprom[oid] = value
        return True


class SimulatedPrinterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.backup_dir = pathlib.Path(self.tmp.name)
        FakePrinter.eeprom = {1604 + i: ord(c) for i, c in enumerate(SERIAL)} | REAL_BYTES
        FakePrinter.writes = []
        FakePrinter.snmp = {
            "Model": "EPSON L3150 Series",
            "Epson Printer Name": "L3156",
            "EEPS2 firmware version": "EEPS2 Hard Ver.1.00 Firm Ver.0.39",
            "Serial Number": SERIAL,
        }

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args, answers=("yes",)):
        """Run main() with the given arguments; returns (exit code, output)."""
        replies = iter(answers)
        out = io.StringIO()
        with mock.patch.object(tool, "EpsonPrinter", FakePrinter), \
                mock.patch.object(tool, "BACKUP_DIR", self.backup_dir), \
                mock.patch.object(sys, "argv", ["reset_waste_ink.py", "192.0.2.1", *args]), \
                mock.patch.object(builtins, "input", lambda prompt="": next(replies)), \
                contextlib.redirect_stdout(out):
            try:
                tool.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def latest_backup(self):
        return sorted(self.backup_dir.glob("*.json"))[-1]

    # Reading

    def test_read_only_writes_nothing(self):
        code, out = self.run_tool()
        self.assertEqual(code, 0)
        self.assertEqual(FakePrinter.writes, [])
        self.assertIn("94.37 %", out)
        self.assertIn("0.61 %", out)
        self.assertEqual(list(self.backup_dir.iterdir()), [])

    # Safety stops

    def test_serial_mismatch_stops(self):
        FakePrinter.snmp["Serial Number"] = "DIFFERENT1"
        code, out = self.run_tool("--reset")
        self.assertEqual(code, 1)
        self.assertEqual(FakePrinter.writes, [])
        self.assertIn("serial numbers differ", out)

    def test_other_model_stops(self):
        FakePrinter.snmp["Model"] = "EPSON XP-2100 Series"
        code, _ = self.run_tool("--reset")
        self.assertEqual(code, 1)
        self.assertEqual(FakePrinter.writes, [])

    def test_declined_confirmation_writes_nothing(self):
        code, _ = self.run_tool("--reset", answers=("no",))
        self.assertEqual(code, 1)
        self.assertEqual(FakePrinter.writes, [])

    def test_missing_network_serial_asks_about_the_label(self):
        FakePrinter.snmp["Serial Number"] = None
        code, out = self.run_tool(answers=("no",))
        self.assertEqual(code, 1)
        self.assertIn("serial number could not be confirmed", out)

    # Full reset

    def test_reset_writes_the_definition_bytes_and_backs_them_up(self):
        code, out = self.run_tool("--reset")
        self.assertEqual(code, 0)
        self.assertEqual(dict(FakePrinter.writes), FULL_RESET)
        saved = json.loads(self.latest_backup().read_text())
        self.assertEqual(saved["serial_number"], SERIAL)
        self.assertEqual(saved["eeprom"], {str(o): REAL_BYTES[o] for o in FULL_RESET})
        self.assertIn("0.00 %", out)

    # Partial setting

    def test_set_main_50_changes_only_byte_49(self):
        code, out = self.run_tool("--set-main", "50")
        self.assertEqual(code, 0)
        self.assertEqual(FakePrinter.writes, [(49, 12)])
        self.assertIn("50.00 %", out)
        self.assertIn("0.61 %", out)  # borderless untouched

    def test_set_main_twice_writes_once(self):
        self.run_tool("--set-main", "50")
        FakePrinter.writes = []
        code, out = self.run_tool("--set-main", "50")
        self.assertEqual(code, 0)
        self.assertEqual(FakePrinter.writes, [])
        self.assertIn("Nothing to write", out)

    def test_set_main_out_of_range_is_refused(self):
        code, _ = self.run_tool("--set-main", "150")
        self.assertEqual(code, 1)
        self.assertEqual(FakePrinter.writes, [])

    # Restore

    def test_restore_undoes_a_change(self):
        self.run_tool("--set-main", "50")
        backup = self.latest_backup()
        FakePrinter.writes = []
        code, _ = self.run_tool("--restore", str(backup))
        self.assertEqual(code, 0)
        self.assertEqual(FakePrinter.eeprom[49], 23)

    def test_restore_refuses_another_printers_backup(self):
        self.run_tool("--set-main", "50")
        other = self.backup_dir / "other.json"
        other.write_text(json.dumps(json.loads(self.latest_backup().read_text()) | {"serial_number": "ZZZZZZZZZZ"}))
        FakePrinter.writes = []
        code, _ = self.run_tool("--restore", str(other))
        self.assertEqual(code, 1)
        self.assertEqual(FakePrinter.writes, [])


if __name__ == "__main__":
    unittest.main()
