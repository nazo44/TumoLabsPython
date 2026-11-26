"""The automatic follow-up sequence: when it sends, what it says, when it stops."""

from datetime import datetime, timedelta, timezone

from orthocase.db import parse_iso

from conftest import START, new_case, run, sent_case


def kinds(service, case_id, direction="out"):
    return [m["kind"] for m in service.db.messages(case_id) if m["direction"] == direction]


def test_sending_schedules_the_first_reminder_a_day_later(make_service):
    service = make_service()
    case = sent_case(service)
    assert case["status"] == "sent"
    assert kinds(service, case["id"]) == ["plan"]
    assert parse_iso(case["next_followup_at"]) == START + timedelta(days=1)


def test_plan_cannot_be_sent_before_approval(make_service):
    service = make_service()
    case = new_case(service)
    try:
        run(service.send_plan(case["id"]))
    except Exception as exc:
        assert "Approve" in str(exc)
    else:
        raise AssertionError("sending a draft should be refused")


def test_full_sequence_runs_on_schedule_then_closes_as_no_response(make_service, clock):
    service = make_service()
    case = sent_case(service)
    cid = case["id"]

    # Nothing is due yet.
    clock.advance(hours=23)
    assert run(service.run_followups()) == 0

    for day, step in ((1, 1), (3, 2), (7, 3), (14, 4)):
        clock.set(START + timedelta(days=day, minutes=5))
        assert run(service.run_followups()) == 1, f"day {day}"
        assert run(service.run_followups()) == 0, "never sends the same step twice"
        assert service.require_case(cid)["followup_step"] == step

    assert kinds(service, cid) == ["plan", "followup_1", "followup_2", "followup_3", "followup_4"]
    messages = service.db.messages(cid)
    assert "Reply STOP" in messages[1]["body"], "first reminder explains how to opt out"
    assert "last reminder" in messages[-1]["body"]
    assert "Reply STOP" in messages[-1]["body"]
    assert all(case["token"] in m["body"] for m in messages)

    # A week of silence after the last reminder closes the case.
    clock.advance(days=7, minutes=1)
    assert run(service.run_followups()) == 0
    closed = service.require_case(cid)
    assert closed["status"] == "expired"
    assert closed["next_followup_at"] is None
    assert len(service.db.messages(cid)) == 5


def test_first_reminder_depends_on_whether_the_plan_was_opened(make_service, clock):
    service = make_service()
    unopened = sent_case(service)
    opened = sent_case(service, patient_name="Davit Sargsyan", phone="+374 93 111222")
    service.record_view(service.require_case(opened["id"]))
    assert service.require_case(opened["id"])["status"] == "viewed"

    clock.advance(days=1, minutes=5)
    assert run(service.run_followups()) == 2
    assert "ready whenever you have a couple of minutes" in service.db.messages(unopened["id"])[-1]["body"]
    assert "thank you for looking through" in service.db.messages(opened["id"])[-1]["body"]


def test_reminders_respect_the_clinic_sending_window(make_service, clock):
    service = make_service()
    # Sent at 23:30 Yerevan time: the day-1 reminder would land at night.
    clock.set(datetime(2026, 11, 16, 19, 30, tzinfo=timezone.utc))
    case = sent_case(service)
    due = parse_iso(case["next_followup_at"])
    assert due == datetime(2026, 11, 18, 6, 0, tzinfo=timezone.utc)  # 10:00 local, next allowed morning

    # If the scheduler wakes up at night for a due reminder, it waits for the morning.
    service.db.update_case(case["id"], {"next_followup_at": "2026-11-17T22:00:00+00:00"})
    clock.set(datetime(2026, 11, 17, 23, 0, tzinfo=timezone.utc))  # 03:00 local
    assert run(service.run_followups()) == 0
    assert parse_iso(service.require_case(case["id"])["next_followup_at"]) == datetime(2026, 11, 18, 6, 0, tzinfo=timezone.utc)
    assert kinds(service, case["id"]) == ["plan"]


def test_booking_stops_the_sequence(make_service, clock):
    service = make_service()
    case = sent_case(service)
    option = case["plan"]["options"][0]
    run(service.book(case, {"option_id": option["id"], "payment": "installments", "preferred_days": ["Tue"], "preferred_time": "evening"}))

    booked = service.require_case(case["id"])
    assert booked["status"] == "booked"
    assert booked["next_followup_at"] is None
    assert booked["booking"]["amount"] == option["price"]
    assert kinds(service, case["id"])[-1] == "booked_confirmation"

    clock.advance(days=30)
    assert run(service.run_followups()) == 0
    assert service.require_case(case["id"])["status"] == "booked"


def test_paying_in_full_books_the_discounted_amount(make_service):
    service = make_service()
    case = sent_case(service)
    option = case["plan"]["options"][0]
    run(service.book(case, {"option_id": option["id"], "payment": "full"}))
    assert service.require_case(case["id"])["booking"]["amount"] == 570_000


def test_declining_stops_the_sequence(make_service, clock):
    service = make_service()
    case = sent_case(service)
    run(service.decline(case, "price", "Too expensive right now"))
    declined = service.require_case(case["id"])
    assert (declined["status"], declined["decline_reason"]) == ("declined", "price")
    clock.advance(days=30)
    assert run(service.run_followups()) == 0


def test_stop_reply_opts_the_patient_out_in_any_language(make_service, clock):
    for word in ("STOP", "стоп", "Ստոպ."):
        service = make_service()
        case = sent_case(service)
        run(service.inbound(case, word, "whatsapp"))
        after = service.require_case(case["id"])
        assert after["opted_out"] == 1, word
        assert (after["status"], after["decline_reason"]) == ("declined", "opt_out")
        clock.advance(days=30)
        assert run(service.run_followups()) == 0
        try:
            run(service.send_plan(case["id"]))
        except Exception as exc:
            assert "closed" in str(exc) or "not to be contacted" in str(exc)
        else:
            raise AssertionError("an opted-out patient must not be messaged again")


def test_a_pending_question_holds_reminders_until_the_clinic_replies(make_service, clock):
    service = make_service()
    case = sent_case(service)
    cid = case["id"]
    run(service.question(case, "Does it hurt?", "page"))
    assert service.require_case(cid)["pending_question"] == 1
    assert kinds(service, cid, "in") == ["question"]
    assert kinds(service, cid)[-1] == "question_ack"

    clock.advance(days=2)
    assert run(service.run_followups()) == 0, "no nudging while the patient waits for an answer"

    result = run(service.reply(cid, "Only mild pressure for a few days."))
    assert result["ok"]
    after = service.require_case(cid)
    assert after["pending_question"] == 0
    assert parse_iso(after["next_followup_at"]) >= clock.now() + timedelta(hours=24)

    clock.advance(hours=23)
    assert run(service.run_followups()) == 0
    clock.advance(hours=2)
    assert run(service.run_followups()) == 1


def test_pausing_and_resuming(make_service, clock):
    service = make_service()
    case = sent_case(service)
    service.set_paused(case["id"], True)
    clock.advance(days=2)
    assert run(service.run_followups()) == 0
    service.set_paused(case["id"], False)
    assert run(service.run_followups()) == 1


def test_repeated_send_failures_stop_the_sequence(make_service, clock, recorder):
    service = make_service(
        whatsapp_token="token", whatsapp_phone_number_id="123", whatsapp_template_plan="plan_ready",
        whatsapp_template_followup="plan_followup",
    )
    case = sent_case(service)
    recorder.fail_whatsapp = True

    clock.advance(days=1, minutes=5)
    for attempt in range(3):
        assert run(service.run_followups()) == 0
        clock.advance(hours=1, minutes=1)

    after = service.require_case(case["id"])
    assert after["followups_paused"] == 1
    assert after["followup_step"] == 0
    failed = [m for m in service.db.messages(case["id"]) if m["status"] == "failed"]
    assert len(failed) == 3 and "Template name does not exist" in failed[0]["error"]
    clock.advance(days=5)
    assert run(service.run_followups()) == 0, "no endless retry loop"


def test_send_next_now_advances_the_sequence(make_service):
    service = make_service()
    case = sent_case(service)
    assert run(service.send_followup_now(case["id"]))["ok"]
    after = service.require_case(case["id"])
    assert after["followup_step"] == 1
    # The next reminder never follows within the same day.
    assert parse_iso(after["next_followup_at"]) >= START + timedelta(hours=20)


def test_schedule_view_reflects_progress(make_service):
    service = make_service()
    case = sent_case(service)
    run(service.send_followup_now(case["id"]))
    schedule = service.followup_schedule(service.require_case(case["id"]), service.settings())
    assert [s["state"] for s in schedule] == ["sent", "next", "upcoming", "upcoming"]
    run(service.decline(service.require_case(case["id"]), "timing"))
    schedule = service.followup_schedule(service.require_case(case["id"]), service.settings())
    assert [s["state"] for s in schedule] == ["sent", "cancelled", "cancelled", "cancelled"]


def test_messages_are_written_in_the_patients_language(make_service, clock):
    service = make_service()
    hy = sent_case(service, language="hy", patient_name="Անի Գրիգորյան")
    ru = sent_case(service, language="ru", patient_name="Анна Саркисян", phone="+374 93 000111")
    assert "Բարև Ձեզ, Անի" in service.db.messages(hy["id"])[0]["body"]
    assert "Здравствуйте, Анна" in service.db.messages(ru["id"])[0]["body"]
    clock.advance(days=1, minutes=5)
    run(service.run_followups())
    assert "ՍՏՈՊ" in service.db.messages(hy["id"])[-1]["body"]
    assert "СТОП" in service.db.messages(ru["id"])[-1]["body"]
