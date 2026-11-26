"""Live-channel behaviour with the provider APIs replaced by a recording transport."""

import hashlib
import hmac
import json
from datetime import timedelta

from orthocase.messaging import normalize_phone

from conftest import HEADERS, new_case, run, sent_case

TELEGRAM = dict(telegram_token="123:ABC")
WHATSAPP = dict(
    whatsapp_token="token", whatsapp_phone_number_id="5550001", whatsapp_app_secret="app-secret",
    whatsapp_verify_token="verify-me", whatsapp_template_plan="plan_ready", whatsapp_template_followup="plan_followup",
)


def test_normalize_phone():
    assert normalize_phone("+374 91 555-123", "374") == "37491555123"
    assert normalize_phone("091 555123", "374") == "37491555123"
    assert normalize_phone("91555123", "374") == "37491555123"
    assert normalize_phone("00374 91 555123", "374") == "37491555123"
    assert normalize_phone("7 999 123 45 67", "374") == "79991234567"
    assert normalize_phone("", "374") == ""


# ---------------------------------------------------------------- Telegram


def test_telegram_plan_waits_until_the_patient_opens_the_bot(make_service, recorder):
    service = make_service(**TELEGRAM)
    case = new_case(service, channel="telegram")
    service.approve(case["id"])

    result = run(service.send_plan(case["id"]))
    assert result == {"ok": True, "pending": True}
    waiting = service.require_case(case["id"])
    assert waiting["status"] == "approved" and waiting["send_pending"] == 1
    assert recorder.sent("sendMessage") == []

    # The patient taps the deep link: /start <code>
    update = {"message": {"chat": {"id": 777}, "text": f"/start {case['link_code']}"}}
    run(service.handle_telegram_update(update))

    linked = service.require_case(case["id"])
    assert linked["tg_chat_id"] == "777"
    assert linked["status"] == "sent" and linked["send_pending"] == 0
    sends = recorder.sent("sendMessage")
    assert [s["chat_id"] for s in sends] == ["777", "777"]
    assert "connected" in sends[0]["text"]
    assert case["token"] in sends[1]["text"]
    assert sends[1]["reply_markup"]["inline_keyboard"][0][0]["url"].endswith(case["token"])


def test_telegram_link_code_cannot_be_claimed_by_a_second_chat(make_service, recorder):
    service = make_service(**TELEGRAM)
    case = new_case(service, channel="telegram")
    run(service.handle_telegram_update({"message": {"chat": {"id": 1}, "text": f"/start {case['link_code']}"}}))
    run(service.handle_telegram_update({"message": {"chat": {"id": 2}, "text": f"/start {case['link_code']}"}}))
    assert service.require_case(case["id"])["tg_chat_id"] == "1"
    assert "personal link" in recorder.sent("sendMessage")[-1]["text"] or "Միանալու" in recorder.sent("sendMessage")[-1]["text"]


def test_telegram_unknown_start_code_is_rejected(make_service, recorder):
    service = make_service(**TELEGRAM)
    run(service.handle_telegram_update({"message": {"chat": {"id": 5}, "text": "/start nope"}}))
    assert len(recorder.sent("sendMessage")) == 1
    assert service.db.list_cases() == []


def test_telegram_text_becomes_a_question_and_button_declines(make_service, recorder):
    service = make_service(**TELEGRAM)
    case = new_case(service, channel="telegram")
    service.approve(case["id"])
    run(service.handle_telegram_update({"message": {"chat": {"id": 42}, "text": f"/start {case['link_code']}"}}))
    run(service.send_plan(case["id"]))
    assert service.require_case(case["id"])["status"] == "sent"

    run(service.handle_telegram_update({"message": {"chat": {"id": 42}, "text": "How long does it take?"}}))
    after = service.require_case(case["id"])
    assert after["pending_question"] == 1
    assert [m["body"] for m in service.db.messages(case["id"]) if m["direction"] == "in"] == ["How long does it take?"]

    # "Not interested" button under a reminder
    foreign = {"callback_query": {"id": "cb0", "data": f"decline:{case['link_code']}", "message": {"chat": {"id": 999}}}}
    run(service.handle_telegram_update(foreign))
    assert service.require_case(case["id"])["status"] == "sent", "a different chat cannot decline someone else's plan"

    own = {"callback_query": {"id": "cb1", "data": f"decline:{case['link_code']}", "message": {"chat": {"id": 42}}}}
    run(service.handle_telegram_update(own))
    declined = service.require_case(case["id"])
    assert (declined["status"], declined["decline_reason"]) == ("declined", "not_interested")
    assert recorder.sent("answerCallbackQuery")


def test_followup_on_telegram_carries_open_and_decline_buttons(make_service, recorder, clock):
    service = make_service(**TELEGRAM)
    case = new_case(service, channel="telegram")
    service.approve(case["id"])
    run(service.handle_telegram_update({"message": {"chat": {"id": 42}, "text": f"/start {case['link_code']}"}}))
    run(service.send_plan(case["id"]))
    clock.advance(days=1, minutes=5)
    assert run(service.run_followups()) == 1
    keyboard = recorder.sent("sendMessage")[-1]["reply_markup"]["inline_keyboard"]
    assert keyboard[0][0]["text"] == "Open my plan"
    assert keyboard[1][0]["callback_data"] == f"decline:{case['link_code']}"


# ---------------------------------------------------------------- WhatsApp


def test_whatsapp_first_message_uses_the_approved_template(make_service, recorder):
    service = make_service(**WHATSAPP)
    case = sent_case(service)
    url, body = recorder.calls[-1]
    assert url == "https://graph.facebook.com/v21.0/5550001/messages"
    assert body["to"] == "37491555123"
    assert body["type"] == "template"
    template = body["template"]
    assert template["name"] == "plan_ready" and template["language"] == {"code": "en"}
    assert [p["text"] for p in template["components"][0]["parameters"]] == ["Mariam", "Your Dental Clinic"]
    assert template["components"][1]["parameters"][0]["text"] == case["token"]
    message = service.db.messages(case["id"])[0]
    assert (message["status"], message["provider_id"]) == ("sent", "wamid.1")


def test_whatsapp_free_text_only_inside_the_24_hour_window(make_service, recorder, clock):
    service = make_service(**WHATSAPP)
    case = sent_case(service)

    # No inbound message yet: a free-text reply is not allowed by WhatsApp.
    outcome = run(service.reply(case["id"], "Hello!"))
    assert not outcome["ok"] and "24-hour" in outcome["error"]
    assert len(recorder.calls) == 1, "nothing was sent to the API"

    run(service.inbound(service.require_case(case["id"]), "Can I pay monthly?", "whatsapp"))
    ack = recorder.calls[-1][1]
    assert ack["type"] == "text", "inside the window the acknowledgement goes as plain text"

    assert run(service.reply(case["id"], "Yes, in 12 installments."))["ok"]
    assert recorder.calls[-1][1]["text"]["body"] == "Yes, in 12 installments."

    clock.advance(hours=25)
    assert not run(service.reply(case["id"], "Still there?"))["ok"]


def test_whatsapp_without_template_name_fails_with_a_clear_message(make_service, recorder):
    service = make_service(whatsapp_token="token", whatsapp_phone_number_id="5550001")
    case = new_case(service)
    service.approve(case["id"])
    result = run(service.send_plan(case["id"]))
    assert not result["ok"] and "template" in result["error"]
    assert service.require_case(case["id"])["status"] == "approved"
    assert recorder.calls == []


def _signed(client, payload: dict, secret: str = "app-secret"):
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/webhooks/whatsapp", content=raw, headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"})


def test_whatsapp_webhook_verification_and_signature(make_client):
    client, _ = make_client(login=False, **WHATSAPP)
    ok = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
    assert (ok.status_code, ok.text) == (200, "42")
    assert client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "42"}).status_code == 403

    payload = {"entry": []}
    assert _signed(client, payload).status_code == 200
    assert _signed(client, payload, secret="guess").status_code == 403
    assert client.post("/webhooks/whatsapp", json=payload).status_code == 403


def test_whatsapp_webhook_delivers_replies_and_read_receipts(make_client):
    client, app = make_client(**WHATSAPP)
    service = app.state.service
    created = client.post("/api/cases", json={"patient_name": "Mariam Hakobyan", "phone": "091 555123", "language": "en"}, headers=HEADERS).json()
    cid = created["id"]
    plan = created["plan"]
    plan["summary"] = "Summary"
    client.patch(f"/api/cases/{cid}", json={"plan": plan}, headers=HEADERS)
    client.post(f"/api/cases/{cid}/approve", headers=HEADERS)
    assert client.post(f"/api/cases/{cid}/send", headers=HEADERS).json()["result"]["ok"]
    provider_id = service.db.messages(cid)[0]["provider_id"]

    statuses = {"entry": [{"changes": [{"value": {"statuses": [{"id": provider_id, "status": "read"}]}}]}]}
    assert _signed(client, statuses).status_code == 200
    assert service.db.messages(cid)[0]["status"] == "read"
    # An older "delivered" receipt arriving late must not downgrade it.
    late = {"entry": [{"changes": [{"value": {"statuses": [{"id": provider_id, "status": "delivered"}]}}]}]}
    _signed(client, late)
    assert service.db.messages(cid)[0]["status"] == "read"

    inbound = {"entry": [{"changes": [{"value": {"messages": [{"from": "37491555123", "text": {"body": "Is Saturday possible?"}}]}}]}]}
    assert _signed(client, inbound).status_code == 200
    detail = client.get(f"/api/cases/{cid}").json()
    assert detail["pending_question"] is True
    assert [m["body"] for m in detail["messages"] if m["direction"] == "in"] == ["Is Saturday possible?"]

    stop = {"entry": [{"changes": [{"value": {"messages": [{"from": "37491555123", "text": {"body": "STOP"}}]}}]}]}
    _signed(client, stop)
    detail = client.get(f"/api/cases/{cid}").json()
    assert detail["opted_out"] is True and detail["status"] == "declined"


def test_telegram_webhook_requires_the_shared_secret(make_client):
    client, _ = make_client(login=False, telegram_token="123:ABC", telegram_mode="webhook", telegram_webhook_secret="s3cret")
    update = {"message": {"chat": {"id": 1}, "text": "/start x"}}
    assert client.post("/webhooks/telegram", json=update).status_code == 403
    assert client.post("/webhooks/telegram", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "nope"}).status_code == 403
    assert client.post("/webhooks/telegram", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"}).status_code == 200


def test_simulated_mode_never_calls_a_provider(make_service, recorder, clock):
    service = make_service()
    case = sent_case(service)
    clock.advance(days=1, minutes=5)
    run(service.run_followups())
    assert recorder.calls == []
    assert {m["status"] for m in service.db.messages(case["id"])} == {"simulated"}
