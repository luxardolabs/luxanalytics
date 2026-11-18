"""A TCP proxy in front of the test Redis whose replies can be held on command: a stalled Redis that
the app's real clients talk to, with real socket timeouts and no load-dependent busy script.

It runs on its own thread and event loop, so a client that blocks the test's loop (slowapi's
synchronous Redis client) still meets a proxy that answers, or holds, as told."""

import asyncio
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit


class StallingProxy:
    """Forwards every connection to Redis. While stalled, replies are held (not dropped) until
    `resume()`; commands still reach Redis and run. With `delay` set, every reply is `delay`
    seconds late: a Redis that is slow but answers."""

    delay = 0.0

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.port_listening = 0
        self._flowing = threading.Event()
        self._flowing.set()
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._server: asyncio.Server | None = None

    @property
    def netloc(self) -> str:
        return f"127.0.0.1:{self.port_listening}"

    @property
    def url(self) -> str:
        return f"redis://{self.netloc}/0"

    def stall(self) -> None:
        self._flowing.clear()

    def resume(self) -> None:
        self._flowing.set()

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(5)

    def stop(self) -> None:
        self.resume()
        asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop).result(5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)
        self._loop.close()

    async def _shutdown(self) -> None:
        if self._server is not None:
            self._server.close()
        current = asyncio.current_task()
        tasks = [task for task in asyncio.all_tasks() if task is not current]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._server = self._loop.run_until_complete(
            asyncio.start_server(self._serve, "127.0.0.1", 0)
        )
        self.port_listening = self._server.sockets[0].getsockname()[1]
        self._ready.set()
        self._loop.run_forever()

    async def _serve(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        redis_reader, redis_writer = await asyncio.open_connection(self.host, self.port)

        async def forward(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter, held: bool
        ) -> None:
            try:
                while data := await reader.read(65536):
                    while held and not self._flowing.is_set():
                        await asyncio.sleep(0.01)
                    if held and self.delay:
                        await asyncio.sleep(self.delay)
                    writer.write(data)
                    await writer.drain()
            except OSError:
                pass
            finally:
                writer.close()

        await asyncio.gather(
            forward(client_reader, redis_writer, held=False),
            forward(redis_reader, client_writer, held=True),
        )


@contextmanager
def stalling_proxy(redis_url: str) -> Iterator[StallingProxy]:
    parts = urlsplit(redis_url)
    proxy = StallingProxy(parts.hostname or "", parts.port or 6379)
    proxy.start()
    try:
        yield proxy
    finally:
        proxy.stop()
