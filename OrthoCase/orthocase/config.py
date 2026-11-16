"""Runtime configuration, read from environment variables.

Nothing here is a secret default: when a password or signing key is not
provided, a random one is generated instead of falling back to a known value.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    data_dir: Path
    public_url: str
    admin_password: str
    admin_password_generated: bool
    secret_key: bytes

    ai_enabled: bool
    ai_model: str

    telegram_token: str
    telegram_mode: str  # "polling" | "webhook"
    telegram_webhook_secret: str
    telegram_staff_chat_id: str

    whatsapp_token: str
    whatsapp_phone_number_id: str
    whatsapp_app_secret: str
    whatsapp_verify_token: str
    whatsapp_template_plan: str
    whatsapp_template_followup: str
    whatsapp_api_version: str

    scheduler_enabled: bool
    scheduler_interval: float

    @property
    def db_path(self) -> Path:
        return self.data_dir / "orthocase.sqlite3"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def telegram_live(self) -> bool:
        return bool(self.telegram_token)

    @property
    def whatsapp_live(self) -> bool:
        return bool(self.whatsapp_token and self.whatsapp_phone_number_id)

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")


def _load_secret_key(data_dir: Path) -> bytes:
    env = os.environ.get("ORTHO_SECRET_KEY")
    if env:
        return env.encode()
    key_file = data_dir / "secret.key"
    if key_file.exists():
        return key_file.read_bytes()
    key = secrets.token_bytes(32)
    key_file.write_bytes(key)
    try:
        key_file.chmod(0o600)
    except OSError:
        pass
    return key


def load_config() -> Config:
    data_dir = Path(os.environ.get("ORTHO_DATA_DIR", "data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "uploads").mkdir(exist_ok=True)

    password = os.environ.get("ORTHO_ADMIN_PASSWORD", "")
    generated = False
    if not password:
        password = secrets.token_urlsafe(9)
        generated = True

    has_ai_credentials = bool(
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )

    return Config(
        data_dir=data_dir,
        public_url=os.environ.get("ORTHO_PUBLIC_URL", "http://localhost:8000").rstrip("/"),
        admin_password=password,
        admin_password_generated=generated,
        secret_key=_load_secret_key(data_dir),
        ai_enabled=_flag("ORTHO_AI", has_ai_credentials),
        ai_model=os.environ.get("ORTHO_AI_MODEL", "claude-opus-5-5"),
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_mode=os.environ.get("TELEGRAM_MODE", "polling"),
        telegram_webhook_secret=os.environ.get("TELEGRAM_WEBHOOK_SECRET", ""),
        telegram_staff_chat_id=os.environ.get("TELEGRAM_STAFF_CHAT_ID", ""),
        whatsapp_token=os.environ.get("WHATSAPP_TOKEN", ""),
        whatsapp_phone_number_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID", ""),
        whatsapp_app_secret=os.environ.get("WHATSAPP_APP_SECRET", ""),
        whatsapp_verify_token=os.environ.get("WHATSAPP_VERIFY_TOKEN", ""),
        whatsapp_template_plan=os.environ.get("WHATSAPP_TEMPLATE_PLAN", ""),
        whatsapp_template_followup=os.environ.get("WHATSAPP_TEMPLATE_FOLLOWUP", ""),
        whatsapp_api_version=os.environ.get("WHATSAPP_API_VERSION", "v21.0"),
        scheduler_enabled=_flag("ORTHO_SCHEDULER", True),
        scheduler_interval=float(os.environ.get("ORTHO_SCHEDULER_INTERVAL", "30")),
    )
