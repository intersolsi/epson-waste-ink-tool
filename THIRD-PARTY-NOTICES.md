# Third-party software

The Epson Waste Ink Tool itself is licensed under EUPL-1.2 (see `LICENSE`). The released programs (`EpsonWasteInk.exe`, `reset-waste-ink.exe`) are built with PyInstaller and include the following software.

| Component | Version | License | Source |
|---|---|---|---|
| epson_print_conf (vendored in `vendor/`) | 8.2.0, commit 6873e9e | EUPL-1.2 | https://github.com/Ircama/epson_print_conf |
| pysnmp-sync-adapter | 1.1.0 | EUPL-1.2 | https://github.com/Ircama/pysnmp-sync-adapter |
| PyPrintLpr | 1.1.1 | EUPL-1.2 | https://github.com/Ircama/PyPrintLpr |
| epson_escp2 | 1.0.4 | EUPL-1.2 | https://github.com/Ircama/epson_escp2 |
| pysnmp | 7.1.30 | BSD-2-Clause | https://github.com/lextudio/pysnmp |
| pyasn1 | 0.6.4 | BSD-2-Clause | https://github.com/pyasn1/pyasn1 |
| PyYAML | 6.0.3 | MIT | https://pyyaml.org/ |
| psutil | 7.2.2 | BSD-3-Clause | https://github.com/giampaolo/psutil |
| Pillow | 12.3.0 | MIT-CMU | https://python-pillow.org/ |
| hexdump2 | 1.2.2 | MIT | https://pypi.org/project/hexdump2/ |
| colorama | 0.4.6 | BSD-3-Clause | https://github.com/tartley/colorama |
| Python | 3.14 | PSF License | https://www.python.org/ |
| Tcl/Tk | 8.6 | Tcl/Tk License (BSD-style) | https://www.tcl.tk/ |

The full EUPL-1.2 text is in `licenses/epson_print_conf-EUPL-1.2.txt` (installed) and `vendor/LICENSE` (repository). The source code of the EUPL-licensed components is available at the URLs above.

Versions are those used for the 1.0.0 build; update this file when dependencies change.
