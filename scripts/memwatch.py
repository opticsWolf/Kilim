"""Offscreen leak bisection: run KilimWindow headless, pump events, log RSS."""

import ctypes
import sys
import time


class _Counters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def rss_mb():
    from ctypes import wintypes

    c = _Counters()
    c.cb = ctypes.sizeof(c)
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_Counters),
        wintypes.DWORD,
    ]
    h = ctypes.windll.kernel32.GetCurrentProcess()
    if psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
        return c.WorkingSetSize / 1048576
    return -1.0


layout = sys.argv[1] if len(sys.argv) > 1 else "layouts/default.json"
secs = int(sys.argv[2]) if len(sys.argv) > 2 else 30
pin = len(sys.argv) > 3 and sys.argv[3] == "pin"
print(f"START {layout} {secs}s pin={pin}", flush=True)

from PySide6.QtWidgets import QApplication

from kilim.qt_app import KilimWindow

app = QApplication([])
w = KilimWindow(layout)
w.show()
if pin:
    from lace.enums import DockWidgetArea

    w.manager.sidebar_manager.pin_widget(
        w.pane_docks["term1"], area=DockWidgetArea.left
    )
    print("term1 pinned left", flush=True)
t0 = time.time()
last = 0.0
while time.time() - t0 < secs:
    app.processEvents()
    time.sleep(0.05)
    if time.time() - last > 5:
        last = time.time()
        print(f"t={time.time() - t0:.0f}s RSS={rss_mb():.0f} MB", flush=True)
print(f"RSS after {secs}s offscreen ({layout}): {rss_mb():.0f} MB", flush=True)
