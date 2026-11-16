"""Start OrthoCase:  python run.py  (set HOST / PORT to change the address)."""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "orthocase.app:create_app",
        factory=True,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        log_level="info",
    )
