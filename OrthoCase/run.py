"""Start OrthoCase:  python run.py

Settings come from environment variables; a `.env` file next to this script is
read too (see .env.example). HOST and PORT choose the listening address.
"""

import os
from pathlib import Path

import uvicorn


def load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE reader. Real environment variables win over the file."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value:  # an empty line in .env means "not set"
            os.environ.setdefault(key.strip(), value)


if __name__ == "__main__":
    load_dotenv(Path(__file__).with_name(".env"))
    uvicorn.run(
        "orthocase.app:create_app",
        factory=True,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        log_level="info",
    )
