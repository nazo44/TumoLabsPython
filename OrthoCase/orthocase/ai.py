"""Plan drafting with Claude.

The dentist's notes, X-rays and photos go in; a structured draft comes out.
The draft is never sent to a patient directly — the dentist reviews, edits
and approves it first.
"""

from __future__ import annotations

import base64
import io
import logging
from datetime import date
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from . import i18n, planning

log = logging.getLogger("orthocase.ai")

MAX_IMAGES = 8
MAX_IMAGE_EDGE = 1568

LANGUAGE_NAMES = {"hy": "Armenian", "ru": "Russian", "en": "English"}


class Finding(BaseModel):
    title: str = Field(description="Short plain-language name of the problem, in the patient's language.")
    detail: str = Field(description="One or two sentences for the patient: what it is and why treating it helps.")


class SimulatorParams(BaseModel):
    """Starting position of the teeth, in millimetres."""

    crowding_upper: float = Field(description="Upper arch crowding, 0-12 mm. 0 if none.")
    crowding_lower: float = Field(description="Lower arch crowding, 0-12 mm. 0 if none.")
    spacing_upper: float = Field(description="Total upper spacing excluding a midline diastema, 0-10 mm.")
    spacing_lower: float = Field(description="Total lower spacing, 0-10 mm.")
    diastema: float = Field(description="Upper midline diastema, 0-5 mm.")
    overjet: float = Field(description="Overjet in mm. Normal is 2.5; negative for an underbite. Range -4..12.")
    overbite: float = Field(description="Overbite in mm. Normal is 2; negative for an open bite. Range -6..9.")
    midline: float = Field(description="Upper midline shift in mm; positive toward the patient's left. -5..5.")
    narrow_upper: float = Field(description="Upper arch constriction in mm, 0-8. 0 if no crossbite.")


class OptionDraft(BaseModel):
    catalog_id: str = Field(description="id of a treatment from the clinic catalog. Never invent one.")
    duration_months: int = Field(description="Expected treatment duration for this patient, in months.")
    recommended: bool = Field(description="True for the single option that suits this case best.")
    note: str = Field(description="One sentence for the patient on why this option fits, in their language.")
    align_pct: int = Field(description="Share of the alignment problems this option is expected to correct, 0-100.")
    bite_pct: int = Field(description="Share of the bite problems this option is expected to correct, 0-100.")


class PlanDraft(BaseModel):
    summary: str = Field(description="2-3 warm, plain sentences addressed to the patient, in their language.")
    findings: list[Finding]
    simulator: SimulatorParams
    options: list[OptionDraft]
    dentist_checks: list[str] = Field(
        description="Things the dentist should verify before sending, in English. Not shown to the patient."
    )


SYSTEM_PROMPT = """\
You help an orthodontist turn clinical records into a treatment plan presentation for a patient at a \
private, cash-pay dental clinic. The dentist reviews and edits your draft before the patient sees it.

How to work:
- The dentist's notes are the source of truth for the diagnosis. Use the X-rays and photos to understand \
the case and to check the notes, not to add diagnoses of your own. If an image seems to show something \
the notes don't mention, or contradicts them, put that in dentist_checks instead of in the findings.
- Write everything the patient reads (summary, findings, option notes) in {language}, in plain everyday \
words a person with no dental background understands. Address the patient politely and directly.
- Describe what is expected, not what is guaranteed. Treatment results vary, so avoid promises, fear, \
urgency and sales pressure. A patient who understands their situation decides better than one who is pushed.
- Offer only treatments from the clinic catalog below, at most three, and mark exactly one as recommended. \
Leave out any option that is clinically unsuitable for this case and say why in dentist_checks. Prices come \
from the catalog; do not mention amounts.
- Estimate the simulator values from measurements in the notes. Where the notes give no number, estimate \
from the description, and use the normal value (overjet 2.5, overbite 2, everything else 0) when a feature \
isn't mentioned.
- align_pct and bite_pct describe how much of the starting problem each option is expected to correct. A \
comprehensive option is 100/100; an option that only straightens the front teeth leaves the bite mostly as it is.

Clinic catalog:
{catalog}
"""


def _catalog_text(settings: dict) -> str:
    lines = []
    for item in settings["catalog"]:
        lines.append(
            f"- id: {item['id']} | {item['name'].get('en') or item['id']} | appliance: {item['appliance']} "
            f"| typical duration: {item['months']} months"
        )
    return "\n".join(lines)


def _image_block(path: Path) -> dict | None:
    """Downscale to the size the model actually uses and re-encode as JPEG."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=88)
    except Exception:  # unreadable file: skip it rather than fail the draft
        log.warning("could not read image %s for drafting", path)
        return None
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(buffer.getvalue()).decode("ascii"),
        },
    }


def build_request(case: dict, attachments: list[dict], settings: dict, model: str) -> dict:
    lang = i18n.lang_of(case.get("language"))
    content: list[dict] = []
    skipped = max(0, len(attachments) - MAX_IMAGES)
    for attachment in attachments[:MAX_IMAGES]:
        block = _image_block(Path(attachment["path"]))
        if block is None:
            continue
        label = "X-ray" if attachment["kind"] == "xray" else "Photo"
        caption = f" — {attachment['caption']}" if attachment.get("caption") else ""
        content.append({"type": "text", "text": f"{label}{caption}:"})
        content.append(block)

    notes = (case.get("notes") or "").strip() or "(no notes written yet)"
    tail = f"Patient's first name: {i18n.first_name(case['patient_name'])}\n\nDentist's notes:\n{notes}"
    if skipped:
        tail += f"\n\n({skipped} more image(s) were attached but not included here.)"
    content.append({"type": "text", "text": tail})

    return {
        "model": model,
        "max_tokens": 16000,
        "system": SYSTEM_PROMPT.format(language=LANGUAGE_NAMES[lang], catalog=_catalog_text(settings)),
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": "high"},
        "output_format": PlanDraft,
        # A declined request is re-run on Anthropic's recommended fallback model
        # instead of coming back empty.
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    }


class DraftError(Exception):
    """AI drafting failed; the caller falls back to the template draft."""


def draft_to_plan(draft: PlanDraft, case: dict, settings: dict, today: date) -> tuple[dict, list[str]]:
    lang = i18n.lang_of(case.get("language"))
    catalog = {item["id"]: item for item in settings["catalog"]}
    checks = list(draft.dentist_checks)

    options: list[dict] = []
    for index, proposed in enumerate(draft.options):
        item = catalog.get(proposed.catalog_id)
        if item is None:
            checks.append(f"The draft proposed an option that is not in your catalog ({proposed.catalog_id}); it was dropped.")
            continue
        option = planning.option_from_catalog(item, lang, index)
        option.update(
            duration_months=proposed.duration_months,
            recommended=proposed.recommended,
            note=proposed.note,
            align_pct=proposed.align_pct,
            bite_pct=proposed.bite_pct,
        )
        options.append(option)
    if not options:
        options = planning.empty_plan(settings, lang, today)["options"]
        checks.append("No usable treatment options were drafted; the catalog defaults were added instead.")
    if not any(o["recommended"] for o in options):
        options[0]["recommended"] = True

    plan = planning.empty_plan(settings, lang, today)
    plan.update(
        summary=draft.summary,
        findings=[f.model_dump() for f in draft.findings],
        sim=draft.simulator.model_dump(),
        options=options,
    )
    return planning.normalize_plan(plan), checks


async def draft_plan(
    case: dict,
    attachments: list[dict],
    settings: dict,
    *,
    model: str,
    today: date,
    client: anthropic.AsyncAnthropic | None = None,
) -> tuple[dict, list[str]]:
    """Return (plan, dentist_checks). Raises DraftError on any failure."""
    client = client or anthropic.AsyncAnthropic()
    request = build_request(case, attachments, settings, model)
    try:
        response = await client.beta.messages.parse(**request)
    except anthropic.AuthenticationError as exc:
        raise DraftError("The Claude API key was rejected.") from exc
    except anthropic.RateLimitError as exc:
        raise DraftError("Claude is rate-limited right now; try again in a minute.") from exc
    except anthropic.APIStatusError as exc:
        raise DraftError(f"Claude returned an error ({exc.status_code}).") from exc
    except anthropic.APIConnectionError as exc:
        raise DraftError("Could not reach the Claude API.") from exc

    if response.stop_reason == "refusal":
        raise DraftError("Claude declined to draft this plan.")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise DraftError("Claude's draft came back incomplete.")

    return draft_to_plan(response.parsed_output, case, settings, today)


Source = Literal["ai", "template"]
