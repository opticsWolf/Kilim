"""CoreSession plus a background asyncio loop for the Qt surface."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading

from kilim import CoreSession


class Bridge:
    """CoreSession + background asyncio loop. GUI thread calls submit()/call().

    All core coroutines are *created* on the background loop (inside driver),
    because `future_into_py` requires a running loop in the creating thread.
    Call sites always pass a zero-arg lambda, never a pre-made coroutine.
    """

    def __init__(self, layout_doc: str):
        self.core = CoreSession(layout_doc)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, make_coro) -> concurrent.futures.Future:
        out: concurrent.futures.Future = concurrent.futures.Future()

        async def driver():
            try:
                out.set_result(await make_coro())
            except BaseException as e:  # noqa: BLE001
                out.set_exception(e)

        asyncio.run_coroutine_threadsafe(driver(), self.loop)
        return out

    def call(self, make_coro, timeout: float = 15.0):
        return self.submit(make_coro).result(timeout)

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
