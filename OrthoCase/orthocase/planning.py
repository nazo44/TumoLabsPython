"""Plan structure, clinic defaults, pricing maths and the non-AI plan draft."""

from __future__ import annotations

import copy
import math
import re
from datetime import date, timedelta
from typing import Any

from . import i18n

APPLIANCES = ("metal", "ceramic", "aligners")

# name: (min, max, default). Distances in millimetres.
SIM_RANGES: dict[str, tuple[float, float, float]] = {
    "crowding_upper": (0, 12, 0),
    "crowding_lower": (0, 12, 0),
    "spacing_upper": (0, 10, 0),
    "spacing_lower": (0, 10, 0),
    "diastema": (0, 5, 0),
    "overjet": (-4, 12, 2.5),
    "overbite": (-6, 9, 2),
    "midline": (-5, 5, 0),
    "narrow_upper": (0, 8, 0),
}
TOOTH_RANGES: dict[str, tuple[float, float]] = {
    "rot": (-45, 45),
    "inout": (-4, 4),
    "vert": (-4, 4),
    "tip": (-20, 20),
}
VALID_FDI = {str(q * 10 + n) for q in (1, 2, 3, 4) for n in range(1, 8)}

CURRENCIES = {
    "AMD": {"symbol": "֏", "step": 100},
    "RUB": {"symbol": "₽", "step": 100},
    "GEL": {"symbol": "₾", "step": 1},
    "USD": {"symbol": "$", "step": 1},
    "EUR": {"symbol": "€", "step": 1},
}


def _catalog_item(item_id: str, price: int, months: int) -> dict:
    return {
        "id": item_id,
        "appliance": item_id,
        "price": price,
        "months": months,
        "down_pct": 25,
        "installment_months": 12,
        "full_discount_pct": 5,
        "name": dict(i18n.CATALOG_TEXT[item_id]["name"]),
        "tagline": dict(i18n.CATALOG_TEXT[item_id]["tagline"]),
        "includes": copy.deepcopy(i18n.DEFAULT_INCLUDES),
    }


# Prices are placeholders for a first run; every clinic sets its own.
DEFAULT_SETTINGS: dict[str, Any] = {
    "clinic_name": "Your Dental Clinic",
    "dentist_name": "",
    "phone": "",
    "address": "",
    "currency": "AMD",
    "timezone": "Asia/Yerevan",
    "default_language": "hy",
    "country_code": "374",
    "send_window": [10, 20],
    "followup_days": [1, 3, 7, 14],
    "expire_after_days": 7,
    "plan_valid_days": 30,
    "catalog": [
        _catalog_item("metal", 600_000, 18),
        _catalog_item("ceramic", 850_000, 18),
        _catalog_item("aligners", 1_400_000, 14),
    ],
}


def _clamp(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return round(min(hi, max(lo, number)), 2)


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _int(value: Any, lo: int, hi: int, default: int) -> int:
    return int(_clamp(value, lo, hi, default))


def _localized(value: Any, limit: int) -> dict[str, str]:
    value = value if isinstance(value, dict) else {}
    return {lang: _text(value.get(lang), limit) for lang in i18n.LANGUAGES}


def normalize_settings(raw: dict | None) -> dict:
    """Merge user-supplied settings over the defaults and sanitize them."""
    raw = raw or {}
    base = copy.deepcopy(DEFAULT_SETTINGS)
    out: dict[str, Any] = {
        "clinic_name": _text(raw.get("clinic_name", base["clinic_name"]), 80) or base["clinic_name"],
        "dentist_name": _text(raw.get("dentist_name", ""), 80),
        "phone": _text(raw.get("phone", ""), 40),
        "address": _text(raw.get("address", ""), 160),
        "currency": raw.get("currency") if raw.get("currency") in CURRENCIES else base["currency"],
        "timezone": _text(raw.get("timezone", base["timezone"]), 64) or base["timezone"],
        "default_language": i18n.lang_of(raw.get("default_language", base["default_language"])),
        "country_code": re.sub(r"\D", "", str(raw.get("country_code", base["country_code"])))[:4] or "374",
        "expire_after_days": _int(raw.get("expire_after_days"), 1, 60, base["expire_after_days"]),
        "plan_valid_days": _int(raw.get("plan_valid_days"), 1, 365, base["plan_valid_days"]),
    }

    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(out["timezone"])
    except Exception:
        out["timezone"] = base["timezone"]

    window = raw.get("send_window") or base["send_window"]
    try:
        start, end = int(window[0]), int(window[1])
    except (TypeError, ValueError, IndexError):
        start, end = base["send_window"]
    start = min(23, max(0, start))
    end = min(24, max(start + 1, end))
    out["send_window"] = [start, end]

    days: list[int] = []
    for value in raw.get("followup_days") or base["followup_days"]:
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= day <= 120 and day not in days:
            days.append(day)
    out["followup_days"] = sorted(days)[:8] or list(base["followup_days"])

    catalog: list[dict] = []
    seen: set[str] = set()
    for item in raw.get("catalog") or base["catalog"]:
        if not isinstance(item, dict):
            continue
        item_id = re.sub(r"[^a-z0-9_-]", "", str(item.get("id", "")).lower())[:24]
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        includes_raw = item.get("includes") if isinstance(item.get("includes"), dict) else {}
        catalog.append(
            {
                "id": item_id,
                "appliance": item.get("appliance") if item.get("appliance") in APPLIANCES else "metal",
                "price": _int(item.get("price"), 0, 1_000_000_000, 0),
                "months": _int(item.get("months"), 1, 60, 18),
                "down_pct": _int(item.get("down_pct"), 0, 100, 25),
                "installment_months": _int(item.get("installment_months"), 0, 60, 12),
                "full_discount_pct": _int(item.get("full_discount_pct"), 0, 50, 0),
                "name": _localized(item.get("name"), 80),
                "tagline": _localized(item.get("tagline"), 240),
                "includes": {
                    lang: [_text(x, 120) for x in (includes_raw.get(lang) or []) if _text(x, 120)][:8]
                    for lang in i18n.LANGUAGES
                },
            }
        )
    out["catalog"] = catalog[:8] or copy.deepcopy(base["catalog"])
    return out


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------


def default_sim() -> dict:
    sim: dict[str, Any] = {name: spec[2] for name, spec in SIM_RANGES.items()}
    sim["teeth"] = {}
    return sim


def normalize_sim(raw: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    sim: dict[str, Any] = {
        name: _clamp(raw.get(name, default), lo, hi, default) for name, (lo, hi, default) in SIM_RANGES.items()
    }
    teeth: dict[str, dict] = {}
    for fdi, values in (raw.get("teeth") or {}).items():
        if str(fdi) not in VALID_FDI or not isinstance(values, dict):
            continue
        entry = {k: _clamp(values.get(k, 0), lo, hi, 0) for k, (lo, hi) in TOOTH_RANGES.items()}
        if any(entry.values()):
            teeth[str(fdi)] = entry
    sim["teeth"] = teeth
    return sim


def option_from_catalog(item: dict, lang: str, index: int) -> dict:
    lang = i18n.lang_of(lang)
    price = int(item["price"])
    return {
        "id": f"opt{index + 1}",
        "catalog_id": item["id"],
        "name": item["name"].get(lang) or item["name"].get("en") or item["id"],
        "tagline": item["tagline"].get(lang) or item["tagline"].get("en") or "",
        "appliance": item["appliance"],
        "duration_months": int(item["months"]),
        "price": price,
        "down_payment": round_to_step(price * item["down_pct"] / 100, 1000 if price >= 100_000 else 1),
        "installment_months": int(item["installment_months"]),
        "full_discount_pct": int(item["full_discount_pct"]),
        "includes": list(item["includes"].get(lang) or item["includes"].get("en") or []),
        "note": "",
        "recommended": False,
        "align_pct": 100,
        "bite_pct": 100,
    }


def normalize_option(raw: dict, index: int) -> dict:
    price = _int(raw.get("price"), 0, 1_000_000_000, 0)
    return {
        "id": re.sub(r"[^a-zA-Z0-9_-]", "", str(raw.get("id") or f"opt{index + 1}"))[:24] or f"opt{index + 1}",
        "catalog_id": _text(raw.get("catalog_id"), 24),
        "name": _text(raw.get("name"), 80) or f"Option {index + 1}",
        "tagline": _text(raw.get("tagline"), 240),
        "appliance": raw.get("appliance") if raw.get("appliance") in APPLIANCES else "metal",
        "duration_months": _int(raw.get("duration_months"), 1, 60, 18),
        "price": price,
        "down_payment": min(price, _int(raw.get("down_payment"), 0, 1_000_000_000, 0)),
        "installment_months": _int(raw.get("installment_months"), 0, 60, 0),
        "full_discount_pct": _int(raw.get("full_discount_pct"), 0, 50, 0),
        "includes": [_text(x, 120) for x in (raw.get("includes") or []) if _text(x, 120)][:8],
        "note": _text(raw.get("note"), 400),
        "recommended": bool(raw.get("recommended")),
        "align_pct": _int(raw.get("align_pct"), 0, 100, 100),
        "bite_pct": _int(raw.get("bite_pct"), 0, 100, 100),
    }


def normalize_plan(raw: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    findings = []
    for item in raw.get("findings") or []:
        if not isinstance(item, dict):
            continue
        title, detail = _text(item.get("title"), 120), _text(item.get("detail"), 600)
        if title or detail:
            findings.append({"title": title, "detail": detail})

    options: list[dict] = []
    used_ids: set[str] = set()
    for index, item in enumerate(raw.get("options") or []):
        if not isinstance(item, dict):
            continue
        option = normalize_option(item, index)
        while option["id"] in used_ids:
            option["id"] += "x"
        used_ids.add(option["id"])
        options.append(option)
    options = options[:4]

    # Exactly one recommended option when there are several.
    recommended = [o for o in options if o["recommended"]]
    for extra in recommended[1:]:
        extra["recommended"] = False

    valid_until = _text(raw.get("valid_until"), 10)
    try:
        date.fromisoformat(valid_until)
    except ValueError:
        valid_until = ""

    return {
        "summary": _text(raw.get("summary"), 1500),
        "findings": findings[:10],
        "sim": normalize_sim(raw.get("sim")),
        "options": options,
        "valid_until": valid_until,
    }


def empty_plan(settings: dict, lang: str, today: date) -> dict:
    options = [option_from_catalog(item, lang, i) for i, item in enumerate(settings["catalog"][:3])]
    if options:
        options[0]["recommended"] = True
    return normalize_plan(
        {
            "summary": "",
            "findings": [],
            "sim": default_sim(),
            "options": options,
            "valid_until": (today + timedelta(days=settings["plan_valid_days"])).isoformat(),
        }
    )


# --------------------------------------------------------------------------
# Pricing
# --------------------------------------------------------------------------


def round_to_step(value: float, step: int) -> int:
    return int(round(value / step) * step)


def price_breakdown(option: dict, currency: str) -> dict:
    """Totals a patient sees. Installments are interest-free by construction:
    the monthly amount is the remaining balance split evenly, rounded up to
    the currency step so the clinic is never short."""
    step = CURRENCIES.get(currency, CURRENCIES["AMD"])["step"]
    total = int(option["price"])
    down = min(total, int(option["down_payment"]))
    months = int(option["installment_months"])
    monthly = 0
    if months > 0 and total > down:
        monthly = int(math.ceil((total - down) / months / step) * step)
    full = round_to_step(total * (1 - option["full_discount_pct"] / 100), step)
    return {
        "total": total,
        "down": down,
        "months": months if monthly else 0,
        "monthly": monthly,
        "full_price": full,
        "savings": total - full,
    }


# --------------------------------------------------------------------------
# Draft without AI: read the dentist's notes for known conditions.
# --------------------------------------------------------------------------

_MM = r"(\d+(?:[.,]\d+)?)\s*(?:mm|мм|մմ)"

_CONDITION_PATTERNS: dict[str, str] = {
    "crowding": r"crowd|скучен|խիտ|կուտակ",
    "diastema": r"diastema|диастем|դիաստեմ",
    "spacing": r"spacing|spaced|gaps?\b|трем|промежут|բացվածք",
    "underbite": r"underbite|class\s*(?:iii|3)|мезиальн|обратн\w*\s+прикус|iii\s*класс|класс\w*\s*iii|3\s*класс",
    "overjet": r"overjet|protru|сагиттальн|протрузи|дистальн\w*\s+прикус|class\s*(?:ii|2)\b|ii\s*класс|класс\w*\s*ii\b|2\s*класс",
    "openbite": r"open\s*bite|открыт\w*\s+прикус|բաց\s+կծվածք",
    "deepbite": r"deep\s*bite|overbite|глубок\w*\s+прикус|խոր\w*\s+կծվածք",
    "crossbite": r"cross\s*bite|narrow|перекр[её]стн|суж|խաչաձև|նեղ",
    "midline": r"midline|средн\w*\s+лини|միջին\s+գծ",
}
_DEFAULT_MM = {
    "crowding": 4.0, "spacing": 3.0, "diastema": 2.0, "overjet": 6.0, "underbite": -1.0,
    "deepbite": 5.0, "openbite": -2.0, "crossbite": 5.0, "midline": 2.0,
}
_MILD = r"mild|slight|минимальн|легк|незначит|небольш|թեթև|փոքր"
_SEVERE = r"severe|significant|выражен|значительн|тяж[её]л|сильн|ծանր|արտահայտված|զգալի"
_UPPER = r"upper|maxill|верхн|վերին"
_LOWER = r"lower|mandib|нижн|ստորին"


def conditions_from_notes(notes: str) -> dict[str, dict]:
    """Find conditions mentioned in free-text notes (English, Russian, Armenian).

    Returns {condition: {"mm": float, "upper": bool, "lower": bool}}.
    """
    found: dict[str, dict] = {}
    text = notes.lower()
    # Split on sentence and clause punctuation, but never inside a number ("4.5 mm", "4,5 мм").
    for sentence in re.split(r"(?:(?<!\d)\.|\.(?!\d)|[;\n])+", text):
        # Clauses inside a sentence, so "crowding upper 5 mm, lower 3 mm" works.
        clauses = re.split(r"(?<!\d),|,(?!\d)|\band\b|\bи\b|\bև\b", sentence)
        current: str | None = None
        for clause in clauses:
            matched = [c for c, pattern in _CONDITION_PATTERNS.items() if re.search(pattern, clause)]
            if matched:
                current = matched[0]
            elif current is None:
                continue
            for condition in matched or [current]:
                entry = found.setdefault(condition, {"mm": None, "upper": None, "lower": None})
                mm_match = re.search(_MM, clause)
                value: float | None = None
                if mm_match:
                    value = float(mm_match.group(1).replace(",", "."))
                    if condition in ("underbite", "openbite"):
                        value = -abs(value)
                is_upper, is_lower = bool(re.search(_UPPER, clause)), bool(re.search(_LOWER, clause))
                if value is None and entry["mm"] is None:
                    scale = 0.5 if re.search(_MILD, clause) else 1.6 if re.search(_SEVERE, clause) else 1.0
                    entry["_scale"] = scale
                if condition in ("crowding", "spacing") and (is_upper or is_lower):
                    if is_upper:
                        entry["upper"] = value if value is not None else entry["upper"] or True
                    if is_lower:
                        entry["lower"] = value if value is not None else entry["lower"] or True
                elif value is not None:
                    entry["mm"] = value

    for condition, entry in found.items():
        scale = entry.pop("_scale", 1.0)
        if entry["mm"] is None:
            entry["mm"] = round(_DEFAULT_MM[condition] * scale, 1)
    return found


def sim_from_conditions(conditions: dict[str, dict]) -> dict:
    sim = default_sim()

    def per_arch(name: str) -> tuple[float, float]:
        entry = conditions[name]
        upper, lower = entry["upper"], entry["lower"]
        if upper is None and lower is None:
            return entry["mm"], round(entry["mm"] * 0.75, 1)
        resolve = lambda v: 0.0 if v is None else entry["mm"] if v is True else float(v)
        return resolve(upper), resolve(lower)

    if "crowding" in conditions:
        sim["crowding_upper"], sim["crowding_lower"] = per_arch("crowding")
    if "spacing" in conditions:
        sim["spacing_upper"], sim["spacing_lower"] = per_arch("spacing")
    if "diastema" in conditions:
        sim["diastema"] = conditions["diastema"]["mm"]
    if "overjet" in conditions:
        sim["overjet"] = conditions["overjet"]["mm"]
    if "underbite" in conditions:
        sim["overjet"] = conditions["underbite"]["mm"]
    if "deepbite" in conditions:
        sim["overbite"] = conditions["deepbite"]["mm"]
    if "openbite" in conditions:
        sim["overbite"] = conditions["openbite"]["mm"]
    if "crossbite" in conditions:
        sim["narrow_upper"] = conditions["crossbite"]["mm"]
    if "midline" in conditions:
        sim["midline"] = conditions["midline"]["mm"]
    return normalize_sim(sim)


def template_draft(case: dict, settings: dict, today: date) -> dict:
    """Plan draft built from keyword matches in the notes — used when AI
    drafting is unavailable. The dentist still reviews and edits everything."""
    lang = i18n.lang_of(case.get("language"))
    conditions = conditions_from_notes(case.get("notes") or "")
    findings = [
        {"title": i18n.FINDINGS[c][lang][0], "detail": i18n.FINDINGS[c][lang][1]}
        for c in i18n.FINDINGS
        if c in conditions
    ]
    plan = empty_plan(settings, lang, today)
    plan["summary"] = i18n.SUMMARY_TEMPLATE[lang].format(name=i18n.first_name(case["patient_name"]))
    plan["findings"] = findings
    plan["sim"] = sim_from_conditions(conditions)
    return normalize_plan(plan)
