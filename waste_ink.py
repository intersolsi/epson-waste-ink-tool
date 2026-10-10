"""
Core logic for reading and changing the waste ink counters of an Epson
L3150-family printer. Shared by the command line tool and the GUI.

Nothing here prints, exits or asks questions: problems raise Stopped (nothing
was written) or WriteFailed (writing started), and confirmations are left to
the caller.
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import json
import pathlib
import socket
import sys
from dataclasses import dataclass, field
from typing import Callable

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "vendor"))
# In a packaged .exe, __file__ is in a temporary folder that is deleted on exit;
# keep backups next to the .exe instead.
APP_DIR = pathlib.Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else HERE

from epson_print_conf import EpsonPrinter  # noqa: E402

# The L3156 is a regional variant of the L3150; epson_print_conf defines the
# family under "L3150". Its serial number and reset addresses come from the
# L4160 entry ("same-as").
__version__ = "1.0.0"

DEFINITION = "L3150"
# Standard Printer-MIB serial number (prtGeneralSerialNumber), reported by the
# network interface independently of the EEPROM.
SERIAL_OID = "1.3.6.1.2.1.43.5.1.1.17.1"
SYS_DESCR_OID = "1.3.6.1.2.1.1.1.0"
BACKUP_DIR = APP_DIR / "backups"


class Stopped(Exception):
    """A check failed, the printer didn't answer, or the user cancelled. Nothing was written."""


class WriteFailed(Exception):
    """A write failed after writing had started. Some bytes may have changed: restore the backup."""


@dataclass
class PrinterInfo:
    host: str
    model: str | None
    name: str | None
    firmware: str | None
    eeprom_serial: str | None
    network_serial: str | None


@dataclass
class Plan:
    """A set of EEPROM bytes to write, with the values they have now."""

    description: str
    current: dict[int, int]
    changes: dict[int, tuple[int, int]] = field(default_factory=dict)  # address -> (old, new)


@dataclass
class FoundPrinter:
    host: str
    description: str
    model: str | None
    name: str | None


# Connecting and identifying


def connect(host: str, timeout: float = 5, retries: int = 2) -> EpsonPrinter:
    printer = EpsonPrinter(model=DEFINITION, hostname=host, timeout=timeout, retries=retries)
    if printer.parm is None:
        raise Stopped(f"epson_print_conf has no definition for {DEFINITION}.")
    printer.MIB_INFO["Serial Number"] = SERIAL_OID
    return printer


def snmp_value(printer: EpsonPrinter, name: str) -> str | None:
    # Ask for single values: some printers never answer a few of the library's
    # other SNMP values, and asking for all of them at once then times out.
    try:
        value = (printer.get_snmp_info(name) or {}).get(name)
    except TimeoutError:
        return None
    return str(value).strip() if value else None


def read_identity(printer: EpsonPrinter) -> PrinterInfo:
    """Read what the printer reports about itself, without judging it yet."""
    info = {n: snmp_value(printer, n) for n in ["Model", "Epson Printer Name", "EEPS2 firmware version", "Serial Number"]}
    if not any(info.values()):
        raise Stopped("The printer did not answer over SNMP. Check the IP address and that SNMP is enabled.")
    try:
        eeprom_serial = printer.get_serial_number()
    except TimeoutError:
        eeprom_serial = None
    return PrinterInfo(
        host=printer.hostname,
        model=info["Model"],
        name=info["Epson Printer Name"],
        firmware=info["EEPS2 firmware version"],
        eeprom_serial=str(eeprom_serial) if eeprom_serial and str(eeprom_serial).isalnum() else None,
        network_serial=info["Serial Number"],
    )


def verify(info: PrinterInfo, confirm_label: Callable[[str], bool]) -> str:
    """
    Prove that the L3150 definition fits this printer, and return its serial number.

    confirm_label(serial) is called only when the printer doesn't report its
    serial number over the network; it must return True if the serial matches
    the label on the printer.
    """
    model = info.model or info.name or "?"
    if "L31" not in model:
        raise Stopped(f"This tool is only for the L3150 family, but the printer reports '{model}'.")
    if not info.eeprom_serial:
        raise Stopped("Could not read a valid serial number from the EEPROM with the L3150 read key.")
    if info.network_serial:
        if info.network_serial != info.eeprom_serial:
            raise Stopped("The serial numbers differ, so the L3150 memory layout does not match this printer.")
    elif not confirm_label(info.eeprom_serial):
        raise Stopped("The serial number could not be confirmed.")
    return info.eeprom_serial


def levels(printer: EpsonPrinter) -> dict[str, float]:
    """Waste ink counters in percent, e.g. {"main_waste": 50.0, "borderless_waste": 0.61}."""
    try:
        result = printer.get_waste_ink_levels()
    except TimeoutError:
        result = None
    if result is None:
        raise Stopped("Could not read the waste ink levels.")
    return result


def read_bytes(printer: EpsonPrinter, oids: list[int]) -> dict[int, int]:
    try:
        values = printer.read_eeprom_many(oids, label="read")
    except TimeoutError:
        values = None
    if not values or None in values:
        raise Stopped("Could not read the EEPROM. Check the IP address and that the printer is switched on.")
    return {oid: int(value, 16) for oid, value in zip(oids, values)}


# Planning changes


def plan_reset(printer: EpsonPrinter) -> Plan:
    """Full reset: every address in the definition's reset map."""
    if "raw_waste_reset" in printer.parm:
        targets = dict(printer.parm["raw_waste_reset"])
    else:
        oids = printer.parm["main_waste"]["oids"] + printer.parm.get("borderless_waste", {}).get("oids", [])
        targets = {oid: 0 for oid in oids}
    current = read_bytes(printer, list(targets))
    return Plan("full reset", current, {oid: (current[oid], new) for oid, new in targets.items()})


def plan_set_main(printer: EpsonPrinter, percent: float) -> Plan:
    """Only the main counter bytes that must change to show `percent`."""
    if not 0 <= percent <= 100:
        raise Stopped("PERCENT must be between 0 and 100.")
    main = printer.parm["main_waste"]
    current = read_bytes(printer, main["oids"])
    value = round(percent * main["divider"])
    if value >> (8 * len(main["oids"])):
        raise Stopped(f"{percent} % does not fit in the counter.")
    shared = set(printer.parm.get("borderless_waste", {}).get("oids", []))
    changes = {}
    # The counter is stored least significant byte first, in the order of "oids".
    for position, oid in enumerate(main["oids"]):
        new = (value >> (8 * position)) & 0xFF
        if new == current[oid]:
            continue
        if oid in shared:
            raise Stopped(f"{percent} % would change byte {oid}, which the borderless counter also uses. Pick another value.")
        changes[oid] = (current[oid], new)
    return Plan(f"main counter to {percent:g} %", current, changes)


def plan_restore(printer: EpsonPrinter, backup_path: pathlib.Path, serial: str) -> Plan:
    """Write a backup back, only to the printer it came from."""
    saved = json.loads(pathlib.Path(backup_path).read_text())
    if saved.get("serial_number") != serial:
        raise Stopped(f"The backup belongs to printer {saved.get('serial_number')}, not {serial}.")
    target = {int(oid): value for oid, value in saved["eeprom"].items()}
    current = read_bytes(printer, list(target))
    return Plan(f"restore {pathlib.Path(backup_path).name}", current,
                {oid: (current[oid], value) for oid, value in target.items()})


# Changing the printer


def save_backup(host: str, serial: str, values: dict[int, int]) -> pathlib.Path:
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


def apply(printer: EpsonPrinter, plan: Plan) -> None:
    """Write the plan's bytes. Each write is read back by write_eeprom()."""
    for oid, (_, new) in plan.changes.items():
        try:
            ok = printer.write_eeprom(oid, new, label="waste_ink_tool")
            error = ""
        except Exception as e:
            ok, error = False, f" ({type(e).__name__}: {e})"
        if not ok:
            raise WriteFailed(f"Writing address {oid} failed{error}. Some bytes may already be written; restore the backup.")


# Finding printers


def local_networks() -> list[ipaddress.IPv4Network]:
    """This computer's IPv4 networks: each adapter's own network up to /22, otherwise the /24 around it."""
    import psutil

    networks = set()
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            if a.family != socket.AF_INET or not a.netmask:
                continue
            iface = ipaddress.ip_interface(f"{a.address}/{a.netmask}")
            if iface.ip.is_loopback or iface.ip.is_link_local:
                continue
            network = iface.network if iface.network.prefixlen >= 22 else ipaddress.ip_interface(f"{a.address}/24").network
            networks.add(network)
    return sorted(networks)


async def _probe(dispatcher, host: str, timeout: float) -> FoundPrinter | None:
    from pysnmp.hlapi.v1arch.asyncio import CommunityData, ObjectIdentity, ObjectType, UdpTransportTarget, get_cmd

    async def get(*oids):
        # One retry: over Wi-Fi the first packet to a sleeping printer is often lost.
        transport = await UdpTransportTarget.create((host, 161), timeout=timeout, retries=1)
        error, status, _, binds = await get_cmd(
            dispatcher, CommunityData("public", mpModel=0), transport,
            *(ObjectType(ObjectIdentity(oid)) for oid in oids))
        return None if error or status else [str(b[1]) for b in binds]

    try:
        descr = await get(SYS_DESCR_OID)
        if not descr or "EPSON" not in descr[0].upper():
            return None
        details = await get(EpsonPrinter.MIB_INFO["Model"], EpsonPrinter.MIB_INFO["Epson Printer Name"])
    except Exception:
        return None
    model, name = details if details else (None, None)
    return FoundPrinter(host, descr[0], model or None, name or None)


def discover(networks: list[ipaddress.IPv4Network] | None = None, timeout: float = 1.0,
             on_progress: Callable[[int, int], None] | None = None) -> list[FoundPrinter]:
    """Ask every address in the local networks for its SNMP description and keep the Epson devices."""
    from pysnmp.hlapi.v1arch.asyncio import SnmpDispatcher

    hosts = [str(h) for n in (networks if networks is not None else local_networks()) for h in n.hosts()]

    async def run():
        dispatcher = SnmpDispatcher()
        limit = asyncio.Semaphore(300)
        done = 0

        async def one(host):
            nonlocal done
            async with limit:
                result = await _probe(dispatcher, host, timeout)
            done += 1
            if on_progress:
                on_progress(done, len(hosts))
            return result

        try:
            results = await asyncio.gather(*(one(h) for h in hosts))
        finally:
            dispatcher.transport_dispatcher.close_dispatcher()
        return [r for r in results if r]

    return sorted(asyncio.run(run()), key=lambda p: ipaddress.ip_address(p.host))
