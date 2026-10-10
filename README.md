# Epson L3150-family waste ink counter tool

Reads, backs up and resets the waste ink counters of an Epson EcoTank **L3150, L3151, L3156, L3160** (and other L3150-family models) over the network.

It uses the EEPROM definitions from [epson_print_conf](https://github.com/Ircama/epson_print_conf) (release 8.2.0, in `vendor/`, EUPL-1.2 license).

## Read this first

The counter only counts. Resetting it does **not** empty the waste ink pads inside the printer. If they are really full, ink can leak out of the bottom of the printer. Before or right after resetting, either:

- replace the waste ink pads, or
- fit an external waste ink tank (a common modification for EcoTank printers).

The L3156 is not listed by name in epson_print_conf; it uses the L3150 definition. That's why the tool first checks that the serial number stored in the printer's EEPROM matches the serial number the printer reports over the network (or the one on its label). If they don't match, it stops without writing anything.

## Install (Windows)

Download `EpsonWasteInk-<version>-setup.exe` from the [Releases](../../releases) page and run it. It installs for your user account only, so no administrator rights are needed, and adds a Start menu entry (and optionally a desktop icon). Uninstall it from *Settings → Apps*; backups in the program folder are kept.

Windows may show a SmartScreen warning ("Windows protected your PC") because the installer isn't code-signed. Click *More info → Run anyway*.

Prefer not to install? The release also has `EpsonWasteInk.exe` (GUI) and `reset-waste-ink.exe` (command line) as single portable files. They save backups in a `backups` folder next to themselves.

## Run from source

You need Python 3.10 or newer.

```powershell
cd D:\Development\tools\epson-waste-ink
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Graphical interface

```powershell
.venv\Scripts\python gui.py
```

1. **Printer:** click *Scan network* to find Epson printers on this computer's networks (about 10–20 seconds), or type the IP address and click *Add*.
2. **Counters:** select the printer and click *Read counters* (or double-click it). The serial number check runs first; the change buttons stay disabled until it passes.
3. **Change:** *Apply…* sets the main counter to the chosen percentage, *Full reset…* runs the full reset, *Restore backup…* writes a backup back. Each one saves a backup first (except restore), shows the exact bytes, and asks before writing.

Everything is also written to the log at the bottom.

The scan sends one read-only SNMP request to each address in this computer's own networks (each adapter's network up to /22, otherwise the /24 around the adapter's address). It doesn't reach other networks.

## Find the printer's IP address

Not needed with the GUI's scan. Otherwise, print a network status sheet from the printer (the button sequence is in the printer's manual under "printing a network status sheet"), or look the printer up in your router's list of connected devices. The printer and this computer must be on the same network. If you're connected to the printer's own Wi-Fi Direct network (`DIRECT-xx-EPSON-...`), the printer is at `192.168.223.1`.

## Command line

**1. Read only** (always safe, writes nothing):

```powershell
.venv\Scripts\python reset_waste_ink.py 192.168.1.50
```

It shows the model, firmware, serial number check and the current counter levels in percent. 100 % is where the printer stops.

**2. Reset:**

```powershell
.venv\Scripts\python reset_waste_ink.py 192.168.1.50 --reset
```

It saves a backup of every byte it is about to change to `backups\<serial>-<date>.json`, lists the exact changes, and only writes after you type `yes`. Every write is read back to confirm it took effect. Switch the printer off and on afterwards.

**Or set the main counter to a chosen level** instead of 0 %:

```powershell
.venv\Scripts\python reset_waste_ink.py 192.168.1.50 --set-main 50
```

This writes only the main counter bytes that need to change (usually one or two), leaves every other address alone, and refuses values that would touch a byte the borderless counter shares. A partial setting like 50 % keeps an early warning: the printer stops again after about half the original pad capacity, instead of only when the pads are completely saturated. Same backup and confirmation as `--reset`.

**3. Undo** (writes a backup back to the same printer):

```powershell
.venv\Scripts\python reset_waste_ink.py 192.168.1.50 --restore backups\X5NY012345-20261010-153000.json
```

Add `--debug` to any command to see the underlying SNMP traffic.

## What gets written

For the L3150 family, the reset sets these EEPROM addresses (taken from the definition, which the tool shows again before writing):

| Address | New value | Meaning |
|---|---|---|
| 47, 48, 49 | 0 | main waste ink counter |
| 50, 51 | 0 | borderless waste ink counter (shares byte 47) |
| 54, 55 | 94 | "maintenance required level" of the 1st and 2nd waste ink counters |
| 52, 53, 28 | 0 | reset by the epson_print_conf definition; their meaning isn't documented |

## Building a release

```powershell
.venv\Scripts\python -m pip install -r requirements-build.txt
winget install JRSoftware.InnoSetup      # once, for the installer
.\build.ps1
```

`build.ps1` runs the tests, builds both programs with PyInstaller and the installer with Inno Setup (`installer.iss`). Everything lands in `dist\`.

To publish on GitHub, raise `__version__` in `waste_ink.py`, commit, and push a matching tag:

```powershell
git tag v1.0.0
git push origin v1.0.0
```

The `Release` workflow (`.github/workflows/release.yml`) then builds everything on a Windows runner and creates the GitHub release with the installer and both portable programs attached.

## Tests

The tests run against a simulated printer, so no printer or network is needed:

```powershell
.venv\Scripts\python -m unittest discover tests
```

See [SPEC.md](SPEC.md) for the full specification: commands, functions, safety rules, EEPROM layout, and planned additions.

## Troubleshooting

- **"The printer did not respond"**: check the IP address, that the printer is on, and that SNMP is not disabled in the printer's web settings (open the IP address in a browser).
- **"Could not read a valid serial number from the EEPROM"**: the L3150 read key doesn't work on this printer, possibly because of a newer firmware. Nothing was written. Check the epson_print_conf issues page for your model and firmware.
- **"The serial numbers differ"**: the memory layout doesn't match. Don't force it.
