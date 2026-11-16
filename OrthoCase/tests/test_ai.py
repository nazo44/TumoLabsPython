"""Claude drafting, with the API client replaced by a stub (no network, no key)."""

from datetime import date
from types import SimpleNamespace

import pytest

from orthocase import ai, planning

from conftest import HEADERS, png_bytes, run

CASE = {"patient_name": "Anna Sargsyan", "language": "ru", "notes": "Скученность 6 мм. Глубокий прикус."}
TODAY = date(2026, 11, 16)


def sample_draft(**overrides) -> ai.PlanDraft:
    values = dict(
        summary="Анна, мы подготовили для вас план.",
        findings=[ai.Finding(title="Скученность зубов", detail="Зубам не хватает места.")],
        simulator=ai.SimulatorParams(
            crowding_upper=6, crowding_lower=4, spacing_upper=0, spacing_lower=0, diastema=0,
            overjet=2.5, overbite=5, midline=0, narrow_upper=0,
        ),
        options=[
            ai.OptionDraft(catalog_id="aligners", duration_months=12, recommended=True, note="Незаметно.", align_pct=100, bite_pct=70),
            ai.OptionDraft(catalog_id="gold_braces", duration_months=10, recommended=False, note="", align_pct=100, bite_pct=100),
        ],
        dentist_checks=["Panoramic image suggests an impacted 38 — not in the notes."],
    )
    values.update(overrides)
    return ai.PlanDraft(**values)


class StubClient:
    def __init__(self, draft=None, stop_reason="end_turn", error=None):
        self.requests: list[dict] = []
        self._draft, self._stop, self._error = draft, stop_reason, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    async def _parse(self, **request):
        self.requests.append(request)
        if self._error:
            raise self._error
        return SimpleNamespace(stop_reason=self._stop, parsed_output=self._draft)


def test_request_shape(tmp_path):
    image = tmp_path / "pano.jpg"
    image.write_bytes(png_bytes(size=(3000, 1500)))
    attachments = [{"path": str(image), "kind": "xray", "caption": "Panoramic"}, {"path": str(tmp_path / "missing.jpg"), "kind": "photo", "caption": ""}]
    settings = planning.normalize_settings(None)
    request = ai.build_request(CASE, attachments, settings, "claude-opus-5-5")

    assert request["model"] == "claude-opus-5-5"
    assert request["output_format"] is ai.PlanDraft
    assert request["fallbacks"] == "default" and request["betas"] == ["server-side-fallback-2026-07-01"]
    assert request["output_config"] == {"effort": "high"}
    assert "thinking" not in request and "temperature" not in request
    assert "Russian" in request["system"] and "id: aligners" in request["system"]
    assert "600" not in request["system"], "prices are not sent to the model"

    content = request["messages"][0]["content"]
    assert [block["type"] for block in content] == ["text", "image", "text"]
    assert content[0]["text"] == "X-ray — Panoramic:"
    assert content[1]["source"]["media_type"] == "image/jpeg"
    assert "Скученность 6 мм" in content[-1]["text"] and "Anna" in content[-1]["text"]
    assert "Sargsyan" not in str(content), "only the first name leaves the clinic"

    import base64, io
    from PIL import Image

    with Image.open(io.BytesIO(base64.b64decode(content[1]["source"]["data"]))) as sent:
        assert max(sent.size) <= ai.MAX_IMAGE_EDGE


def test_draft_is_mapped_onto_the_clinic_catalog():
    settings = planning.normalize_settings(None)
    client = StubClient(sample_draft())
    plan, checks = run(ai.draft_plan(CASE, [], settings, model="claude-opus-5-5", today=TODAY, client=client))

    assert plan["summary"].startswith("Анна")
    assert plan["sim"]["crowding_upper"] == 6 and plan["sim"]["overbite"] == 5
    assert len(plan["options"]) == 1, "an option that is not in the catalog is dropped"
    option = plan["options"][0]
    assert option["name"] == "Прозрачные элайнеры"
    assert option["price"] == 1_400_000, "the price comes from the catalog, never from the model"
    assert (option["duration_months"], option["bite_pct"], option["recommended"]) == (12, 70, True)
    assert any("gold_braces" in check for check in checks)
    assert any("impacted 38" in check for check in checks)


def test_out_of_range_model_values_are_clamped():
    settings = planning.normalize_settings(None)
    wild = sample_draft(simulator=ai.SimulatorParams(
        crowding_upper=80, crowding_lower=-3, spacing_upper=0, spacing_lower=0, diastema=0, overjet=40, overbite=-30, midline=0, narrow_upper=0))
    plan, _ = run(ai.draft_plan(CASE, [], settings, model="m", today=TODAY, client=StubClient(wild)))
    assert plan["sim"]["crowding_upper"] == 12 and plan["sim"]["crowding_lower"] == 0
    assert plan["sim"]["overjet"] == 12 and plan["sim"]["overbite"] == -6


@pytest.mark.parametrize("stub", [StubClient(None, stop_reason="refusal"), StubClient(None, stop_reason="max_tokens"), StubClient(sample_draft(), stop_reason="refusal")])
def test_refusals_and_truncated_output_raise(stub):
    with pytest.raises(ai.DraftError):
        run(ai.draft_plan(CASE, [], planning.normalize_settings(None), model="m", today=TODAY, client=stub))


def test_api_errors_are_reported_as_draft_errors():
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    errors = [
        anthropic.APIConnectionError(request=request),
        anthropic.RateLimitError("slow down", response=httpx.Response(429, request=request), body=None),
        anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=request), body=None),
    ]
    for error in errors:
        with pytest.raises(ai.DraftError):
            run(ai.draft_plan(CASE, [], planning.normalize_settings(None), model="m", today=TODAY, client=StubClient(error=error)))


def test_endpoint_uses_ai_when_enabled(make_client):
    stub = StubClient(sample_draft())
    client, _ = make_client(ai_enabled=True, ai_client=stub)
    case = client.post("/api/cases", json=CASE, headers=HEADERS).json()
    drafted = client.post(f"/api/cases/{case['id']}/draft", headers=HEADERS).json()
    assert drafted["draft_meta"]["source"] == "ai"
    assert drafted["draft_meta"]["checks"]
    assert drafted["plan"]["options"][0]["catalog_id"] == "aligners"
    assert drafted["status"] == "draft", "an AI draft is never approved or sent automatically"
    assert len(stub.requests) == 1


def test_endpoint_falls_back_to_the_template_when_ai_fails(make_client):
    stub = StubClient(None, stop_reason="refusal")
    client, _ = make_client(ai_enabled=True, ai_client=stub)
    case = client.post("/api/cases", json=CASE, headers=HEADERS).json()
    drafted = client.post(f"/api/cases/{case['id']}/draft", headers=HEADERS).json()
    assert drafted["draft_meta"]["source"] == "template"
    assert "template draft was used" in drafted["draft_meta"]["notice"]
    assert [f["title"] for f in drafted["plan"]["findings"]] == ["Скученность зубов", "Глубокий прикус"]
