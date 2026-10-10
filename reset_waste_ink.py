"""
Read, back up and reset the waste ink counters of an Epson L3150-family
printer (L3150, L3151, L3156, L3160, ...) over the network.

    python reset_waste_ink.py 192.168.1.50                  # read only
    python reset_waste_ink.py 192.168.1.50 --reset          # back up, then full reset
    python reset_waste_ink.py 192.168.1.50 --set-main 50    # back up, then set the main counter
    python reset_waste_ink.py 192.168.1.50 --restore backups/<file>.json

Nothing is written unless you pass --reset, --set-main or --restore and confirm.
For a graphical interface, run gui.py.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys

import waste_ink
from waste_ink import Plan, Stopped, WriteFailed


def stop_message(message: str) -> None:
    print(f"\nSTOP: {message}\nNothing was written to the printer.")


def print_identity(info: waste_ink.PrinterInfo) -> None:
    print(f"Printer model (reported): {info.model or info.name or '?'}")
    if info.name:
        print(f"Printer name:             {info.name}")
    print(f"Firmware:                 {info.firmware or '?'}")
    print(f"Serial number (EEPROM):   {info.eeprom_serial or '?'}")
    if info.network_serial:
        print(f"Serial number (network):  {info.network_serial}")
    else:
        print("The printer does not report its serial number over the network.")


def ask_label(serial: str) -> bool:
    answer = input(f"Is '{serial}' the serial number on the label at the back of the printer? [yes/no] ")
    return answer.strip().lower() == "yes"


def show_levels(printer, title: str) -> None:
    print(f"\n{title}")
    for name, percent in waste_ink.levels(printer).items():
        print(f"  {name.replace('_', ' '):<18} {percent:6.2f} %")


def confirm(plan: Plan) -> None:
    print("\nThese EEPROM bytes will be written (address: current -> new):")
    for oid, (old, new) in plan.changes.items():
        print(f"  {oid:>5}: {old:3d} -> {new:3d}")
    if input('\nType "yes" to write them: ').strip().lower() != "yes":
        raise Stopped("Cancelled.")


def change(printer, host: str, serial: str, plan: Plan, backup: bool, title: str) -> None:
    if not plan.changes:
        print("\nThe printer already has these values. Nothing to write.")
        return
    if backup:
        print(f"\nBackup saved: {waste_ink.save_backup(host, serial, plan.current)}")
    confirm(plan)
    waste_ink.apply(printer, plan)
    show_levels(printer, title)


def cli(argv: list[str] | None = None) -> int:
    """Run the tool and return its exit code: 0 done, 1 stopped before writing, 2 a write failed."""
    try:
        main(argv)
        return 0
    except Stopped as e:
        stop_message(str(e))
        return 1
    except TimeoutError:
        stop_message("The printer did not respond. Check the IP address and that the printer is switched on.")
        return 1
    except WriteFailed as e:
        print(f"\nERROR: {e}\nCheck the connection, then write the backup back with --restore.")
        return 2


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", help="IP address of the printer")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--reset", action="store_true", help="back up, then reset the waste ink counters")
    action.add_argument("--set-main", metavar="PERCENT", type=float,
                        help="back up, then set only the main waste ink counter to PERCENT (0-100)")
    action.add_argument("--restore", metavar="BACKUP", type=pathlib.Path, help="write a backup file back")
    parser.add_argument("--debug", action="store_true", help="show the library's debug output")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.CRITICAL)

    printer = waste_ink.connect(args.host)
    info = waste_ink.read_identity(printer)
    print_identity(info)
    serial = waste_ink.verify(info, ask_label)
    if info.network_serial:
        print("The serial numbers match: the L3150 definition fits this printer.")
    else:
        print("Serial number confirmed against the label.")
    show_levels(printer, "Waste ink counters:")

    if args.reset:
        change(printer, args.host, serial, waste_ink.plan_reset(printer), True, "Waste ink counters after the reset:")
        print("\nDone. Switch the printer off and on again.")
        print("Remember: the counter is reset, the pads are not. Replace them or fit an external waste ink tank.")
    elif args.set_main is not None:
        change(printer, args.host, serial, waste_ink.plan_set_main(printer, args.set_main), True,
               "Waste ink counters after the change:")
        print("\nDone. Switch the printer off and on again.")
    elif args.restore:
        change(printer, args.host, serial, waste_ink.plan_restore(printer, args.restore, serial), False,
               "Waste ink counters after the restore:")
        print("\nDone. Switch the printer off and on again.")
    else:
        print("\nRead-only run: nothing was written. Use --reset or --set-main to change the counters.")


if __name__ == "__main__":
    sys.exit(cli())
