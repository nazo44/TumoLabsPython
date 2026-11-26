from datetime import date

from orthocase import planning


def sim_for(notes: str) -> dict:
    return planning.sim_from_conditions(planning.conditions_from_notes(notes))


def test_notes_with_decimal_measurements_in_english():
    sim = sim_for("Crowding upper 7 mm, lower 5 mm. Deep bite 4.5 mm. Overjet 5 mm. Midline shifted 1.5 mm.")
    assert (sim["crowding_upper"], sim["crowding_lower"]) == (7.0, 5.0)
    assert sim["overbite"] == 4.5
    assert sim["overjet"] == 5.0
    assert sim["midline"] == 1.5


def test_notes_in_russian_with_comma_decimals():
    sim = sim_for("Скученность верхней челюсти 6,5 мм, нижней 4 мм; глубокий прикус 4,5 мм. Диастема 2 мм.")
    assert (sim["crowding_upper"], sim["crowding_lower"]) == (6.5, 4.0)
    assert sim["overbite"] == 4.5
    assert sim["diastema"] == 2.0


def test_notes_without_numbers_use_defaults_scaled_by_severity():
    mild = sim_for("Mild crowding.")
    severe = sim_for("Severe crowding.")
    assert 0 < mild["crowding_upper"] < severe["crowding_upper"]


def test_open_bite_and_underbite_become_negative():
    sim = sim_for("Open bite 3 mm. Underbite 2 mm.")
    assert sim["overbite"] == -3.0
    assert sim["overjet"] == -2.0


def test_unmentioned_features_stay_normal():
    sim = sim_for("Patient is nervous about treatment.")
    assert sim == planning.normalize_sim(planning.default_sim())


def test_price_breakdown_rounds_installments_up_to_currency_step():
    option = {"price": 850_000, "down_payment": 212_000, "installment_months": 12, "full_discount_pct": 5}
    pricing = planning.price_breakdown(option, "AMD")
    assert pricing["monthly"] == 53_200  # 638 000 / 12 = 53 166.67 -> next 100
    assert pricing["down"] + pricing["monthly"] * pricing["months"] >= pricing["total"]
    assert pricing["full_price"] == 807_500
    assert pricing["savings"] == 42_500


def test_price_breakdown_without_installments():
    option = {"price": 1000, "down_payment": 1000, "installment_months": 12, "full_discount_pct": 0}
    pricing = planning.price_breakdown(option, "USD")
    assert pricing["monthly"] == 0 and pricing["months"] == 0
    assert pricing["full_price"] == 1000 and pricing["savings"] == 0


def test_normalize_plan_clamps_values_and_keeps_one_recommendation():
    plan = planning.normalize_plan(
        {
            "summary": "x" * 5000,
            "findings": [{"title": "A", "detail": "B"}, {"title": "", "detail": ""}, "junk"],
            "sim": {"crowding_upper": 99, "overjet": "abc", "teeth": {"11": {"rot": 400}, "99": {"rot": 5}, "21": {"rot": 0}}},
            "options": [
                {"name": "One", "price": 100, "down_payment": 500, "recommended": True, "appliance": "lasers"},
                {"name": "Two", "price": -5, "recommended": True},
            ],
            "valid_until": "not a date",
        }
    )
    assert len(plan["summary"]) == 1500
    assert plan["findings"] == [{"title": "A", "detail": "B"}]
    assert plan["sim"]["crowding_upper"] == 12
    assert plan["sim"]["overjet"] == 2.5
    assert plan["sim"]["teeth"] == {"11": {"rot": 45, "inout": 0, "vert": 0, "tip": 0}}
    assert [o["recommended"] for o in plan["options"]] == [True, False]
    assert plan["options"][0]["down_payment"] == 100  # never more than the price
    assert plan["options"][0]["appliance"] == "metal"
    assert plan["options"][1]["price"] == 0
    assert plan["valid_until"] == ""


def test_normalize_settings_falls_back_to_safe_values():
    settings = planning.normalize_settings(
        {"currency": "DOGE", "timezone": "Mars/Olympus", "send_window": [25, 3], "followup_days": ["x", 0, 3, 3, 1], "catalog": []}
    )
    assert settings["currency"] == "AMD"
    assert settings["timezone"] == "Asia/Yerevan"
    assert 0 <= settings["send_window"][0] < settings["send_window"][1] <= 24
    assert settings["followup_days"] == [1, 3]
    assert [item["id"] for item in settings["catalog"]] == ["metal", "ceramic", "aligners"]


def test_template_draft_is_written_in_the_patients_language():
    settings = planning.normalize_settings(None)
    case = {"patient_name": "Анна Саркисян", "language": "ru", "notes": "Скученность 5 мм, глубокий прикус"}
    plan = planning.template_draft(case, settings, date(2026, 11, 16))
    assert plan["summary"].startswith("Анна,")
    assert [f["title"] for f in plan["findings"]] == ["Скученность зубов", "Глубокий прикус"]
    assert plan["options"][0]["name"] == "Металлические брекеты"
    assert plan["valid_until"] == "2026-12-16"
