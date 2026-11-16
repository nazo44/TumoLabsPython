"""Case lifecycle: sending the plan, automatic follow-ups, and everything a
patient can do in response (view, ask, book, decline, opt out).

Status flow:  draft -> approved -> sent -> viewed -> booked | declined | expired
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from . import i18n, planning
from .config import Config
from .db import Database, iso, parse_iso, utcnow
from .messaging import Messenger, SendResult, normalize_phone

log = logging.getLogger("orthocase.service")

OPEN_STATUSES = ("sent", "viewed")
DECLINE_REASONS = ("price", "timing", "other_clinic", "not_interested", "other", "opt_out")
MAX_FOLLOWUP_FAILURES = 3
# Acknowledgements are a courtesy; if the channel can't carry them they are
# skipped instead of being reported as failures.
COURTESY_KINDS = ("booked_confirmation", "declined_ack", "question_ack", "system")


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class Service:
    def __init__(
        self,
        db: Database,
        config: Config,
        messenger: Messenger,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.db = db
        self.config = config
        self.messenger = messenger
        self.clock = clock

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def settings(self) -> dict:
        return planning.normalize_settings(self.db.kv_get("settings"))

    def save_settings(self, raw: dict) -> dict:
        settings = planning.normalize_settings(raw)
        self.db.kv_set("settings", settings)
        return settings

    # ------------------------------------------------------------------
    # Cases
    # ------------------------------------------------------------------

    def create_case(self, data: dict) -> dict:
        settings = self.settings()
        name = str(data.get("patient_name") or "").strip()[:80]
        if not name:
            raise ServiceError("Patient name is required.")
        language = i18n.lang_of(data.get("language") or settings["default_language"])
        now = self.clock()
        case_id = self.db.insert(
            "cases",
            {
                "token": secrets.token_urlsafe(24),
                "link_code": secrets.token_urlsafe(9),
                "patient_name": name,
                "phone": str(data.get("phone") or "").strip()[:40],
                "channel": data.get("channel") if data.get("channel") in ("whatsapp", "telegram") else "whatsapp",
                "language": language,
                "notes": str(data.get("notes") or "")[:8000],
                "status": "draft",
                "plan": "{}",
                "created_at": iso(now),
                "updated_at": iso(now),
            },
        )
        self.db.update_case(case_id, {"plan": planning.empty_plan(settings, language, now.date())})
        self.db.add_event(case_id, "created", at=now)
        return self.require_case(case_id)

    def require_case(self, case_id: int) -> dict:
        case = self.db.get_case(case_id)
        if case is None:
            raise ServiceError("Case not found.", 404)
        return case

    def plan_link(self, case: dict) -> str:
        return f"{self.config.public_url}/p/{case['token']}"

    def message_values(self, case: dict, settings: dict, **extra: str) -> dict:
        return {
            "name": i18n.first_name(case["patient_name"]),
            "clinic": settings["clinic_name"],
            "dentist": settings["dentist_name"] or settings["clinic_name"],
            "link": self.plan_link(case),
            "valid_until": i18n.format_date((case["plan"] or {}).get("valid_until"), case["language"]),
            **extra,
        }

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    async def deliver(
        self, case: dict, kind: str, text: str, *, link: bool = False, decline_button: bool = False
    ) -> SendResult:
        channel = case["channel"]
        now = self.clock()
        lang = case["language"]
        settings = self.settings()

        if self.messenger.mode(channel) == "simulated":
            result = SendResult("simulated")
        elif channel == "telegram":
            if not case["tg_chat_id"]:
                result = SendResult(
                    "skipped" if kind in COURTESY_KINDS else "failed",
                    error="The patient has not opened the Telegram link yet.",
                )
            else:
                result = await self.messenger.send_telegram(
                    case["tg_chat_id"],
                    text,
                    link=self.plan_link(case) if link else None,
                    link_label=i18n.render("button_open_plan", lang),
                    decline=(
                        (i18n.render("button_not_interested", lang), f"decline:{case['link_code']}")
                        if decline_button
                        else None
                    ),
                )
        else:
            window_open = self.messenger.whatsapp_window_open(parse_iso(case["last_inbound_at"]), now)
            template = None
            if kind == "plan":
                template = self.config.whatsapp_template_plan
            elif kind.startswith("followup"):
                template = self.config.whatsapp_template_followup
            if not window_open and kind in COURTESY_KINDS + ("reply",) and not template:
                error = "Outside WhatsApp's 24-hour window; only an approved template can be sent."
                result = SendResult("skipped" if kind in COURTESY_KINDS else "failed", error=error)
            else:
                result = await self.messenger.send_whatsapp(
                    normalize_phone(case["phone"], settings["country_code"]),
                    text,
                    template=template,
                    language=lang,
                    template_params=[i18n.first_name(case["patient_name"]), settings["clinic_name"]],
                    url_suffix=case["token"],
                    window_open=window_open,
                )

        self.db.insert(
            "messages",
            {
                "case_id": case["id"],
                "direction": "out",
                "channel": channel,
                "kind": kind,
                "body": text,
                "status": result.status,
                "error": result.error,
                "provider_id": result.provider_id,
                "created_at": iso(now),
            },
        )
        return result

    async def notify_staff(self, text: str) -> None:
        chat_id = self.config.telegram_staff_chat_id
        if not (chat_id and self.config.telegram_live):
            return
        result = await self.messenger.send_telegram(chat_id, text)
        if not result.ok:
            log.warning("staff notification failed: %s", result.error)

    # ------------------------------------------------------------------
    # Approve and send
    # ------------------------------------------------------------------

    def approve(self, case_id: int) -> dict:
        case = self.require_case(case_id)
        plan = case["plan"] or {}
        if not plan.get("options"):
            raise ServiceError("Add at least one treatment option before approving.")
        if not plan.get("summary") and not plan.get("findings"):
            raise ServiceError("Write a summary or at least one finding before approving.")
        if case["status"] == "draft":
            now = self.clock()
            self.db.update_case(case_id, {"status": "approved", "approved_at": iso(now)})
            self.db.add_event(case_id, "approved", at=now)
        return self.require_case(case_id)

    async def send_plan(self, case_id: int) -> dict:
        case = self.require_case(case_id)
        if case["status"] == "draft":
            raise ServiceError("Approve the plan before sending it.")
        if case["opted_out"]:
            raise ServiceError("This patient asked not to be contacted.")
        if case["status"] in ("booked", "declined"):
            raise ServiceError("This case is already closed.")
        if case["channel"] == "whatsapp" and not case["phone"].strip():
            raise ServiceError("Add the patient's phone number first.")

        now = self.clock()
        settings = self.settings()

        if case["channel"] == "telegram" and self.messenger.mode("telegram") == "live" and not case["tg_chat_id"]:
            # A bot cannot start a chat; the plan goes out as soon as the patient opens the link.
            if not case["send_pending"]:
                self.db.update_case(case_id, {"send_pending": 1})
                self.db.add_event(case_id, "awaiting_telegram", at=now)
            return {"ok": True, "pending": True}

        text = i18n.render("plan_ready", case["language"], **self.message_values(case, settings))
        result = await self.deliver(case, "plan", text, link=True)
        if not result.ok:
            return {"ok": False, "error": result.error or "Sending failed."}

        updates: dict = {"send_pending": 0}
        if case["status"] in ("approved", "expired"):
            first_due = now + timedelta(days=settings["followup_days"][0])
            updates.update(
                status="sent",
                sent_at=iso(now),
                followup_step=0,
                followup_failures=0,
                next_followup_at=iso(self.next_allowed(first_due, settings)),
            )
        self.db.update_case(case_id, updates)
        self.db.add_event(case_id, "sent", {"mode": result.status}, at=now)
        return {"ok": True, "pending": False, "mode": result.status}

    # ------------------------------------------------------------------
    # Follow-ups
    # ------------------------------------------------------------------

    def next_allowed(self, when: datetime, settings: dict) -> datetime:
        """Move a send time into the clinic's sending window (local time)."""
        tz = ZoneInfo(settings["timezone"])
        start, end = settings["send_window"]
        local = when.astimezone(tz)
        if start <= local.hour < end:
            return when
        day = local.date() if local.hour < start else local.date() + timedelta(days=1)
        moved = datetime(day.year, day.month, day.day, start, 0, tzinfo=tz)
        return moved.astimezone(when.tzinfo)

    def followup_text(self, case: dict, step: int, total: int, settings: dict) -> str:
        lang = case["language"]
        last = step == total - 1
        if last:
            key = "followup_final"
        elif step == 0:
            key = "followup_1_viewed" if case["viewed_at"] else "followup_1_unviewed"
        elif step == 1:
            key = "followup_2"
        else:
            key = "followup_3"
        text = i18n.render(key, lang, **self.message_values(case, settings))
        if step == 0 or last:
            text += "\n\n" + i18n.render("opt_out_hint", lang)
        return text

    def followup_schedule(self, case: dict, settings: dict) -> list[dict]:
        """What the dashboard shows: each step with its planned or actual time."""
        if not case["sent_at"]:
            return []
        sent_at = parse_iso(case["sent_at"])
        active = (
            case["status"] in OPEN_STATUSES
            and not case["followups_paused"]
            and not case["opted_out"]
            and not case["pending_question"]
        )
        out = []
        for index, day in enumerate(settings["followup_days"]):
            if index < case["followup_step"]:
                state, due = "sent", None
            elif index == case["followup_step"] and case["status"] in OPEN_STATUSES:
                state = "next" if active else "on_hold"
                due = case["next_followup_at"]
            elif case["status"] in OPEN_STATUSES:
                state = "upcoming"
                due = iso(self.next_allowed(sent_at + timedelta(days=day), settings))
            else:
                state, due = "cancelled", None
            out.append({"step": index + 1, "day": day, "state": state, "due_at": due})
        return out

    async def run_followups(self) -> int:
        """Send every follow-up that is due. Called by the scheduler loop."""
        now = self.clock()
        settings = self.settings()
        due = self.db.query(
            "SELECT id FROM cases WHERE status IN ('sent', 'viewed') AND next_followup_at IS NOT NULL "
            "AND next_followup_at <= ? AND followups_paused = 0 AND opted_out = 0 AND pending_question = 0",
            [iso(now)],
        )
        sent = 0
        for row in due:
            case = self.require_case(row["id"])
            allowed = self.next_allowed(now, settings)
            if allowed > now:
                self.db.update_case(case["id"], {"next_followup_at": iso(allowed)}, touch=False)
                continue
            if await self._send_followup(case, now, settings):
                sent += 1
        return sent

    async def _send_followup(self, case: dict, now: datetime, settings: dict) -> bool:
        days = settings["followup_days"]
        step = case["followup_step"]
        if step >= len(days):
            # The whole sequence went unanswered and the grace period is over.
            self.db.update_case(case["id"], {"status": "expired", "next_followup_at": None, "decided_at": iso(now)})
            self.db.add_event(case["id"], "expired", at=now)
            return False

        text = self.followup_text(case, step, len(days), settings)
        result = await self.deliver(case, f"followup_{step + 1}", text, link=True, decline_button=True)
        if not result.ok:
            failures = case["followup_failures"] + 1
            updates: dict = {"followup_failures": failures}
            if failures >= MAX_FOLLOWUP_FAILURES:
                updates["followups_paused"] = 1
                self.db.add_event(case["id"], "followups_stopped", {"error": result.error}, at=now)
            else:
                updates["next_followup_at"] = iso(now + timedelta(hours=1))
            self.db.update_case(case["id"], updates, touch=False)
            return False

        next_step = step + 1
        if next_step < len(days):
            sent_at = parse_iso(case["sent_at"]) or now
            planned = max(sent_at + timedelta(days=days[next_step]), now + timedelta(hours=20))
            next_at = self.next_allowed(planned, settings)
        else:
            next_at = now + timedelta(days=settings["expire_after_days"])
        self.db.update_case(
            case["id"],
            {"followup_step": next_step, "followup_failures": 0, "next_followup_at": iso(next_at)},
        )
        self.db.add_event(case["id"], "followup_sent", {"step": next_step}, at=now)
        return True

    async def send_followup_now(self, case_id: int) -> dict:
        case = self.require_case(case_id)
        if case["status"] not in OPEN_STATUSES:
            raise ServiceError("Follow-ups only run while a plan is waiting for the patient's decision.")
        if case["opted_out"]:
            raise ServiceError("This patient asked not to be contacted.")
        settings = self.settings()
        if case["followup_step"] >= len(settings["followup_days"]):
            raise ServiceError("All follow-ups in the sequence have been sent.")
        ok = await self._send_followup(case, self.clock(), settings)
        if not ok:
            last = self.db.one(
                "SELECT error FROM messages WHERE case_id = ? ORDER BY id DESC LIMIT 1", [case_id]
            )
            return {"ok": False, "error": (last or {}).get("error") or "Sending failed."}
        return {"ok": True}

    def set_paused(self, case_id: int, paused: bool) -> None:
        case = self.require_case(case_id)
        updates: dict = {"followups_paused": 1 if paused else 0}
        if not paused:
            updates["followup_failures"] = 0
            if case["status"] in OPEN_STATUSES and not case["next_followup_at"]:
                updates["next_followup_at"] = iso(self.next_allowed(self.clock(), self.settings()))
        self.db.update_case(case_id, updates)
        self.db.add_event(case_id, "followups_paused" if paused else "followups_resumed", at=self.clock())

    # ------------------------------------------------------------------
    # Patient actions
    # ------------------------------------------------------------------

    def record_view(self, case: dict) -> None:
        if case["status"] == "draft":
            return
        now = self.clock()
        updates: dict = {"view_count": case["view_count"] + 1}
        if not case["viewed_at"]:
            updates["viewed_at"] = iso(now)
        if case["status"] == "sent":
            updates["status"] = "viewed"
        self.db.update_case(case["id"], updates)
        self.db.add_event(case["id"], "viewed", at=now)

    def _find_option(self, case: dict, option_id: str) -> dict:
        for option in (case["plan"] or {}).get("options", []):
            if option["id"] == option_id:
                return option
        raise ServiceError("That treatment option is not part of this plan.")

    async def book(self, case: dict, data: dict) -> None:
        if case["status"] == "draft":
            raise ServiceError("This plan is not available yet.", 404)
        if case["status"] == "booked":
            raise ServiceError("This plan is already booked.", 409)
        option = self._find_option(case, str(data.get("option_id") or ""))
        settings = self.settings()
        pricing = planning.price_breakdown(option, settings["currency"])
        payment = "full" if data.get("payment") == "full" else "installments"
        if payment == "installments" and not pricing["monthly"]:
            payment = "full"
        booking = {
            "option_name": option["name"],
            "payment": payment,
            "amount": pricing["full_price"] if payment == "full" else pricing["total"],
            "preferred_days": [str(d)[:12] for d in (data.get("preferred_days") or [])][:7],
            "preferred_time": str(data.get("preferred_time") or "")[:20],
            "note": str(data.get("note") or "").strip()[:600],
        }
        now = self.clock()
        self.db.update_case(
            case["id"],
            {
                "status": "booked",
                "decided_at": iso(now),
                "booked_option": option["id"],
                "booking": booking,
                "next_followup_at": None,
                "decline_reason": None,
            },
        )
        self.db.add_event(case["id"], "booked", {"option": option["name"], "payment": payment}, at=now)
        text = i18n.render(
            "booked_confirmation", case["language"], **self.message_values(case, settings, option=option["name"])
        )
        await self.deliver(case, "booked_confirmation", text)
        await self.notify_staff(f"✅ {case['patient_name']} asked to start: {option['name']} ({payment}).")

    async def decline(self, case: dict, reason: str, note: str = "", via: str = "page") -> None:
        if case["status"] in ("draft", "booked"):
            raise ServiceError("This plan can't be declined right now.", 409)
        reason = reason if reason in DECLINE_REASONS else "other"
        now = self.clock()
        updates: dict = {
            "status": "declined",
            "decided_at": iso(now),
            "decline_reason": reason,
            "next_followup_at": None,
        }
        if reason == "opt_out":
            updates["opted_out"] = 1
        self.db.update_case(case["id"], updates)
        self.db.add_event(case["id"], "declined", {"reason": reason, "note": note[:600], "via": via}, at=now)
        settings = self.settings()
        text = i18n.render("declined_ack", case["language"], **self.message_values(case, settings))
        await self.deliver(self.require_case(case["id"]), "declined_ack", text)
        await self.notify_staff(f"✖️ {case['patient_name']} declined the plan ({reason}).")

    async def question(self, case: dict, text: str, via: str) -> None:
        """A question from the plan page (via='page') or from the chat itself."""
        text = text.strip()[:2000]
        if not text:
            raise ServiceError("Write your question first.")
        now = self.clock()
        self.db.insert(
            "messages",
            {
                "case_id": case["id"],
                "direction": "in",
                "channel": via,
                "kind": "question",
                "body": text,
                "status": "received",
                "created_at": iso(now),
            },
        )
        first_in_streak = not case["pending_question"]
        self.db.update_case(case["id"], {"pending_question": 1})
        self.db.add_event(case["id"], "question", {"via": via}, at=now)
        if first_in_streak:
            settings = self.settings()
            ack = i18n.render("question_ack", case["language"], **self.message_values(case, settings))
            await self.deliver(self.require_case(case["id"]), "question_ack", ack)
        await self.notify_staff(f"💬 {case['patient_name']}: {text[:300]}")

    async def inbound(self, case: dict, text: str, channel: str) -> None:
        """A message the patient typed in WhatsApp or Telegram."""
        now = self.clock()
        self.db.update_case(case["id"], {"last_inbound_at": iso(now)}, touch=False)
        case = self.require_case(case["id"])
        if text.strip().lower().strip(".!") in i18n.STOP_WORDS:
            self.db.insert(
                "messages",
                {
                    "case_id": case["id"], "direction": "in", "channel": channel, "kind": "opt_out",
                    "body": text.strip()[:200], "status": "received", "created_at": iso(now),
                },
            )
            if case["status"] in ("approved", "sent", "viewed", "expired"):
                await self.decline(case, "opt_out", via=channel)
            else:
                self.db.update_case(case["id"], {"opted_out": 1, "next_followup_at": None})
            return
        await self.question(case, text, channel)

    async def reply(self, case_id: int, text: str) -> dict:
        text = text.strip()[:2000]
        if not text:
            raise ServiceError("Write a reply first.")
        case = self.require_case(case_id)
        if case["opted_out"]:
            raise ServiceError("This patient asked not to be contacted.")
        result = await self.deliver(case, "reply", text)
        if not result.ok:
            return {"ok": False, "error": result.error or "Sending failed."}
        now = self.clock()
        updates: dict = {"pending_question": 0}
        if case["status"] in OPEN_STATUSES and case["next_followup_at"]:
            # Give the patient a day with the answer before the next nudge.
            quiet_until = self.next_allowed(now + timedelta(hours=24), self.settings())
            current = parse_iso(case["next_followup_at"])
            updates["next_followup_at"] = iso(max(current, quiet_until))
        self.db.update_case(case_id, updates)
        self.db.add_event(case_id, "replied", at=now)
        return {"ok": True, "mode": result.status}

    def set_status(self, case_id: int, status: str) -> None:
        """Manual override from the dashboard (e.g. the patient booked by phone)."""
        case = self.require_case(case_id)
        now = self.clock()
        if status == "booked":
            updates = {"status": "booked", "decided_at": iso(now), "next_followup_at": None}
        elif status == "declined":
            updates = {
                "status": "declined", "decided_at": iso(now), "next_followup_at": None,
                "decline_reason": case["decline_reason"] or "other",
            }
        elif status == "reopen":
            if case["status"] not in ("booked", "declined", "expired"):
                raise ServiceError("Only closed cases can be reopened.")
            reopened = "viewed" if case["viewed_at"] else "sent" if case["sent_at"] else "approved"
            updates = {
                "status": reopened, "decided_at": None, "booked_option": None, "decline_reason": None,
                "followups_paused": 1, "next_followup_at": None,
            }
        else:
            raise ServiceError("Unknown status.")
        self.db.update_case(case_id, updates)
        self.db.add_event(case_id, f"manual_{status}", at=now)

    # ------------------------------------------------------------------
    # Inbound from Telegram / WhatsApp
    # ------------------------------------------------------------------

    async def handle_telegram_update(self, update: dict) -> None:
        callback = update.get("callback_query")
        if callback:
            data = str(callback.get("data") or "")
            chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id") or "")
            if data.startswith("decline:"):
                case = self.db.get_case_by("link_code", data.split(":", 1)[1])
                if case and case["tg_chat_id"] == chat_id and case["status"] in OPEN_STATUSES + ("approved", "expired"):
                    await self.decline(case, "not_interested", via="telegram")
            try:
                await self.messenger.telegram_call("answerCallbackQuery", {"callback_query_id": callback["id"]})
            except Exception:
                pass
            return

        message = update.get("message") or {}
        text = str(message.get("text") or "").strip()
        chat_id = str((message.get("chat") or {}).get("id") or "")
        if not text or not chat_id:
            return
        default_lang = self.settings()["default_language"]

        if text.startswith("/start"):
            code = text[len("/start"):].strip()
            case = self.db.get_case_by("link_code", code) if code else None
            if case is None or (case["tg_chat_id"] and case["tg_chat_id"] != chat_id):
                await self.messenger.send_telegram(chat_id, i18n.render("tg_unknown", default_lang))
                return
            now = self.clock()
            if not case["tg_chat_id"]:
                self.db.update_case(case["id"], {"tg_chat_id": chat_id, "last_inbound_at": iso(now)})
                self.db.add_event(case["id"], "telegram_linked", at=now)
            case = self.require_case(case["id"])
            settings = self.settings()
            hello = i18n.render("tg_linked", case["language"], **self.message_values(case, settings))
            await self.deliver(case, "system", hello)
            if case["send_pending"]:
                await self.send_plan(case["id"])
            return

        row = self.db.one(
            "SELECT id FROM cases WHERE tg_chat_id = ? ORDER BY updated_at DESC, id DESC LIMIT 1", [chat_id]
        )
        if row is None:
            await self.messenger.send_telegram(chat_id, i18n.render("tg_unknown", default_lang))
            return
        await self.inbound(self.require_case(row["id"]), text, "telegram")

    async def handle_whatsapp_payload(self, payload: dict) -> None:
        settings = self.settings()
        rank = {"sent": 1, "delivered": 2, "read": 3}
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}

                for status in value.get("statuses") or []:
                    row = self.db.one("SELECT id, status FROM messages WHERE provider_id = ?", [status.get("id")])
                    if row is None:
                        continue
                    new = status.get("status")
                    if new == "failed":
                        errors = status.get("errors") or [{}]
                        self.db.update(
                            "messages", row["id"],
                            {"status": "failed", "error": str(errors[0].get("title") or "Delivery failed")[:300]},
                        )
                    elif rank.get(new, 0) > rank.get(row["status"], 0):
                        self.db.update("messages", row["id"], {"status": new})

                for message in value.get("messages") or []:
                    sender = str(message.get("from") or "")
                    text = (
                        (message.get("text") or {}).get("body")
                        or (message.get("button") or {}).get("text")
                        or ((message.get("interactive") or {}).get("button_reply") or {}).get("title")
                        or ""
                    )
                    if not sender or not text:
                        continue
                    candidates = self.db.query(
                        "SELECT id, phone FROM cases WHERE channel = 'whatsapp' ORDER BY updated_at DESC, id DESC"
                    )
                    match = next(
                        (c for c in candidates if normalize_phone(c["phone"], settings["country_code"]) == sender),
                        None,
                    )
                    if match:
                        await self.inbound(self.require_case(match["id"]), text, "whatsapp")

    # ------------------------------------------------------------------
    # Dashboard numbers
    # ------------------------------------------------------------------

    def stats(self, cases: list[dict]) -> dict:
        sent = [c for c in cases if c["sent_at"]]
        viewed = [c for c in sent if c["viewed_at"]]
        booked = [c for c in cases if c["status"] == "booked"]
        value = sum(int((c["booking"] or {}).get("amount") or 0) for c in booked)
        open_value = 0
        for case in cases:
            if case["status"] in OPEN_STATUSES:
                options = (case["plan"] or {}).get("options") or []
                chosen = next((o for o in options if o["recommended"]), options[0] if options else None)
                open_value += int(chosen["price"]) if chosen else 0
        return {
            "total": len(cases),
            "sent": len(sent),
            "viewed": len(viewed),
            "booked": len(booked),
            "declined": len([c for c in cases if c["status"] == "declined"]),
            "needs_reply": len([c for c in cases if c["pending_question"]]),
            "view_rate": round(100 * len(viewed) / len(sent)) if sent else None,
            "accept_rate": round(100 * len([c for c in booked if c["sent_at"]]) / len(sent)) if sent else None,
            "booked_value": value,
            "open_value": open_value,
        }
