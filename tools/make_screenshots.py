"""
Regenerate the README screenshots by driving the real GUI against the
simulated printer from the tests (made-up serial number, nothing touches a
real printer):

    .venv\\Scripts\\python tools\\make_screenshots.py

Writes docs/screenshots/*.png. Windows only; the window briefly appears on screen.
"""

import ctypes
import os
import ipaddress
import pathlib
import sys
import threading
import time
from ctypes import wintypes
from unittest import mock

ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixel coordinates on scaled displays

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from PIL import Image  # noqa: E402  (installed as a dependency of epson_escp2)

import gui  # noqa: E402
import waste_ink  # noqa: E402
from test_simulated_printer import FakePrinter, REAL_BYTES, SERIAL  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
user32 = ctypes.windll.user32
WM_COMMAND, IDYES = 0x0111, 6


def own_windows(title: str) -> list[int]:
    """Visible top-level windows of this process with this title (other programs may use the same title)."""
    matches = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def check(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        text = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, text, 256)
        if pid.value == os.getpid() and text.value == title and user32.IsWindowVisible(hwnd):
            matches.append(hwnd)
        return True

    user32.EnumWindows(check, 0)
    return matches


def find_window(title: str, timeout: float = 10) -> int:
    """Wait for this process's dialog to be visible: dialogs exist briefly before they are shown and take input."""
    end = time.time() + timeout
    while time.time() < end:
        matches = own_windows(title)
        if matches:
            time.sleep(0.5)
            return matches[0]
        time.sleep(0.1)
    raise RuntimeError(f"window '{title}' did not appear")


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


gdi32 = ctypes.windll.gdi32
user32.GetWindowDC.restype = wintypes.HDC
user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]


def grab(hwnd: int, name: str) -> None:
    """
    Capture one window with PrintWindow, which asks the window to draw itself.
    Unlike copying the screen, this works when other windows cover it, so the
    script doesn't need to bring anything to the front.
    """
    window, frame = wintypes.RECT(), wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(window))
    # The visible frame, without the invisible resize borders and shadow.
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(frame), ctypes.sizeof(frame))
    w, h = window.right - window.left, window.bottom - window.top

    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, w, h)
    gdi32.SelectObject(memory_dc, bitmap)
    user32.PrintWindow(hwnd, memory_dc, 2)  # PW_RENDERFULLCONTENT
    header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(memory_dc, bitmap, 0, h, pixels, ctypes.byref(header), 0)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory_dc)
    user32.ReleaseDC(hwnd, window_dc)

    image = Image.frombuffer("RGB", (w, h), pixels, "raw", "BGRX", 0, 1)
    image.crop((frame.left - window.left, frame.top - window.top,
                frame.right - window.left, frame.bottom - window.top)).save(OUT / name)
    print("saved", name, flush=True)


def answer_dialogs() -> None:
    """Runs in a thread while the GUI waits in its confirmation dialog: capture it, then click Yes."""
    print("waiting for the confirmation dialog", flush=True)
    confirm = find_window("Write to the printer?")
    grab(confirm, "2-confirm.png")
    user32.PostMessageW(confirm, WM_COMMAND, IDYES, 0)
    print("clicked Yes", flush=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    FakePrinter.eeprom = {1604 + i: ord(c) for i, c in enumerate(SERIAL)} | REAL_BYTES
    FakePrinter.writes = []
    FakePrinter.snmp = {
        "Model": "EPSON L3150 Series", "Epson Printer Name": "L3156",
        "EEPS2 firmware version": "EEPS2 Hard Ver.1.00 Firm Ver.0.39", "Serial Number": SERIAL,
    }
    found = [waste_ink.FoundPrinter("192.168.1.42", "EPSON Built-in 11b/g/n Print Server", "EPSON L3150 Series", "L3156")]
    # Show a neutral backup path (no real user name) and don't write a file.
    demo_backup = pathlib.PureWindowsPath(
        r"C:\Users\you\AppData\Local\Programs\Epson Waste Ink Tool\backups\X5NY012345-20261010-185829.json")

    with mock.patch.object(waste_ink, "EpsonPrinter", FakePrinter), \
            mock.patch.object(waste_ink, "save_backup", return_value=demo_backup), \
            mock.patch.object(gui.messagebox, "showinfo"), \
            mock.patch.object(waste_ink, "local_networks", return_value=[ipaddress.ip_network("192.168.1.0/24")]), \
            mock.patch.object(waste_ink, "discover", side_effect=lambda *a, **k: (time.sleep(0.5), found)[1]):
        app = gui.App()
        app.geometry("+80+80")
        app.update()
        # This window's own handle: another copy of the app may be open with the same title.
        window = user32.GetParent(app.winfo_id())

        def settle(seconds=2.0):
            end = time.time() + seconds
            while time.time() < end or app.busy:
                app.update()
                time.sleep(0.02)

        settle(1)
        app.scan()
        settle()
        app.read_counters()
        settle()
        grab(window, "1-counters.png")

        app.target.set("50")
        threading.Thread(target=answer_dialogs, daemon=True).start()
        app.set_main()
        settle(4)
        grab(window, "3-after.png")
        app.destroy()


if __name__ == "__main__":
    main()
