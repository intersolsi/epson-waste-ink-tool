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
reset_waste_ink.py          CLI and all safety logic (this project)
vendor/epson_print_conf.py  SNMP + EEPROM access and printer definitions
                            (Ircama/epson_print_conf release 8.2.0, commit 6873e9e, EUPL-1.2)
tests/                      unit tests against a simulated printer
backups/                    JSON backups written before any change (not in git)
```

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
2. **Identify** (section 5, `identify`): model check, EEPROM serial, network serial, comparison.
3. Show current counter levels.
4. Command-specific steps below. Nothing is written in read-only mode.

| Command | Reads | Backs up | Writes |
|---|---|---|---|
| read only | counters | no | nothing |
| `--reset` | every address in the reset map | those addresses | the reset map (10 bytes on L3150) |
| `--set-main P` | main counter bytes (48, 49, 47) | those 3 bytes | only bytes whose value changes (often 1) |
| `--restore F` | the addresses in the backup | no | the backup's values, after checking its serial |

Every write path shows `address: current -> new` for each byte and requires the user to type `yes`.

## 5. Functions

| Function | Responsibility | Fails with |
|---|---|---|
| `connect(host)` | Create an `EpsonPrinter` for the `L3150` definition (timeout 5 s, 2 retries) and register the Printer-MIB serial number OID `1.3.6.1.2.1.43.5.1.1.17.1` | exit 1 if the definition is missing |
| `snmp_value(printer, name)` | Read one SNMP value; a timeout returns `None`. Values are read one by one because the L3156 never answers some of the library's other OIDs, which made a bulk read time out | never raises on timeout |
| `identify(printer)` | Print model, printer name, firmware; refuse non-`L31` models; read the serial number from EEPROM (1604–1613) with the L3150 read key; compare it with the network serial, or ask the user to compare it with the label. Returns the serial number | exit 1 on any mismatch or unreadable serial |
| `show_levels(printer, title)` | Print every waste counter the definition knows, in percent | exit 1 if unreadable |
| `addresses_to_reset(printer)` | The `raw_waste_reset` map of the definition, or zero for every main/borderless counter byte if there is none | — |
| `main_counter_bytes(printer, percent, current)` | Encode `round(percent × divider)` little-endian over the main counter's OIDs; return only bytes that differ from `current` | exit 1 if a changed byte is shared with the borderless counter, or the value doesn't fit |
| `read_bytes(printer, oids)` | Read EEPROM bytes as integers | exit 1 if any byte is unreadable |
| `backup(host, serial, values)` | Write `backups/<serial>-<YYYYmmdd-HHMMSS>.json` (format in section 7) and return its path | — |
| `confirm(changes)` | List the planned bytes and require `yes` | exit 1 on any other answer |
| `write(printer, changes)` | Write each byte through `write_eeprom()`, which reads it back and fails if it didn't change | exit 2 on the first failure or exception |
| `fail(message)` | Print `STOP: ...` and `Nothing was written to the printer.` | exit 1 |
| `main()` | Parse arguments and run the flow in section 4. A `TimeoutError` outside `write()` exits 1 with "The printer did not respond" | — |

## 6. Safety invariants

These must hold for every change to the tool; the tests in `tests/` cover each of them.

1. **Nothing is written without an explicit `yes`.** Read-only is the default.
2. **A backup is saved before the first write** of `--reset` and `--set-main`.
3. **The memory layout is proven before writing:** the EEPROM serial (read with the definition's key and addresses) must equal the network or label serial.
4. **Only L3150-family printers are accepted.**
5. **Only definition addresses are written:** the reset map, the main counter bytes, or the addresses already in a backup. No user-supplied addresses.
6. **Every write is verified** by reading the byte back.
7. **Shared bytes are protected:** `--set-main` never changes a byte that the borderless counter also uses (address 47 on L3150).
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

`tests/test_simulated_printer.py` replaces the library's SNMP and EEPROM access with an in-memory printer seeded with the real L3156 byte values, and uses a temporary backup folder, so it never touches a printer or the real `backups/`. It covers: read-only, serial mismatch, wrong model, declined confirmation, missing network serial, full reset bytes and backup, `--set-main 50` writing only byte 49, idempotence, out-of-range percent, restore, and refusing another printer's backup.

## 9. Manual verification on a real printer

Required for a new model, a new firmware, or a library upgrade:

1. Read-only run: model, printer name, firmware and both serial numbers print, and the serials match.
2. The percentages are plausible (compare with the printer's warning state).
3. `--set-main` with the current value: must report "Nothing to write".
4. A small `--set-main` change, then a read-only run: the new value is shown.
5. `--restore` the backup, then a read-only run: the original value is back.
6. Power-cycle the printer and read again: the value persisted.

Add the model and firmware to section 2 once verified.

## 10. Known limitations

- Network (SNMP) only; no USB.
- One printer family. Other families need their own definition and verification.
- Three addresses in the full reset (52, 53, 28) have no documented meaning; `--set-main` avoids them.
- A firmware update could change the read or write key. The serial check would then fail safely (nothing written).
- The waste ink pads themselves are not emptied.

## 11. Possible additions

Ordered by usefulness versus risk.

| # | Addition | Notes |
|---|---|---|
| 1 | `--dry-run` | Run the whole flow including the backup and the planned-changes list, but skip writes. The library's `EpsonPrinter(dry_run=True)` already supports it. Low risk, high value before first use on a new printer. |
| 2 | `--set-borderless PERCENT`, or a generic `--set COUNTER PERCENT` | Same encoding as `main_counter_bytes`, using the borderless OIDs and divider. Must keep the shared-byte rule (47) in both directions. |
| 3 | Show more statistics | The definition's `stats` (total pages, scans, ink replacements, first-use time) are read-only and safe to display, e.g. with `--stats`. |
| 4 | Verified-firmware list | Record firmware versions that passed section 9; warn (not block) on an unknown firmware. |
| 5 | Action log | Append every write (time, serial, address, old, new, result) to `backups/actions.log` for an audit trail independent of backup files. |
| 6 | Printer discovery | Find Epson printers on the local network via SNMP (`sysDescr` contains "EPSON") so the user doesn't need the IP address. Must not scan beyond the local subnet. |
| 7 | Monitoring | A read-only scheduled check that warns when the main counter passes a threshold (e.g. 80 %), as a reminder to service the pads or empty an external tank. |
| 8 | More models | Accept other epson_print_conf definitions selected by the reported model name, keeping the serial-number proof mandatory. Each model is "unverified" until section 9 is done. |
| 9 | USB connection | Upstream supports USB via the `epson-usb` package; needed for printers without network. Larger change: separate transport, Windows driver issues. |
| 10 | Packaging | `pyproject.toml` with a console command and pinned, hashed dependencies (`pip install --require-hashes`). |
| 11 | Graphical interface | Only after the CLI is stable; the safety flow (checks, backup, explicit confirmation) must stay identical. |
