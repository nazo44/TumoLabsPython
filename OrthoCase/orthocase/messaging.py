"""Outbound delivery over Telegram and WhatsApp, with a simulated fallback.

A channel is "live" when its credentials are configured. Otherwise messages
are recorded with status "simulated" so the whole flow can be exercised
without touching a real patient's phone.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from .config import Config

log = logging.getLogger("orthocase.messaging")

TELEGRAM_API = "https://api.telegram.org"
WHATSAPP_API = "https://graph.facebook.com"
WHATSAPP_WINDOW = timedelta(hours=24)

# WhatsApp template language codes for each plan language.
WHATSAPP_LANG = {"hy": "hy", "ru": "ru", "en": "en"}


@dataclass
class SendResult:
    status: str  # "sent" | "simulated" | "failed"
    provider_id: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("sent", "simulated")


def normalize_phone(phone: str, country_code: str) -> str:
    """Digits only, in international format without a plus (what WhatsApp expects)."""
    raw = phone.strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if raw.startswith("+"):
        return digits
    if digits.startswith("00"):
        return digits[2:]
    if digits.startswith("0"):
        return country_code + digits[1:]
    if len(digits) <= 9:  # local number without the trunk prefix
        return country_code + digits
    return digits


class Messenger:
    def __init__(self, config: Config, http: httpx.AsyncClient | None = None):
        self.config = config
        self.http = http or httpx.AsyncClient(timeout=20)
        self.bot_username: str | None = None
        self._username_checked_at = -1e9

    async def aclose(self) -> None:
        await self.http.aclose()

    def mode(self, channel: str) -> str:
        if channel == "telegram":
            return "live" if self.config.telegram_live else "simulated"
        if channel == "whatsapp":
            return "live" if self.config.whatsapp_live else "simulated"
        return "simulated"

    # -- Telegram ----------------------------------------------------------

    async def telegram_call(self, method: str, payload: dict, timeout: float = 20) -> dict:
        url = f"{TELEGRAM_API}/bot{self.config.telegram_token}/{method}"
        response = await self.http.post(url, json=payload, timeout=timeout)
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description") or f"Telegram error {response.status_code}")
        return data["result"]

    async def telegram_username(self) -> str | None:
        if not self.config.telegram_live:
            return None
        if self.bot_username is None and time.monotonic() - self._username_checked_at > 300:
            self._username_checked_at = time.monotonic()
            try:
                me = await self.telegram_call("getMe", {}, timeout=6)
                self.bot_username = me.get("username")
            except Exception as exc:  # bad token or no network; retried in a few minutes
                log.warning("Telegram getMe failed: %s", exc)
        return self.bot_username

    async def send_telegram(
        self, chat_id: str, text: str, *, link: str | None = None, link_label: str = "",
        decline: tuple[str, str] | None = None,
    ) -> SendResult:
        payload: dict = {
            "chat_id": chat_id,
            "text": text,
            "link_preview_options": {"is_disabled": True},
        }
        rows = []
        # Telegram rejects inline URL buttons that aren't public https links.
        if link and link.startswith("https://") and link_label:
            rows.append([{"text": link_label, "url": link}])
        if decline:
            rows.append([{"text": decline[0], "callback_data": decline[1]}])
        if rows:
            payload["reply_markup"] = {"inline_keyboard": rows}
        try:
            result = await self.telegram_call("sendMessage", payload)
        except Exception as exc:
            return SendResult("failed", error=str(exc)[:300])
        return SendResult("sent", provider_id=str(result.get("message_id")))

    # -- WhatsApp ----------------------------------------------------------

    def whatsapp_window_open(self, last_inbound_at: datetime | None, now: datetime) -> bool:
        return last_inbound_at is not None and now - last_inbound_at < WHATSAPP_WINDOW

    async def send_whatsapp(
        self, phone: str, text: str, *, template: str | None, language: str,
        template_params: list[str], url_suffix: str | None, window_open: bool,
    ) -> SendResult:
        """Inside the 24-hour customer-service window WhatsApp allows free text.
        Outside it, only a pre-approved template may be sent."""
        if not phone:
            return SendResult("failed", error="The patient has no phone number.")
        if window_open:
            body: dict = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": text, "preview_url": True},
            }
        else:
            if not template:
                return SendResult(
                    "failed",
                    error="WhatsApp only allows an approved template here, and no template name is configured.",
                )
            components: list[dict] = [
                {"type": "body", "parameters": [{"type": "text", "text": p} for p in template_params]}
            ]
            if url_suffix:
                components.append(
                    {
                        "type": "button",
                        "sub_type": "url",
                        "index": "0",
                        "parameters": [{"type": "text", "text": url_suffix}],
                    }
                )
            body = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "template",
                "template": {
                    "name": template,
                    "language": {"code": WHATSAPP_LANG.get(language, "en")},
                    "components": components,
                },
            }
        url = f"{WHATSAPP_API}/{self.config.whatsapp_api_version}/{self.config.whatsapp_phone_number_id}/messages"
        try:
            response = await self.http.post(
                url, json=body, headers={"Authorization": f"Bearer {self.config.whatsapp_token}"}
            )
            data = response.json()
        except Exception as exc:
            return SendResult("failed", error=str(exc)[:300])
        if response.status_code >= 400 or "error" in data:
            message = (data.get("error") or {}).get("message") or f"WhatsApp error {response.status_code}"
            return SendResult("failed", error=message[:300])
        ids = data.get("messages") or [{}]
        return SendResult("sent", provider_id=ids[0].get("id"))

    def verify_whatsapp_signature(self, body: bytes, header: str | None) -> bool:
        secret = self.config.whatsapp_app_secret
        if not secret:
            return False
        if not header or not header.startswith("sha256="):
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, header.removeprefix("sha256="))
