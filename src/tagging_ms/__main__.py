from __future__ import annotations

import logging
import os

import uvicorn
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    # uvicorn configures only its own loggers; give the application's a handler.
    logging.basicConfig(
        level=os.getenv("TAGGING_MS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    host = os.getenv("TAGGING_MS_HOST", "127.0.0.1")
    port = int(os.getenv("TAGGING_MS_PORT", "8000"))
    uvicorn.run(
        "tagging_ms.api:app",
        host=host,
        port=port,
        reload=False,
        # Bound the number of in-flight requests so a burst of slow upstream
        # lookups cannot pin the process; excess requests get 503 immediately.
        limit_concurrency=int(os.getenv("TAGGING_MS_MAX_CONCURRENT_REQUESTS", "32")),
        timeout_keep_alive=5,
    )


if __name__ == "__main__":
    main()
