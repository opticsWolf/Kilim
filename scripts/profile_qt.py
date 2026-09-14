"""Qt perf profile (dev tool, not tests): bridge + render costs, idle and burst.

Run:  QT_QPA_PLATFORM=offscreen uv run python scripts/profile_qt.py
"""

from __future__ import annotations

import statistics
import time

from PySide6.QtWidgets import QApplication

from kilim.qt_app import KilimWindow

RENDER_TIMES: list[float] = []
SNAP_TIMES: list[float] = []


def main() -> None:
    app = QApplication([])
    w = KilimWindow("layouts/default.json")
    w.show()
    term = next(iter(w.term_panes.values()))

    # Instrument _render and _snapshot.
    orig_render = term._render
    orig_snapshot = term._snapshot

    def timed_render(snap):
        t0 = time.perf_counter()
        orig_render(snap)
        RENDER_TIMES.append((time.perf_counter() - t0) * 1000)

    async def timed_snapshot(rows):
        t0 = time.perf_counter()
        try:
            return await orig_snapshot(rows)
        finally:
            SNAP_TIMES.append((time.perf_counter() - t0) * 1000)

    term._render = timed_render
    term._snapshot = timed_snapshot

    def settle(secs: float):
        end = time.perf_counter() + secs
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.01)

    def report(tag: str):
        r = RENDER_TIMES.copy()
        s = SNAP_TIMES.copy()
        RENDER_TIMES.clear()
        SNAP_TIMES.clear()
        paints = term._render_count
        print(f"--- {tag} ---")
        if r:
            print(f"render ms: n={len(r)} p50={statistics.median(r):.2f} p95={_p95(r):.2f} max={max(r):.2f}")
        else:
            print("render: no paints (idle-skip active)" if paints else "render: none yet")
        if s:
            print(f"snap   ms: n={len(s)} p50={statistics.median(s):.2f} p95={_p95(s):.2f} max={max(s):.2f}")
        print(f"paints so far: {paints}, cells/frame: {term._grid()[0]}x{term._grid()[1]}")

    settle(2.0)
    report("idle powershell")

    # Burst: 600 lines of mixed-style output.
    w.bridge.call(lambda: w.bridge.core.write_term(
        "term1", b"1..600 | % { \"line $_ abcdefghij KLMNOP\" }\r"))
    settle(4.0)
    report("burst 600 lines")

    # Input latency: time a keystroke submission round-trip.
    lat = []
    for _ in range(5):
        t0 = time.perf_counter()
        w.bridge.call(lambda: w.bridge.core.write_term("term1", b" "))
        lat.append((time.perf_counter() - t0) * 1000)
    print(f"--- input round-trip ms: median={statistics.median(lat):.2f} max={max(lat):.2f} ---")

    settle(2.0)
    report("settle after burst")
    w.close()
    app.processEvents()


def _p95(xs: list[float]) -> float:
    return sorted(xs)[max(0, int(len(xs) * 0.95) - 1)]


if __name__ == "__main__":
    main()
