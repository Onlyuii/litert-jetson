from __future__ import annotations

import signal
import threading
from contextlib import suppress

from .config import parse_args
from .http_api import LiteRTOpenAIServer


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    server = LiteRTOpenAIServer((config.bind, config.port), config)

    def shutdown_handler(signum: int, _frame: object) -> None:
        print(f"Received signal {signum}, shutting down.", flush=True)

        def _shutdown() -> None:
            with suppress(Exception):
                server.shutdown()

        threading.Thread(target=_shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    print(
        "LiteRT server listening on "
        f"http://{config.bind}:{config.port} "
        f"using worker {config.worker_binary}",
        flush=True,
    )

    try:
        server.serve_forever()
    finally:
        server.session_manager.close_all()
        server.server_close()
    return 0
