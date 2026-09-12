from __future__ import annotations

import os

import uvicorn
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
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
