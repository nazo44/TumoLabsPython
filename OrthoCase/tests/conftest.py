from __future__ import annotations

import asyncio
import io
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from orthocase import security
from orthocase.app import create_app
from orthocase.config import Config
from orthocase.db import Database
from orthocase.messaging import Messenger
from orthocase.service import Service

# Monday 16 Nov 2026, 12:00 in Yerevan (UTC+4): inside the default 10:00-20:00 sending window.
START = datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc)
HEADERS = {"X-Requested-With": "orthocase"}


class Clock:
    def __init__(self) -> None:
        self.value = START

    def now(self) -> datetime:
        return self.value

    def advance(self, **kwargs) -> None:
        self.value += timedelta(**kwargs)

    def set(self, value: datetime) -> None:
        self.value = value


class Recorder:
    """httpx transport that records outbound provider calls and answers them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail_whatsapp = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.calls.append((str(request.url), body))
        if "api.telegram.org" in request.url.host:
            method = request.url.path.rsplit("/", 1)[-1]
            if method == "getMe":
                return httpx.Response(200, json={"ok": True, "result": {"username": "clinic_test_bot"}})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.calls)}})
        if self.fail_whatsapp:
            return httpx.Response(400, json={"error": {"message": "Template name does not exist"}})
        return httpx.Response(200, json={"messages": [{"id": f"wamid.{len(self.calls)}"}]})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def sent(self, fragment: str) -> list[dict]:
        return [body for url, body in self.calls if fragment in url]


def make_config(tmp_path, **overrides) -> Config:
    (tmp_path / "uploads").mkdir(exist_ok=True)
    values = dict(
        data_dir=tmp_path,
        public_url="https://plans.example.test",
        admin_password="correct horse",
        admin_password_generated=False,
        secret_key=b"k" * 32,
        ai_enabled=False,
        ai_model="claude-opus-5-5",
        telegram_token="",
        telegram_mode="polling",
        telegram_webhook_secret="",
        telegram_staff_chat_id="",
        whatsapp_token="",
        whatsapp_phone_number_id="",
        whatsapp_app_secret="",
        whatsapp_verify_token="",
        whatsapp_template_plan="",
        whatsapp_template_followup="",
        whatsapp_api_version="v21.0",
        scheduler_enabled=False,
        scheduler_interval=30.0,
    )
    values.update(overrides)
    return Config(**values)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture(autouse=True)
def _reset_login_limiter():
    security.reset_login_attempts()


@pytest.fixture
def make_service(tmp_path, clock, recorder):
    """Service wired to an in-memory database; pass config overrides for live channels."""

    def build(**overrides) -> Service:
        config = make_config(tmp_path, **overrides)
        return Service(Database(":memory:"), config, Messenger(config, recorder.client()), clock.now)

    return build


@pytest.fixture
def make_client(tmp_path, clock, recorder):
    """Signed-in TestClient; yields (client, app)."""
    opened: list[TestClient] = []

    def build(login: bool = True, ai_client=None, **overrides):
        app = create_app(make_config(tmp_path, **overrides), clock=clock.now, http=recorder.client(), ai_client=ai_client)
        client = TestClient(app, base_url="https://testserver")
        client.__enter__()
        opened.append(client)
        if login:
            response = client.post("/api/login", json={"password": "correct horse"})
            assert response.status_code == 200
        return client, app

    yield build
    for client in opened:
        client.__exit__(None, None, None)


def anonymous(app) -> TestClient:
    """A second browser with no session (a patient, or a stranger). It shares the
    running app, so it must not start or stop the app's lifespan itself."""
    return TestClient(app, base_url="https://testserver")


def png_bytes(size=(64, 48), exif: bool = False) -> bytes:
    from PIL import Image

    image = Image.new("RGB", size, (200, 180, 160))
    buffer = io.BytesIO()
    if exif:
        data = Image.Exif()
        data[0x010F] = "PhoneMaker"  # Make
        image.save(buffer, format="JPEG", exif=data)
    else:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def new_case(service: Service, **fields) -> dict:
    data = {"patient_name": "Mariam Hakobyan", "phone": "+374 91 555123", "channel": "whatsapp", "language": "en"}
    data.update(fields)
    case = service.create_case(data)
    plan = case["plan"]
    plan["summary"] = "Summary for the patient."
    service.db.update_case(case["id"], {"plan": plan})
    return service.require_case(case["id"])


def sent_case(service: Service, **fields) -> dict:
    case = new_case(service, **fields)
    service.approve(case["id"])
    result = run(service.send_plan(case["id"]))
    assert result["ok"] and not result.get("pending"), result
    return service.require_case(case["id"])
