"""HTTP surface: sign-in, the clinic workflow, and what a patient link exposes."""

from conftest import HEADERS, anonymous, png_bytes

CASE = {"patient_name": "Mariam Hakobyan", "phone": "+374 91 555123", "channel": "whatsapp", "language": "en",
        "notes": "Crowding upper 6 mm, lower 4 mm. Deep bite. PRIVATE: patient mentioned anxiety."}


def create(client, **overrides) -> dict:
    response = client.post("/api/cases", json={**CASE, **overrides}, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def ready_case(client) -> dict:
    """A case drafted, approved and sent (simulated channel)."""
    case = create(client)
    cid = case["id"]
    assert client.post(f"/api/cases/{cid}/draft", headers=HEADERS).status_code == 200
    assert client.post(f"/api/cases/{cid}/approve", headers=HEADERS).json()["status"] == "approved"
    sent = client.post(f"/api/cases/{cid}/send", headers=HEADERS).json()
    assert sent["result"] == {"ok": True, "pending": False, "mode": "simulated"}
    return sent["case"]


def token_of(case: dict) -> str:
    return case["plan_link"].rsplit("/", 1)[-1]


# ---------------------------------------------------------------- access


def test_dashboard_api_requires_a_session(make_client):
    client, _ = make_client(login=False)
    for method, path in (("get", "/api/cases"), ("get", "/api/settings"), ("get", "/api/cases/1"), ("post", "/api/cases")):
        assert getattr(client, method)(path).status_code == 401, path
    assert client.get("/api/me").json() == {"authenticated": False}


def test_login_rejects_wrong_password_and_throttles(make_client):
    client, _ = make_client(login=False)
    for _ in range(8):
        assert client.post("/api/login", json={"password": "guess"}).status_code == 401
    assert client.post("/api/login", json={"password": "correct horse"}).status_code == 429


def test_state_changing_requests_need_the_csrf_header(make_client):
    client, _ = make_client()
    assert client.post("/api/cases", json=CASE).status_code == 403
    assert client.post("/api/cases", json=CASE, headers=HEADERS).status_code == 200


def test_malformed_bodies_are_rejected_cleanly(make_client):
    client, _ = make_client()
    bad = client.post("/api/cases", content=b"{not json", headers={**HEADERS, "Content-Type": "application/json"})
    assert bad.status_code == 400
    assert client.post("/api/cases", json=["a", "list"], headers=HEADERS).status_code == 400
    assert client.post("/api/cases", json={"patient_name": "  "}, headers=HEADERS).status_code == 400


def test_security_headers(make_client):
    client, _ = make_client()
    response = client.get("/p/anything")
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert "script-src 'self'" in response.headers["content-security-policy"]
    cookie = client.post("/api/login", json={"password": "correct horse"}).headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie


# ---------------------------------------------------------------- clinic workflow


def test_draft_from_notes_fills_the_plan(make_client):
    client, _ = make_client()
    case = create(client)
    drafted = client.post(f"/api/cases/{case['id']}/draft", headers=HEADERS).json()
    assert drafted["draft_meta"]["source"] == "template"
    assert [f["title"] for f in drafted["plan"]["findings"]] == ["Crowded teeth", "Deep bite"]
    assert drafted["plan"]["sim"]["crowding_upper"] == 6.0
    assert len(drafted["plan"]["options"]) == 3
    assert drafted["pricing"]["opt1"]["monthly"] == 37_500


def test_plan_needs_content_before_approval(make_client):
    client, _ = make_client()
    case = create(client)
    response = client.post(f"/api/cases/{case['id']}/approve", headers=HEADERS)
    assert response.status_code == 400 and "summary" in response.json()["detail"]


def test_full_journey_from_draft_to_booking(make_client, clock):
    client, _ = make_client()
    case = ready_case(client)
    cid, token = case["id"], token_of(case)
    assert case["status"] == "sent" and case["schedule"][0]["state"] == "next"

    plan = client.get(f"/api/public/{token}").json()
    assert plan["patient_name"] == "Mariam"
    assert plan["plan"]["options"][0]["pricing"]["total"] == 600_000

    assert client.post(f"/api/public/{token}/event", json={"type": "view"}).status_code == 200
    assert client.post(f"/api/public/{token}/event", json={"type": "sim_played"}).status_code == 200
    viewed = client.get(f"/api/cases/{cid}").json()
    assert viewed["status"] == "viewed" and viewed["view_count"] == 1

    assert client.post(f"/api/public/{token}/question", json={"text": "Can I pay monthly?"}).status_code == 200
    assert client.get(f"/api/cases/{cid}").json()["pending_question"] is True
    reply = client.post(f"/api/cases/{cid}/reply", json={"text": "Yes, 12 installments."}, headers=HEADERS).json()
    assert reply["result"]["ok"] and reply["case"]["pending_question"] is False

    booking = {"option_id": "opt2", "payment": "installments", "preferred_days": ["Tue", "Thu"], "preferred_time": "evening", "note": "After 18:00"}
    assert client.post(f"/api/public/{token}/book", json=booking).status_code == 200
    booked = client.get(f"/api/cases/{cid}").json()
    assert booked["status"] == "booked"
    assert booked["booking"]["option_name"] == "Ceramic braces" and booked["booking"]["amount"] == 850_000
    assert all(step["state"] in ("sent", "cancelled") for step in booked["schedule"])
    assert client.post(f"/api/public/{token}/book", json=booking).status_code == 409

    listing = client.get("/api/cases").json()
    assert listing["stats"]["booked"] == 1 and listing["stats"]["booked_value"] == 850_000
    assert listing["stats"]["view_rate"] == 100 and listing["stats"]["accept_rate"] == 100
    events = [e["type"] for e in booked["events"]]
    for expected in ("created", "drafted", "approved", "sent", "viewed", "sim_played", "question", "replied", "booked"):
        assert expected in events


def test_patient_can_decline_with_a_reason(make_client):
    client, _ = make_client()
    case = ready_case(client)
    token = token_of(case)
    assert client.post(f"/api/public/{token}/decline", json={"reason": "price", "note": "maybe next year"}).status_code == 200
    after = client.get(f"/api/cases/{case['id']}").json()
    assert (after["status"], after["decline_reason"]) == ("declined", "price")
    reopened = client.post(f"/api/cases/{case['id']}/status", json={"status": "reopen"}, headers=HEADERS).json()
    assert reopened["status"] == "sent" and reopened["followups_paused"] is True


def test_booking_an_unknown_option_is_refused(make_client):
    client, _ = make_client()
    token = token_of(ready_case(client))
    assert client.post(f"/api/public/{token}/book", json={"option_id": "opt99"}).status_code == 400


def test_delete_case_removes_its_images(make_client, tmp_path):
    client, _ = make_client()
    case = create(client)
    upload = client.post(f"/api/cases/{case['id']}/attachments?kind=xray", files={"file": ("pano.png", png_bytes(), "image/png")}, headers=HEADERS)
    assert upload.status_code == 200
    stored = list((tmp_path / "uploads" / str(case["id"])).iterdir())
    assert len(stored) == 1
    assert client.delete(f"/api/cases/{case['id']}", headers=HEADERS).status_code == 200
    assert not stored[0].exists()
    assert client.get(f"/api/cases/{case['id']}").status_code == 404


def test_settings_roundtrip(make_client):
    client, _ = make_client()
    settings = client.get("/api/settings").json()["settings"]
    settings.update(clinic_name="Smile Studio", followup_days=[2, 5], currency="USD")
    settings["catalog"][0]["price"] = 3000
    saved = client.put("/api/settings", json=settings, headers=HEADERS).json()["settings"]
    assert (saved["clinic_name"], saved["followup_days"], saved["currency"]) == ("Smile Studio", [2, 5], "USD")
    case = create(client)
    assert case["plan"]["options"][0]["price"] == 3000


# ---------------------------------------------------------------- what the patient link exposes


def test_draft_plans_are_not_public_but_can_be_previewed_by_the_clinic(make_client):
    client, app = make_client()
    case = create(client)
    token = token_of(case)

    stranger = anonymous(app)
    assert stranger.get(f"/api/public/{token}").status_code == 404
    assert stranger.get(f"/api/public/{token}?preview=1").status_code == 404
    assert stranger.post(f"/api/public/{token}/book", json={"option_id": "opt1"}).status_code == 404
    preview = client.get(f"/api/public/{token}?preview=1")
    assert preview.status_code == 200 and preview.json()["preview"] is True


def test_public_plan_does_not_leak_clinical_notes_or_contact_details(make_client):
    client, _ = make_client()
    case = ready_case(client)
    body = client.get(f"/api/public/{token_of(case)}").text
    assert "PRIVATE" not in body and "anxiety" not in body
    assert "555123" not in body
    assert "Hakobyan" not in body


def test_unknown_token_is_404(make_client):
    client, _ = make_client(login=False)
    assert client.get("/api/public/not-a-real-token").status_code == 404
    assert client.post("/api/public/not-a-real-token/question", json={"text": "hi"}).status_code == 404


def test_views_in_preview_are_not_counted(make_client):
    client, _ = make_client()
    case = ready_case(client)
    client.get(f"/api/public/{token_of(case)}?preview=1")
    assert client.get(f"/api/cases/{case['id']}").json()["view_count"] == 0


def test_images_are_private_unless_shared(make_client):
    client, app = make_client()
    case = ready_case(client)
    cid, token = case["id"], token_of(case)
    upload = client.post(f"/api/cases/{cid}/attachments?kind=photo", files={"file": ("smile.jpg", png_bytes(exif=True), "image/jpeg")}, headers=HEADERS).json()
    aid = upload["id"]

    patient = anonymous(app)
    assert patient.get(f"/api/attachments/{aid}/file").status_code == 401
    assert patient.get(f"/api/public/{token}/file/{aid}").status_code == 404
    assert patient.get(f"/api/public/{token}").json()["images"] == []

    client.patch(f"/api/attachments/{aid}", json={"shared": True, "caption": "Before"}, headers=HEADERS)
    images = patient.get(f"/api/public/{token}").json()["images"]
    assert [(i["kind"], i["caption"]) for i in images] == [("photo", "Before")]
    served = patient.get(images[0]["url"])
    assert served.status_code == 200 and served.headers["content-type"] == "image/jpeg"

    # Another patient's token must not unlock this image.
    other = ready_case(client)
    assert patient.get(f"/api/public/{token_of(other)}/file/{aid}").status_code == 404


def test_uploads_are_reencoded_without_metadata_and_junk_is_rejected(make_client, tmp_path):
    client, _ = make_client()
    case = create(client)
    ok = client.post(f"/api/cases/{case['id']}/attachments", files={"file": ("phone.jpg", png_bytes(exif=True), "image/jpeg")}, headers=HEADERS)
    assert ok.status_code == 200
    stored = next((tmp_path / "uploads" / str(case["id"])).iterdir())
    from PIL import Image

    with Image.open(stored) as image:
        assert image.format == "JPEG" and len(image.getexif()) == 0

    bad = client.post(f"/api/cases/{case['id']}/attachments", files={"file": ("virus.jpg", b"<html>not an image</html>", "image/jpeg")}, headers=HEADERS)
    assert bad.status_code == 400
    assert len(list((tmp_path / "uploads" / str(case["id"])).iterdir())) == 1


def test_qr_code_for_the_plan_link(make_client):
    client, _ = make_client()
    case = create(client)
    response = client.get(f"/api/cases/{case['id']}/qr.svg")
    assert response.status_code == 200 and response.headers["content-type"].startswith("image/svg+xml")
    assert client.get(f"/api/cases/{case['id']}/qr.svg?target=telegram").status_code == 404  # no bot configured
