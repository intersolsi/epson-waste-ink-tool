# Specification: Epson waste ink counter tool

## 1. Purpose

Read, back up, set and restore the waste ink counters of an Epson EcoTank L3150-family printer over the network, without risking the rest of the printer's EEPROM.

Out of scope: emptying or replacing the physical waste ink pads. The tool changes counters only.

## 2. Supported printers

| Model | Status |
|---|---|
| L3156 | Verified on a real printer, firmware `EEPS2 Hard Ver.1.00 Firm Ver.0.39`, serial check passing |
| L3150, L3151, L3160, L3166, L3168 | Covered by the epson_print_conf `L3150` definition; not tested here |
| Other models | Refused (the model check requires `L31` in the reported model name) |

Connection: network only (SNMP v1, community `public`, UDP 161). Works over normal Wi-Fi/Ethernet and over the printer's Wi-Fi Direct network (printer at `192.168.223.1`).

## 3. Architecture

```text
waste_ink.py                core: identity, safety checks, plans, backups, writes, printer discovery
reset_waste_ink.py          command line interface on top of the core
gui.py                      Tkinter interface on top of the core
vendor/epson_print_conf.py  SNMP + EEPROM access and printer definitions
                            (Ircama/epson_print_conf release 8.2.0, commit 6873e9e, EUPL-1.2)
tests/                      tests against a simulated printer (CLI and GUI)
backups/                    JSON backups written before any change (not in git)
build.ps1                   release build: tests, PyInstaller (both programs), Inno Setup installer
installer.iss               Inno Setup script (per-user install, AppId fixed for upgrades)
.github/workflows/release.yml  builds and publishes a GitHub release when a v* tag is pushed
THIRD-PARTY-NOTICES.md      licenses of everything bundled into the programs
```

### Packaging

- PyInstaller builds two single-file programs: `EpsonWasteInk.exe` (GUI, no console) and `reset-waste-ink.exe` (command line). `vendor/` is added to the import path and all `pysnmp` submodules are collected, because pysnmp loads parts of itself dynamically.
- In a packaged program `__file__` points to a temporary folder that is deleted on exit, so `APP_DIR` (where `backups/` lives) is the folder of the `.exe` instead.
- The installer installs per user (`%LOCALAPPDATA%\Programs\Epson Waste Ink Tool`, no administrator rights). That keeps the program folder writable for backups. Uninstalling removes only installed files, so backups survive (tested).
- The version lives in one place, `__version__` in `waste_ink.py`; `build.ps1` passes it to the installer, and the release workflow refuses a tag that doesn't match it.

The core never prints, exits or asks questions. Problems raise `Stopped` (nothing was written) or `WriteFailed` (writing had started); confirmations are left to the interface. Both interfaces therefore share exactly the same checks and write logic.

The library is vendored at a fixed commit on purpose: the EEPROM addresses written by this tool come from its definitions, and an upstream change must never alter them silently. Upgrading the library is a deliberate change that requires re-running the verification in section 9.

## 4. Commands

```text
reset_waste_ink.py HOST                        read only
reset_waste_ink.py HOST --reset                back up, then full reset (definition's raw_waste_reset)
reset_waste_ink.py HOST --set-main PERCENT     back up, then set only the main counter
reset_waste_ink.py HOST --restore BACKUP.json  write a backup back
add --debug to any command for library/SNMP debug logging
```

`--reset`, `--set-main` and `--restore` are mutually exclusive.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (or nothing needed to be written) |
| 1 | Stopped before writing anything (check failed, cancelled, printer unreachable) |
| 2 | A write failed after writing started; some bytes may have changed, restore the backup |

### Flow common to all commands

1. Connect with the `L3150` definition.
2. **Identify** (section 5, `read_identity` then `verify`): model check, EEPROM serial, network serial, comparison.
3. Show current counter levels.
4. Command-specific steps below. Nothing is written in read-only mode.

| Command | Reads | Backs up | Writes |
|---|---|---|---|
| read only | counters | no | nothing |
| `--reset` | every address in the reset map | those addresses | the reset map (10 bytes on L3150) |
| `--set-main P` | main counter bytes (48, 49, 47) | those 3 bytes | only bytes whose value changes (often 1) |
| `--restore F` | the addresses in the backup | no | the backup's values, after checking its serial |

Every write path shows `address: current -> new` for each byte and requires the user to type `yes`.

### Graphical interface (`gui.py`)

Three sections, used top to bottom, plus a log:

1. **Printer:** a list of printers. *Scan network* fills it with `discover()`, showing a progress bar; *Add* inserts an IP address typed by hand.
2. **Counters:** *Read counters* (or double-clicking a printer) runs `read_identity()`, shows model, name, firmware and both serial numbers, runs `verify()` (with a yes/no dialog for the label if needed), then shows both counters as progress bars and percentages.
3. **Change:** *Apply…* (`plan_set_main` with the spin box value), *Full reset…* (`plan_reset`) and *Restore backup…* (`plan_restore` with a file chosen from `backups/`). Each saves a backup first (except restore), lists the exact bytes with the backup path and the pad warning in a confirmation dialog, and writes only on *Yes*.

The change buttons are disabled until a printer has passed `verify()`, and everything is disabled while an operation runs. Network work runs in a background thread; results return to the UI thread through a queue, so the window never freezes and widgets are only touched from the UI thread. Dialogs and `verify()` run on the UI thread.

## 5. Functions

### Core (`waste_ink.py`)

| Function | Responsibility | Raises |
|---|---|---|
| `connect(host)` | Create an `EpsonPrinter` for the `L3150` definition (timeout 5 s, 2 retries) and register the Printer-MIB serial number OID `1.3.6.1.2.1.43.5.1.1.17.1` | `Stopped` if the definition is missing |
| `snmp_value(printer, name)` | Read one SNMP value; a timeout returns `None`. Values are read one by one because the L3156 never answers some of the library's other OIDs, which made a bulk read time out | — |
| `read_identity(printer)` | Return a `PrinterInfo`: model, name, firmware, network serial, and the serial read from EEPROM (1604–1613) with the L3150 read key (`None` if invalid). Judges nothing, so callers can show it before the check | `Stopped` if the printer answers no SNMP at all |
| `verify(info, confirm_label)` | Refuse non-`L31` models and an unreadable EEPROM serial; compare EEPROM and network serial, or call `confirm_label(serial)` if there is no network serial. Returns the serial | `Stopped` on any failed check |
| `levels(printer)` | Every waste counter the definition knows, in percent | `Stopped` if unreadable |
| `read_bytes(printer, oids)` | EEPROM bytes as integers | `Stopped` if any byte is unreadable |
| `plan_reset(printer)` | A `Plan` for the definition's `raw_waste_reset` map (or zero for every main/borderless byte if there is none), with the current values | `Stopped` (read errors) |
| `plan_set_main(printer, percent)` | A `Plan` with only the main counter bytes that change: `round(percent × divider)` encoded least significant byte first over the main counter's OIDs | `Stopped` if out of 0–100, too large, or a changed byte is shared with the borderless counter |
| `plan_restore(printer, path, serial)` | A `Plan` that writes a backup's values back | `Stopped` if the backup belongs to another printer |
| `save_backup(host, serial, values)` | Write `backups/<serial>-<YYYYmmdd-HHMMSS>.json` (section 7) and return its path | — |
| `apply(printer, plan)` | Write each byte through `write_eeprom()`, which reads it back and fails if it didn't change | `WriteFailed` on the first failure |
| `local_networks()` | This computer's IPv4 networks from `psutil`: each adapter's own network if it's /22 or smaller, otherwise the /24 around the adapter's address; loopback and link-local skipped | — |
| `discover(networks, timeout, on_progress)` | Send an SNMP `sysDescr` request (1 s timeout, 1 retry, up to 300 in parallel) to every address in the networks; for answers containing "EPSON", also read model and printer name. Returns `FoundPrinter` entries sorted by address | — |

`Plan` holds a description, the `current` values (what a backup saves) and `changes` (`address -> (old, new)`). An empty `changes` means there is nothing to write.

### Command line (`reset_waste_ink.py`)

`cli(argv)` runs `main(argv)` and turns `Stopped` and `TimeoutError` into exit code 1 ("STOP: … Nothing was written to the printer."), and `WriteFailed` into exit code 2 (with the hint to restore the backup). `main` prints the identity, levels and planned changes, and asks for `yes` with `input()`.

### GUI (`gui.py`)

`App` (a `tk.Tk`) with `scan`, `add_manual`, `read_counters`, `set_main`, `full_reset` and `restore` as the button actions; `background(work, on_done)` runs network work in a thread; `_confirm(plan, backup)` saves the backup and shows the confirmation dialog.

## 6. Safety invariants

These must hold for every change to the tool, in both the command line and the GUI; the tests in `tests/` cover each of them.

1. **Nothing is written without an explicit confirmation:** typing `yes` on the command line, *Yes* in the GUI dialog. Reading is the default.
2. **A backup is saved before the first write** of a full reset or a main counter change.
3. **The memory layout is proven before writing:** the EEPROM serial (read with the definition's key and addresses) must equal the network or label serial.
4. **Only L3150-family printers are accepted.**
5. **Only definition addresses are written:** the reset map, the main counter bytes, or the addresses already in a backup. No user-supplied addresses.
6. **Every write is verified** by reading the byte back.
7. **Shared bytes are protected:** setting the main counter never changes a byte that the borderless counter also uses (address 47 on L3150).
11. **No changes before verification in the GUI:** the change buttons stay disabled until the selected printer has passed the serial check.
8. **A backup can only be restored to the printer it came from** (serial check).
9. **Idempotence:** setting a value that is already there writes nothing.
10. **The library is pinned** to a reviewed commit.

## 7. Data formats

### Backup file

```json
{
  "host": "192.168.223.1",
  "serial_number": "X6HF056082",
  "definition": "L3150",
  "created": "20261010-113953",
  "eeprom": { "48": 101, "49": 23, "47": 0 }
}
```

`eeprom` maps decimal address strings to byte values (0–255). Backups contain the printer's serial number and are kept out of git.

### L3150-family EEPROM layout used by the tool

| Address(es) | Use |
|---|---|
| 1604–1613 | Serial number, ASCII (verification only, never written) |
| 48, 49, 47 | Main waste counter, least significant byte first; percent = value ÷ 63.46 |
| 50, 51, 47 | Borderless waste counter, least significant byte first; percent = value ÷ 34.16 |
| 54, 55 | "Maintenance required level" of the 1st and 2nd waste counter (reset sets 94) |
| 52, 53, 28 | Reset to 0 by the definition's `raw_waste_reset`; meaning undocumented |

Read key `[151, 7]`, write key `Maribaya` (from the library definition).

Observed on the verified L3156 before any change: `47=0, 48=101, 49=23, 50=21, 51=0, 52=125, 53=39, 54=99, 55=94, 28=2` (main 94.37 %, borderless 0.61 %).

## 8. Testing

```powershell
.venv\Scripts\python -m unittest discover tests
```

Both test files replace the library's SNMP and EEPROM access with an in-memory printer seeded with the real L3156 byte values, and use a temporary backup folder, so they never touch a printer or the real `backups/`.

- `tests/test_simulated_printer.py` (command line, 11 tests): read-only, serial mismatch, wrong model, declined confirmation, missing network serial, full reset bytes and backup, `--set-main 50` writing only byte 49, idempotence, out-of-range percent, restore, and refusing another printer's backup.
- `tests/test_gui.py` (GUI, 7 tests): opens the real window hidden, simulates the button actions and answers the dialogs. Covers buttons disabled until verification, reading identity and counters, a serial mismatch keeping changes disabled, setting 50 % with the confirmation text, backup and write, declining, restoring, and listing scan results.

`discover()` itself needs a real network and is checked manually (section 9).

## 9. Manual verification on a real printer

Required for a new model, a new firmware, or a library upgrade:

1. Read-only run: model, printer name, firmware and both serial numbers print, and the serials match.
2. The percentages are plausible (compare with the printer's warning state).
3. `--set-main` with the current value: must report "Nothing to write".
4. A small `--set-main` change, then a read-only run: the new value is shown.
5. `--restore` the backup, then a read-only run: the original value is back.
6. Power-cycle the printer and read again: the value persisted.

Add the model and firmware to section 2 once verified.

For the GUI's scan: on the printer's network, *Scan network* must list the printer with its model and name.

## 10. Known limitations

- Network (SNMP) only; no USB.
- One printer family. Other families need their own definition and verification.
- Three addresses in the full reset (52, 53, 28) have no documented meaning; `--set-main` avoids them.
- A firmware update could change the read or write key. The serial check would then fail safely (nothing written).
- The waste ink pads themselves are not emptied.
- The scan only finds printers that answer SNMP with community `public` and only covers this computer's own networks (larger networks are limited to the /24 around the adapter). A printer elsewhere can still be added by IP address.
- A scan of several networks takes 10–20 seconds, because addresses that don't answer have to time out (1 s, 1 retry).

## 11. Possible additions

Ordered by usefulness versus risk.

| # | Addition | Notes |
|---|---|---|
| 1 | `--dry-run` | Run the whole flow including the backup and the planned-changes list, but skip writes. The library's `EpsonPrinter(dry_run=True)` already supports it. Low risk, high value before first use on a new printer. |
| 2 | `--set-borderless PERCENT`, or a generic `--set COUNTER PERCENT` | Same encoding as `main_counter_bytes`, using the borderless OIDs and divider. Must keep the shared-byte rule (47) in both directions. |
| 3 | Show more statistics | The definition's `stats` (total pages, scans, ink replacements, first-use time) are read-only and safe to display, e.g. with `--stats`. |
| 4 | Verified-firmware list | Record firmware versions that passed section 9; warn (not block) on an unknown firmware. |
| 5 | Action log | Append every write (time, serial, address, old, new, result) to `backups/actions.log` for an audit trail independent of backup files. |
| 6 | Faster or selectable scan | Let the user pick which network to scan (e.g. only the Wi-Fi), and remember the last printer found. |
| 7 | Monitoring | A read-only scheduled check that warns when the main counter passes a threshold (e.g. 80 %), as a reminder to service the pads or empty an external tank. |
| 8 | More models | Accept other epson_print_conf definitions selected by the reported model name, keeping the serial-number proof mandatory. Each model is "unverified" until section 9 is done. |
| 9 | USB connection | Upstream supports USB via the `epson-usb` package; needed for printers without network. Larger change: separate transport, Windows driver issues. |
| 10 | Pinned, hashed dependencies | Lock the build dependencies with hashes (`pip install --require-hashes`) so releases are reproducible. |
| 11 | Code signing | Sign the programs and the installer so Windows SmartScreen doesn't warn. Needs a code signing certificate. |

Done: printer discovery, a graphical interface, standalone programs and a Windows installer with GitHub release automation (all added after the first version of this spec).
