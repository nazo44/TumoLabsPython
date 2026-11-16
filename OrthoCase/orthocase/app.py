"""HTTP layer: clinic dashboard API, public patient-plan API, webhooks."""

from __future__ import annotations

import asyncio
import hmac
import io
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx
import segno
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import ai, i18n, planning, security
from .config import Config, load_config
from .db import Database, iso, utcnow
from .messaging import Messenger
from .service import Service, ServiceError

log = logging.getLogger("orthocase")

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS = 24
PUBLIC_EVENT_TYPES = {"sim_played", "option_selected", "compare_used"}

CSP = (
    "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def _process_image(data: bytes, dest: Path) -> int:
    """Validate an upload as a real image, drop its metadata (phone photos carry
    GPS coordinates) and store it as a reasonably sized JPEG."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as img:
        img.load()
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        img.thumbnail((2400, 2400))
        img.save(dest, format="JPEG", quality=90)
    return dest.stat().st_size


def create_app(
    config: Config | None = None,
    *,
    clock: Callable[[], datetime] = utcnow,
    http: httpx.AsyncClient | None = None,
    ai_client: Any = None,
) -> FastAPI:
    config = config or load_config()
    db = Database(config.db_path)
    messenger = Messenger(config, http)
    service = Service(db, config, messenger, clock)

    public_events = security.RateLimiter(120, 3600)
    public_actions = security.RateLimiter(12, 3600)

    async def scheduler_loop() -> None:
        while True:
            try:
                await service.run_followups()
            except Exception:
                log.exception("follow-up run failed")
            await asyncio.sleep(config.scheduler_interval)

    async def telegram_poll_loop() -> None:
        with suppress(Exception):
            await messenger.telegram_call("deleteWebhook", {})
        offset = db.kv_get("telegram_offset", 0)
        while True:
            try:
                updates = await messenger.telegram_call(
                    "getUpdates",
                    {"offset": offset, "timeout": 25, "allowed_updates": ["message", "callback_query"]},
                    timeout=35,
                )
            except Exception as exc:
                log.warning("Telegram polling error: %s", exc)
                await asyncio.sleep(5)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    await service.handle_telegram_update(update)
                except Exception:
                    log.exception("failed to handle Telegram update")
            if updates:
                db.kv_set("telegram_offset", offset)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks: list[asyncio.Task] = []
        if config.admin_password_generated:
            print(
                "\n  OrthoCase: no ORTHO_ADMIN_PASSWORD set. Dashboard password for this run:\n"
                f"      {config.admin_password}\n",
                flush=True,
            )
        if config.scheduler_enabled:
            tasks.append(asyncio.create_task(scheduler_loop()))
            if config.telegram_live and config.telegram_mode == "polling":
                tasks.append(asyncio.create_task(telegram_poll_loop()))
        yield
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await messenger.aclose()
        db.close()

    app = FastAPI(title="OrthoCase", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.config = config
    app.state.db = db
    app.state.service = service
    app.state.messenger = messenger

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # The plan link is a bearer token; never leak it through a Referer header.
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Content-Security-Policy", CSP)
        path = request.url.path
        if path.startswith(("/p/", "/api/")):
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    auth = Depends(security.require_auth)

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    @app.get("/")
    async def dashboard_page():
        return FileResponse(STATIC_DIR / "clinic" / "index.html")

    @app.get("/p/{token}")
    async def plan_page(token: str):
        return FileResponse(STATIC_DIR / "patient" / "plan.html")

    @app.get("/healthz")
    async def health():
        return {"ok": True}

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    @app.post("/api/login")
    async def login(request: Request):
        client = request.client.host if request.client else "unknown"
        if not security.login_allowed(client):
            raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
        body = await request.json()
        if not security.check_password(config.admin_password, str(body.get("password") or "")):
            security.record_failed_login(client)
            raise HTTPException(401, "Wrong password.")
        response = JSONResponse({"ok": True})
        response.set_cookie(
            security.COOKIE_NAME,
            security.make_session(config.secret_key),
            max_age=security.SESSION_SECONDS,
            httponly=True,
            samesite="lax",
            secure=config.secure_cookies,
        )
        return response

    @app.post("/api/logout")
    async def logout():
        response = JSONResponse({"ok": True})
        response.delete_cookie(security.COOKIE_NAME)
        return response

    @app.get("/api/me")
    async def me(request: Request):
        return {"authenticated": security.is_authenticated(request)}

    # ------------------------------------------------------------------
    # Clinic: settings and status
    # ------------------------------------------------------------------

    async def integration_status() -> dict:
        return {
            "public_url": config.public_url,
            "ai": {"enabled": config.ai_enabled, "model": config.ai_model if config.ai_enabled else None},
            "telegram": {
                "mode": messenger.mode("telegram"),
                "bot_username": await messenger.telegram_username(),
                "staff_alerts": bool(config.telegram_staff_chat_id and config.telegram_live),
            },
            "whatsapp": {
                "mode": messenger.mode("whatsapp"),
                "template_plan": config.whatsapp_template_plan or None,
                "template_followup": config.whatsapp_template_followup or None,
            },
            "scheduler": config.scheduler_enabled,
        }

    @app.get("/api/settings", dependencies=[auth])
    async def get_settings():
        return {
            "settings": service.settings(),
            "currencies": planning.CURRENCIES,
            "sim_ranges": planning.SIM_RANGES,
            "tooth_ranges": planning.TOOTH_RANGES,
            "findings_library": i18n.FINDINGS,
            "status": await integration_status(),
        }

    @app.put("/api/settings", dependencies=[auth])
    async def put_settings(request: Request):
        return {"settings": service.save_settings(await request.json())}

    # ------------------------------------------------------------------
    # Clinic: cases
    # ------------------------------------------------------------------

    def case_summary(case: dict) -> dict:
        options = (case["plan"] or {}).get("options") or []
        chosen = next((o for o in options if o["recommended"]), options[0] if options else None)
        return {
            "id": case["id"],
            "patient_name": case["patient_name"],
            "status": case["status"],
            "channel": case["channel"],
            "language": case["language"],
            "updated_at": case["updated_at"],
            "sent_at": case["sent_at"],
            "viewed_at": case["viewed_at"],
            "view_count": case["view_count"],
            "followup_step": case["followup_step"],
            "next_followup_at": case["next_followup_at"],
            "followups_paused": bool(case["followups_paused"]),
            "pending_question": bool(case["pending_question"]),
            "send_pending": bool(case["send_pending"]),
            "value": (case["booking"] or {}).get("amount") if case["status"] == "booked" else (chosen or {}).get("price"),
            "decline_reason": case["decline_reason"],
        }

    def attachment_view(row: dict) -> dict:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "filename": row["filename"],
            "shared": bool(row["shared"]),
            "caption": row["caption"],
            "url": f"/api/attachments/{row['id']}/file",
        }

    async def case_detail(case: dict) -> dict:
        settings = service.settings()
        bot = await messenger.telegram_username()
        currency = settings["currency"]
        return {
            **case_summary(case),
            "phone": case["phone"],
            "notes": case["notes"],
            "plan": case["plan"],
            "pricing": {o["id"]: planning.price_breakdown(o, currency) for o in case["plan"]["options"]},
            "draft_meta": case["draft_meta"],
            "booking": case["booking"],
            "booked_option": case["booked_option"],
            "opted_out": bool(case["opted_out"]),
            "tg_linked": bool(case["tg_chat_id"]),
            "plan_link": service.plan_link(case),
            "telegram_link": f"https://t.me/{bot}?start={case['link_code']}" if bot else None,
            "channel_mode": messenger.mode(case["channel"]),
            "attachments": [attachment_view(a) for a in db.attachments(case["id"])],
            "messages": db.messages(case["id"]),
            "events": db.events(case["id"]),
            "schedule": service.followup_schedule(case, settings),
        }

    @app.get("/api/cases", dependencies=[auth])
    async def list_cases():
        cases = db.list_cases()
        return {
            "cases": [case_summary(c) for c in cases],
            "stats": service.stats(cases),
            "currency": service.settings()["currency"],
        }

    @app.post("/api/cases", dependencies=[auth])
    async def create_case(request: Request):
        case = service.create_case(await request.json())
        return await case_detail(case)

    @app.get("/api/cases/{case_id}", dependencies=[auth])
    async def get_case(case_id: int):
        return await case_detail(service.require_case(case_id))

    @app.patch("/api/cases/{case_id}", dependencies=[auth])
    async def patch_case(case_id: int, request: Request):
        service.require_case(case_id)
        body = await request.json()
        updates: dict[str, Any] = {}
        if "patient_name" in body:
            name = str(body["patient_name"] or "").strip()[:80]
            if not name:
                raise HTTPException(400, "Patient name is required.")
            updates["patient_name"] = name
        if "phone" in body:
            updates["phone"] = str(body["phone"] or "").strip()[:40]
        if body.get("channel") in ("whatsapp", "telegram"):
            updates["channel"] = body["channel"]
        if body.get("language") in i18n.LANGUAGES:
            updates["language"] = body["language"]
        if "notes" in body:
            updates["notes"] = str(body["notes"] or "")[:8000]
        if "plan" in body:
            updates["plan"] = planning.normalize_plan(body["plan"])
        db.update_case(case_id, updates)
        return await case_detail(service.require_case(case_id))

    @app.delete("/api/cases/{case_id}", dependencies=[auth])
    async def delete_case(case_id: int):
        service.require_case(case_id)
        for attachment in db.attachments(case_id):
            Path(attachment["path"]).unlink(missing_ok=True)
        db.execute("DELETE FROM cases WHERE id = ?", [case_id])
        return {"ok": True}

    # -- records (X-rays, photos) -------------------------------------------

    @app.post("/api/cases/{case_id}/attachments", dependencies=[auth])
    async def upload_attachment(case_id: int, kind: str = "photo", file: UploadFile = File(...)):
        service.require_case(case_id)
        if len(db.attachments(case_id)) >= MAX_ATTACHMENTS:
            raise HTTPException(400, f"A case can hold up to {MAX_ATTACHMENTS} images.")
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Image is larger than 20 MB.")
        folder = config.upload_dir / str(case_id)
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{uuid.uuid4().hex}.jpg"
        try:
            size = await run_in_threadpool(_process_image, data, dest)
        except Exception:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, "That file isn't a readable image. Export X-rays as JPEG or PNG.")
        attachment_id = db.insert(
            "attachments",
            {
                "case_id": case_id,
                "kind": kind if kind in ("xray", "photo") else "photo",
                "filename": (file.filename or "image")[:120],
                "mime": "image/jpeg",
                "path": str(dest),
                "size": size,
                "created_at": iso(clock()),
            },
        )
        return attachment_view(db.one("SELECT * FROM attachments WHERE id = ?", [attachment_id]))

    def require_attachment(attachment_id: int) -> dict:
        row = db.one("SELECT * FROM attachments WHERE id = ?", [attachment_id])
        if row is None:
            raise HTTPException(404, "Image not found.")
        return row

    @app.get("/api/attachments/{attachment_id}/file", dependencies=[auth])
    async def attachment_file(attachment_id: int):
        row = require_attachment(attachment_id)
        return FileResponse(row["path"], media_type=row["mime"])

    @app.patch("/api/attachments/{attachment_id}", dependencies=[auth])
    async def patch_attachment(attachment_id: int, request: Request):
        require_attachment(attachment_id)
        body = await request.json()
        updates: dict[str, Any] = {}
        if body.get("kind") in ("xray", "photo"):
            updates["kind"] = body["kind"]
        if "shared" in body:
            updates["shared"] = 1 if body["shared"] else 0
        if "caption" in body:
            updates["caption"] = str(body["caption"] or "").strip()[:160]
        db.update("attachments", attachment_id, updates)
        return attachment_view(require_attachment(attachment_id))

    @app.delete("/api/attachments/{attachment_id}", dependencies=[auth])
    async def delete_attachment(attachment_id: int):
        row = require_attachment(attachment_id)
        Path(row["path"]).unlink(missing_ok=True)
        db.execute("DELETE FROM attachments WHERE id = ?", [attachment_id])
        return {"ok": True}

    # -- drafting -----------------------------------------------------------

    @app.post("/api/cases/{case_id}/draft", dependencies=[auth])
    async def draft_case(case_id: int):
        case = service.require_case(case_id)
        settings = service.settings()
        today = clock().date()
        meta: dict[str, Any] = {"at": iso(clock())}
        plan = None
        if config.ai_enabled:
            try:
                plan, checks = await ai.draft_plan(
                    case, db.attachments(case_id), settings, model=config.ai_model, today=today, client=ai_client
                )
                meta.update(source="ai", checks=checks)
            except ai.DraftError as exc:
                log.warning("AI draft failed for case %s: %s", case_id, exc)
                meta["notice"] = f"{exc} A template draft was used instead."
        if plan is None:
            plan = planning.template_draft(case, settings, today)
            meta.setdefault("notice", "AI drafting is off (no API key); this draft was built from keywords in your notes.")
            meta.update(source="template", checks=[])
        if (case["plan"] or {}).get("valid_until"):
            plan["valid_until"] = case["plan"]["valid_until"]
        db.update_case(case_id, {"plan": plan, "draft_meta": meta})
        db.add_event(case_id, "drafted", {"source": meta["source"]}, at=clock())
        return await case_detail(service.require_case(case_id))

    # -- delivery -----------------------------------------------------------

    @app.post("/api/cases/{case_id}/approve", dependencies=[auth])
    async def approve_case(case_id: int):
        return await case_detail(service.approve(case_id))

    @app.post("/api/cases/{case_id}/send", dependencies=[auth])
    async def send_case(case_id: int):
        result = await service.send_plan(case_id)
        return {"result": result, "case": await case_detail(service.require_case(case_id))}

    @app.post("/api/cases/{case_id}/followup-now", dependencies=[auth])
    async def followup_now(case_id: int):
        result = await service.send_followup_now(case_id)
        return {"result": result, "case": await case_detail(service.require_case(case_id))}

    @app.post("/api/cases/{case_id}/pause", dependencies=[auth])
    async def pause_case(case_id: int, request: Request):
        body = await request.json()
        service.set_paused(case_id, bool(body.get("paused")))
        return await case_detail(service.require_case(case_id))

    @app.post("/api/cases/{case_id}/reply", dependencies=[auth])
    async def reply_case(case_id: int, request: Request):
        body = await request.json()
        result = await service.reply(case_id, str(body.get("text") or ""))
        return {"result": result, "case": await case_detail(service.require_case(case_id))}

    @app.post("/api/cases/{case_id}/status", dependencies=[auth])
    async def status_case(case_id: int, request: Request):
        body = await request.json()
        service.set_status(case_id, str(body.get("status") or ""))
        return await case_detail(service.require_case(case_id))

    @app.get("/api/cases/{case_id}/qr.svg", dependencies=[auth])
    async def case_qr(case_id: int, target: str = "plan"):
        case = service.require_case(case_id)
        url = service.plan_link(case)
        if target == "telegram":
            bot = await messenger.telegram_username()
            if not bot:
                raise HTTPException(404, "Telegram is not connected.")
            url = f"https://t.me/{bot}?start={case['link_code']}"
        buffer = io.BytesIO()
        segno.make(url, error="m").save(buffer, kind="svg", scale=5, border=2, dark="#12313a")
        return Response(buffer.getvalue(), media_type="image/svg+xml")

    # ------------------------------------------------------------------
    # Public: the patient's plan
    # ------------------------------------------------------------------

    def public_case(token: str, request: Request) -> tuple[dict, bool]:
        case = db.get_case_by("token", token)
        preview = request.query_params.get("preview") == "1" and security.is_authenticated(request)
        if case is None or (case["status"] == "draft" and not preview):
            raise HTTPException(404, "Plan not found.")
        return case, preview

    @app.get("/api/public/{token}")
    async def public_plan(token: str, request: Request):
        case, preview = public_case(token, request)
        settings = service.settings()
        currency = settings["currency"]
        plan = case["plan"]
        options = [{**o, "pricing": planning.price_breakdown(o, currency)} for o in plan["options"]]
        shared = [a for a in db.attachments(case["id"]) if a["shared"]]
        suffix = "?preview=1" if preview else ""
        return {
            "preview": preview,
            "language": case["language"],
            "status": case["status"],
            "patient_name": i18n.first_name(case["patient_name"]),
            "clinic": {
                "name": settings["clinic_name"],
                "dentist": settings["dentist_name"],
                "phone": settings["phone"],
                "address": settings["address"],
                "currency": currency,
                "currency_symbol": planning.CURRENCIES[currency]["symbol"],
            },
            "plan": {
                "summary": plan["summary"],
                "findings": plan["findings"],
                "sim": plan["sim"],
                "options": options,
                "valid_until": plan["valid_until"],
            },
            "images": [
                {
                    "id": a["id"],
                    "kind": a["kind"],
                    "caption": a["caption"],
                    "url": f"/api/public/{token}/file/{a['id']}{suffix}",
                }
                for a in shared
            ],
            "booked_option": case["booked_option"] if case["status"] == "booked" else None,
        }

    @app.get("/api/public/{token}/file/{attachment_id}")
    async def public_file(token: str, attachment_id: int, request: Request):
        case, _ = public_case(token, request)
        row = db.one(
            "SELECT * FROM attachments WHERE id = ? AND case_id = ? AND shared = 1", [attachment_id, case["id"]]
        )
        if row is None:
            raise HTTPException(404, "Image not found.")
        return FileResponse(row["path"], media_type=row["mime"], headers={"Cache-Control": "private, max-age=3600"})

    def live_case(token: str) -> dict:
        case = db.get_case_by("token", token)
        if case is None or case["status"] == "draft":
            raise HTTPException(404, "Plan not found.")
        return case

    @app.post("/api/public/{token}/event")
    async def public_event(token: str, request: Request):
        case = live_case(token)
        if not public_events.allow(token):
            return {"ok": True}
        body = await request.json()
        kind = str(body.get("type") or "")
        if kind == "view":
            service.record_view(case)
        elif kind in PUBLIC_EVENT_TYPES:
            detail = str(body.get("detail") or "")[:80]
            db.add_event(case["id"], kind, {"detail": detail} if detail else None, at=clock())
        return {"ok": True}

    def limit_action(token: str) -> None:
        if not public_actions.allow(token):
            raise HTTPException(429, "Too many requests. Please try again later.")

    @app.post("/api/public/{token}/book")
    async def public_book(token: str, request: Request):
        case = live_case(token)
        limit_action(token)
        await service.book(case, await request.json())
        return {"ok": True}

    @app.post("/api/public/{token}/question")
    async def public_question(token: str, request: Request):
        case = live_case(token)
        limit_action(token)
        body = await request.json()
        await service.question(case, str(body.get("text") or ""), "page")
        return {"ok": True}

    @app.post("/api/public/{token}/decline")
    async def public_decline(token: str, request: Request):
        case = live_case(token)
        limit_action(token)
        body = await request.json()
        await service.decline(case, str(body.get("reason") or "other"), str(body.get("note") or ""), via="page")
        return {"ok": True}

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    @app.post("/webhooks/telegram")
    async def telegram_webhook(request: Request):
        secret = config.telegram_webhook_secret
        given = request.headers.get("x-telegram-bot-api-secret-token", "")
        if not secret or not hmac.compare_digest(secret.encode(), given.encode()):
            raise HTTPException(403, "Forbidden")
        await service.handle_telegram_update(await request.json())
        return {"ok": True}

    @app.get("/webhooks/whatsapp")
    async def whatsapp_verify(request: Request):
        params = request.query_params
        expected = config.whatsapp_verify_token
        if (
            expected
            and params.get("hub.mode") == "subscribe"
            and hmac.compare_digest(expected.encode(), params.get("hub.verify_token", "").encode())
        ):
            return PlainTextResponse(params.get("hub.challenge", ""))
        raise HTTPException(403, "Forbidden")

    @app.post("/webhooks/whatsapp")
    async def whatsapp_webhook(request: Request):
        raw = await request.body()
        if not messenger.verify_whatsapp_signature(raw, request.headers.get("x-hub-signature-256")):
            raise HTTPException(403, "Forbidden")
        await service.handle_whatsapp_payload(await request.json())
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
