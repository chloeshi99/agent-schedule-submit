"""规则引擎单元测试。"""

from __future__ import annotations

import pytest

from src.domain import rules as rules_mod


def _material(**overrides) -> dict:
    base = {
        "id": "T01",
        "title": "测试素材",
        "content_type": "video",
        "duration_sec": 30,
        "copy_text": "普通文案。",
        "sensitivity_tags": [],
        "review_notes": "已过审。",
        "authorization": {
            "licensed": True,
            "license_id": "LIC-1",
            "valid_until": "2026-12-31",
        },
        "channels": ["视频号"],
    }
    base.update(overrides)
    return base


def test_clean_material_has_no_risk(rules) -> None:
    result = rules_mod.evaluate_rules(_material(), rules)
    assert result["risk_level"] == "none"
    assert result["risks"] == []
    assert result["missing_fields"] == []
    assert result["auto_clearable"] is True


def test_missing_authorization_is_high_risk(rules) -> None:
    result = rules_mod.evaluate_rules(_material(authorization=None), rules)
    assert result["risk_level"] == "high"
    assert "R-AUTH" in [r["rule_id"] for r in result["risks"]]
    assert "authorization" in result["missing_fields"]


def test_incomplete_authorization_is_high_risk(rules) -> None:
    result = rules_mod.evaluate_rules(
        _material(authorization={"licensed": False, "license_id": None, "valid_until": None}), rules
    )
    assert "R-AUTH" in result["unresolved_high"]


def test_missing_duration_is_medium_risk(rules) -> None:
    result = rules_mod.evaluate_rules(_material(duration_sec=None), rules)
    assert "R-DUR" in [r["rule_id"] for r in result["risks"]]
    assert "duration_sec" in result["missing_fields"]
    assert "R-DUR" in result["unresolved_medium"]


def test_zero_duration_for_image_is_valid(rules) -> None:
    """图文素材 duration_sec == 0 合法。"""
    result = rules_mod.evaluate_rules(_material(content_type="image", duration_sec=0), rules)
    assert "R-DUR" not in [r["rule_id"] for r in result["risks"]]


def test_negative_duration_is_invalid(rules) -> None:
    result = rules_mod.evaluate_rules(_material(duration_sec=-5), rules)
    assert "R-DUR" in [r["rule_id"] for r in result["risks"]]


def test_sensitive_tag_requires_review_notes(rules) -> None:
    result = rules_mod.evaluate_rules(
        _material(sensitivity_tags=["financial_promotion"], review_notes=None), rules
    )
    assert "R-SENS" in [r["rule_id"] for r in result["risks"]]
    assert "R-SENS" in result["unresolved_high"]
    assert "review_notes" in result["missing_fields"]


def test_sensitive_tag_with_review_notes_is_resolvable(rules) -> None:
    result = rules_mod.evaluate_rules(
        _material(sensitivity_tags=["financial_promotion"], review_notes="已人工复核通过。"), rules
    )
    assert "R-SENS" not in result["unresolved_high"]


def test_banned_word_hit_is_medium_and_not_auto_resolvable(rules) -> None:
    result = rules_mod.evaluate_rules(_material(copy_text="国家级理财计划。"), rules)
    assert "R-WORD" in [r["rule_id"] for r in result["risks"]]
    assert "R-WORD" in result["unresolved_medium"]
    assert result["banned_words_hit"] == ["国家级"]


def test_missing_copy_text_is_medium_risk(rules) -> None:
    result = rules_mod.evaluate_rules(_material(copy_text=""), rules)
    assert "R-WORD" in [r["rule_id"] for r in result["risks"]]
    assert "copy_text" in result["missing_fields"]


def test_missing_channel_is_low_risk(rules) -> None:
    result = rules_mod.evaluate_rules(_material(channels=[]), rules)
    assert result["risk_level"] == "low"
    assert "channels" in result["missing_fields"]


def test_need_supplement_only_for_medium_and_above(rules) -> None:
    """低风险（仅提示）不触发补查循环。"""
    result = rules_mod.evaluate_rules(_material(channels=[]), rules)
    assert result["risk_level"] == "low"
    assert result["need_supplement"] is False


def test_missing_required_fields_uses_single_source_of_truth() -> None:
    """缺失字段口径在规则层唯一。"""
    m = _material(duration_sec=None, authorization=None, channels=[])
    missing = rules_mod.missing_required_fields(m)
    assert "duration_sec" in missing
    assert "authorization" in missing
    assert "channels" in missing


def test_review_notes_not_required_without_sensitive_tags() -> None:
    m = _material(review_notes=None)
    assert "review_notes" not in rules_mod.missing_required_fields(m)


@pytest.mark.parametrize("field", ["duration_sec", "authorization", "channels", "copy_text"])
def test_supplementable_fields_are_known(field: str) -> None:
    assert field in rules_mod.SUPPLEMENTABLE_FIELDS
