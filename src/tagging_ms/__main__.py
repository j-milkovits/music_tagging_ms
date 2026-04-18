from __future__ import annotations

import os

import uvicorn
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    host = os.getenv("TAGGING_MS_HOST", "127.0.0.1")
    port = int(os.getenv("TAGGING_MS_PORT", "8000"))
    uvicorn.run("tagging_ms.api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
