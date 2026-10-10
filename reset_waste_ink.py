"""
Read, back up and reset the waste ink counters of an Epson L3150-family
printer (L3150, L3151, L3156, L3160, ...) over the network.

    python reset_waste_ink.py 192.168.1.50            # read only
    python reset_waste_ink.py 192.168.1.50 --reset    # back up, then reset
    python reset_waste_ink.py 192.168.1.50 --restore backups/<file>.json

Nothing is written unless you pass --reset or --restore and confirm.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "vendor"))

from epson_print_conf import EpsonPrinter  # noqa: E402

# The L3156 is a regional variant of the L3150; epson_print_conf defines the
# family under "L3150". Its serial number and reset addresses come from the
# L4160 entry ("same-as").
DEFINITION = "L3150"
# Standard Printer-MIB serial number (prtGeneralSerialNumber), reported by the
# network interface independently of the EEPROM.
SERIAL_OID = "1.3.6.1.2.1.43.5.1.1.17.1"
BACKUP_DIR = HERE / "backups"


def fail(message: str) -> None:
    print(f"\nSTOP: {message}\nNothing was written to the printer.")
    sys.exit(1)


def connect(host: str) -> EpsonPrinter:
    printer = EpsonPrinter(model=DEFINITION, hostname=host, timeout=5, retries=2)
    if printer.parm is None:
        fail(f"epson_print_conf has no definition for {DEFINITION}.")
    printer.MIB_INFO["Serial Number"] = SERIAL_OID
    return printer


def addresses_to_reset(printer: EpsonPrinter) -> dict[int, int]:
    """The EEPROM addresses reset_waste_ink_levels() will write, with their new values."""
    if "raw_waste_reset" in printer.parm:
        return dict(printer.parm["raw_waste_reset"])
    oids = printer.parm["main_waste"]["oids"] + printer.parm.get("borderless_waste", {}).get("oids", [])
    return {oid: 0 for oid in oids}


def main_counter_bytes(printer: EpsonPrinter, percent: float, current: dict[int, int]) -> dict[int, int]:
    """The main waste counter bytes that must change to show `percent`, with their new values."""
    main = printer.parm["main_waste"]
    value = round(percent * main["divider"])
    shared = set(printer.parm.get("borderless_waste", {}).get("oids", []))
    changes = {}
    # The counter is stored least significant byte first, in the order of "oids".
    for position, oid in enumerate(main["oids"]):
        new = (value >> (8 * position)) & 0xFF
        if new == current[oid]:
            continue
        if oid in shared:
            fail(f"{percent} % would change byte {oid}, which the borderless counter also uses. Pick another value.")
        changes[oid] = new
    if value >> (8 * len(main["oids"])):
        fail(f"{percent} % does not fit in the counter.")
    return changes


def read_bytes(printer: EpsonPrinter, oids: list[int]) -> dict[int, int]:
    values = printer.read_eeprom_many(oids, label="backup")
    if not values or None in values:
        fail("Could not read the EEPROM. Check the IP address and that the printer is switched on.")
    return {oid: int(value, 16) for oid, value in zip(oids, values)}


def snmp_value(printer: EpsonPrinter, name: str) -> str | None:
    # Ask for single values: some printers never answer a few of the library's
    # other SNMP values, and asking for all of them at once then times out.
    try:
        return (printer.get_snmp_info(name) or {}).get(name)
    except TimeoutError:
        return None


def identify(printer: EpsonPrinter) -> str:
    """Print what the printer reports and prove the definition fits. Returns the serial number."""
    names = ["Model", "Epson Printer Name", "EEPS2 firmware version", "Serial Number"]
    info = {name: snmp_value(printer, name) for name in names}
    if not any(info.values()):
        fail("The printer did not answer over SNMP. Check the IP address and that SNMP is enabled.")

    model = info.get("Model") or info.get("Epson Printer Name") or "?"
    print(f"Printer model (reported): {model}")
    if info.get("Epson Printer Name"):
        print(f"Printer name:             {info['Epson Printer Name']}")
    print(f"Firmware:                 {info.get('EEPS2 firmware version', '?')}")
    if "L31" not in str(model):
        fail(f"This tool is only for the L3150 family, but the printer reports '{model}'.")

    eeprom_serial = printer.get_serial_number()
    if not eeprom_serial or not str(eeprom_serial).isalnum():
        fail("Could not read a valid serial number from the EEPROM with the L3150 read key.")
    print(f"Serial number (EEPROM):   {eeprom_serial}")

    network_serial = str(info.get("Serial Number") or "").strip()
    if network_serial:
        print(f"Serial number (network):  {network_serial}")
        if network_serial != eeprom_serial:
            fail("The serial numbers differ, so the L3150 memory layout does not match this printer.")
        print("The serial numbers match: the L3150 definition fits this printer.")
    else:
        print("The printer does not report its serial number over the network.")
        answer = input(f"Is '{eeprom_serial}' the serial number on the label at the back of the printer? [yes/no] ")
        if answer.strip().lower() != "yes":
            fail("The serial number could not be confirmed.")
    return eeprom_serial


def show_levels(printer: EpsonPrinter, title: str) -> None:
    levels = printer.get_waste_ink_levels()
    if levels is None:
        fail("Could not read the waste ink levels.")
    print(f"\n{title}")
    for name, percent in levels.items():
        print(f"  {name.replace('_', ' '):<18} {percent:6.2f} %")


def backup(host: str, serial: str, values: dict[int, int]) -> pathlib.Path:
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = BACKUP_DIR / f"{serial}-{stamp}.json"
    path.write_text(json.dumps({
        "host": host,
        "serial_number": serial,
        "definition": DEFINITION,
        "created": stamp,
        "eeprom": {str(oid): value for oid, value in values.items()},
    }, indent=2))
    return path


def confirm(changes: dict[int, tuple[int, int]]) -> None:
    print("\nThese EEPROM bytes will be written (address: current -> new):")
    for oid, (old, new) in changes.items():
        print(f"  {oid:>5}: {old:3d} -> {new:3d}")
    if input('\nType "yes" to write them: ').strip().lower() != "yes":
        fail("Cancelled.")


def write(printer: EpsonPrinter, changes: dict[int, tuple[int, int]]) -> None:
    for oid, (_, new) in changes.items():
        try:
            # write_eeprom() reads every byte back and fails if it did not change.
            ok = printer.write_eeprom(oid, new, label="waste_ink_tool")
        except Exception as e:
            ok = False
            print(f"\n{type(e).__name__}: {e}")
        if not ok:
            print(f"\nERROR: writing address {oid} failed. Some bytes may already be written.")
            print("Check the connection, then write the backup back with --restore.")
            sys.exit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", help="IP address of the printer")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--reset", action="store_true", help="back up, then reset the waste ink counters")
    action.add_argument("--set-main", metavar="PERCENT", type=float,
                        help="back up, then set only the main waste ink counter to PERCENT (0-100)")
    action.add_argument("--restore", metavar="BACKUP", type=pathlib.Path, help="write a backup file back")
    parser.add_argument("--debug", action="store_true", help="show the library's debug output")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.CRITICAL)

    printer = connect(args.host)
    serial = identify(printer)
    show_levels(printer, "Waste ink counters:")

    if args.reset:
        targets = addresses_to_reset(printer)
        current = read_bytes(printer, list(targets))
        path = backup(args.host, serial, current)
        print(f"\nBackup saved: {path}")
        confirm({oid: (current[oid], new) for oid, new in targets.items()})
        write(printer, {oid: (current[oid], new) for oid, new in targets.items()})
        show_levels(printer, "Waste ink counters after the reset:")
        print("\nDone. Switch the printer off and on again.")
        print("Remember: the counter is reset, the pads are not. Replace them or fit an external waste ink tank.")

    elif args.set_main is not None:
        if not 0 <= args.set_main <= 100:
            fail("PERCENT must be between 0 and 100.")
        oids = printer.parm["main_waste"]["oids"]
        current = read_bytes(printer, oids)
        changes = main_counter_bytes(printer, args.set_main, current)
        if not changes:
            print(f"\nThe main counter already shows {args.set_main} %. Nothing to write.")
            return
        path = backup(args.host, serial, current)
        print(f"\nBackup saved: {path}")
        confirm({oid: (current[oid], new) for oid, new in changes.items()})
        write(printer, {oid: (current[oid], new) for oid, new in changes.items()})
        show_levels(printer, "Waste ink counters after the change:")
        print("\nDone. Switch the printer off and on again.")

    elif args.restore:
        saved = json.loads(args.restore.read_text())
        if saved.get("serial_number") != serial:
            fail(f"The backup belongs to printer {saved.get('serial_number')}, not {serial}.")
        target = {int(oid): value for oid, value in saved["eeprom"].items()}
        current = read_bytes(printer, list(target))
        confirm({oid: (current[oid], value) for oid, value in target.items()})
        write(printer, {oid: (current[oid], value) for oid, value in target.items()})
        show_levels(printer, "Waste ink counters after the restore:")
        print("\nRestored. Switch the printer off and on again.")

    else:
        print("\nRead-only run: nothing was written. Use --reset to reset the counters.")


if __name__ == "__main__":
    try:
        main()
    except TimeoutError:
        # Writes handle their own errors; anything reaching here happened before writing.
        fail("The printer did not respond. Check the IP address and that the printer is switched on.")
