"""审核规则引擎。

把 data/rules.json 中的规则配置解释为可执行检查，输出：
- risk_level: none / low / medium / high
- risks: 每条风险的规则 id、原因、严重级别
- missing_fields: 素材本身缺失的字段（信息不足场景的判定依据）
- supplement_fields: 可通过「补查」获取的字段
- unresolved_high / unresolved_medium: 补查后仍无法自动消解的风险（需人工复核 / 阻断）

字段口径统一说明
----------------
`missing_required_fields` 是**唯一**的「缺失字段」判定入口，工具层
（read_material）与规则层（evaluate_rules）共用，避免两处口径漂移：
- 粗粒度字段名：duration_sec / authorization / channels / copy_text / review_notes
- review_notes 仅在素材带敏感标签时才算缺失（无敏感标签时不需要复核结论）
- copy_text 缺失会导致禁用词检查无法执行，按 R-WORD 中风险处理
"""

from __future__ import annotations

from typing import Any

SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}

# 需要通过补查获取的字段（read_material 的 fields 参数取值来源）
SUPPLEMENTABLE_FIELDS = ("duration_sec", "authorization", "channels", "copy_text", "review_notes")


def _auth_state(material: dict) -> tuple[bool, list[str]]:
    auth = material.get("authorization")
    if not isinstance(auth, dict):
        return False, ["authorization"]
    missing: list[str] = []
    if not auth.get("licensed"):
        missing.append("authorization.licensed")
    if not auth.get("license_id"):
        missing.append("authorization.license_id")
    if not auth.get("valid_until"):
        missing.append("authorization.valid_until")
    return (not missing), missing


def _duration_state(material: dict) -> tuple[bool, list[str]]:
    # 图文素材允许 duration_sec == 0；缺失(None)或负数视为非法
    if "duration_sec" not in material or material.get("duration_sec") is None:
        return False, ["duration_sec"]
    value = material.get("duration_sec")
    if not isinstance(value, (int, float)) or value < 0:
        return False, ["duration_sec"]
    return True, []


def missing_required_fields(material: dict) -> list[str]:
    """返回素材当前缺失的、影响审核与排期的字段（粗粒度字段名）。"""
    missing: list[str] = []

    if not _duration_state(material)[0]:
        missing.append("duration_sec")
    if not _auth_state(material)[0]:
        missing.append("authorization")
    if not (material.get("channels") or []):
        missing.append("channels")
    if not (material.get("copy_text") or "").strip():
        missing.append("copy_text")
    # 只有带敏感标签时，复核结论才是必需信息
    if (material.get("sensitivity_tags") or []) and not (material.get("review_notes") or "").strip():
        missing.append("review_notes")
    return missing


def evaluate_rules(material: dict, rules_cfg: dict) -> dict[str, Any]:
    """对单个素材执行全部规则检查。"""
    risks: list[dict[str, Any]] = []
    supplement_fields: list[str] = []
    banned_hit: list[str] = []

    rules = {r["id"]: r for r in rules_cfg.get("rules", [])}

    missing_fields = missing_required_fields(material)

    # R-AUTH
    ok, miss = _auth_state(material)
    if not ok:
        supplement_fields.append("authorization")
        risks.append(
            {
                "rule_id": "R-AUTH",
                "name": rules["R-AUTH"]["name"],
                "severity": "high",
                "reason": rules["R-AUTH"]["message"],
                "detail": f"缺失字段: {', '.join(miss)}",
                "supplement_hint": rules["R-AUTH"]["supplement_hint"],
            }
        )

    # R-DUR
    ok_dur, miss_dur = _duration_state(material)
    if not ok_dur:
        supplement_fields.append("duration_sec")
        risks.append(
            {
                "rule_id": "R-DUR",
                "name": rules["R-DUR"]["name"],
                "severity": "medium",
                "reason": rules["R-DUR"]["message"],
                "detail": f"缺失或非法字段: {', '.join(miss_dur)}",
                "supplement_hint": rules["R-DUR"]["supplement_hint"],
            }
        )

    # R-SENS
    tags = material.get("sensitivity_tags") or []
    if tags:
        review = material.get("review_notes")
        supplement_fields.append("review_notes")
        risks.append(
            {
                "rule_id": "R-SENS",
                "name": rules["R-SENS"]["name"],
                "severity": "high",
                "reason": rules["R-SENS"]["message"],
                "detail": f"敏感标签: {', '.join(tags)}；复核结论: {review or '未提供'}",
                "supplement_hint": rules["R-SENS"]["supplement_hint"],
            }
        )

    # R-WORD
    word_rule = rules.get("R-WORD", {})
    banned = word_rule.get("banned_words", [])
    copy_text = material.get("copy_text") or ""
    banned_hit = [w for w in banned if w and w in copy_text]
    if banned_hit:
        supplement_fields.append("copy_text")
        risks.append(
            {
                "rule_id": "R-WORD",
                "name": word_rule.get("name", "禁用词检查"),
                "severity": "medium",
                "reason": word_rule.get("message", "文案命中禁用词"),
                "detail": f"命中禁用词: {', '.join(banned_hit)}",
                "supplement_hint": None,
            }
        )
    elif not copy_text.strip():
        # 文案缺失 -> 禁用词检查无法执行，按同一规则中风险处理
        supplement_fields.append("copy_text")
        risks.append(
            {
                "rule_id": "R-WORD",
                "name": word_rule.get("name", "禁用词检查"),
                "severity": "medium",
                "reason": word_rule.get("message", "文案命中禁用词"),
                "detail": "文案缺失，无法执行禁用词检查",
                "supplement_hint": None,
            }
        )

    # R-CHAN
    if not (material.get("channels") or []):
        supplement_fields.append("channels")
        risks.append(
            {
                "rule_id": "R-CHAN",
                "name": rules["R-CHAN"]["name"],
                "severity": "low",
                "reason": rules["R-CHAN"]["message"],
                "detail": "未填写投放渠道",
                "supplement_hint": rules["R-CHAN"]["supplement_hint"],
            }
        )

    risk_level = "none"
    for risk in risks:
        if SEVERITY_ORDER[risk["severity"]] > SEVERITY_ORDER[risk_level]:
            risk_level = risk["severity"]

    # 补查后仍无法自动消解的项
    unresolved_high = [
        r["rule_id"] for r in risks if r["severity"] == "high" and not _resolvable(r, material)
    ]
    unresolved_medium = [
        r["rule_id"] for r in risks if r["severity"] == "medium" and not _resolvable(r, material)
    ]

    need_supplement = bool(supplement_fields) and bool(risks) and risk_level in ("medium", "high")

    return {
        "risk_level": risk_level,
        "risks": risks,
        "missing_fields": missing_fields,
        "supplement_fields": sorted(set(supplement_fields)),
        "banned_words_hit": banned_hit,
        "unresolved_high": unresolved_high,
        "unresolved_medium": unresolved_medium,
        "need_supplement": need_supplement,
        "auto_clearable": not unresolved_high and not unresolved_medium,
    }


def _resolvable(risk: dict, material: dict) -> bool:
    """判断该风险能否通过补查自动消解。"""
    rule_id = risk["rule_id"]
    if rule_id == "R-AUTH":
        return _auth_state(material)[0]
    if rule_id == "R-DUR":
        return _duration_state(material)[0]
    if rule_id == "R-SENS":
        return bool((material.get("review_notes") or "").strip())
    if rule_id == "R-WORD":
        # 禁用词需人工改写文案；文案缺失时补查可解决，但命中禁用词不可自动消解
        if risk.get("detail", "").startswith("文案缺失"):
            return bool((material.get("copy_text") or "").strip())
        return False
    if rule_id == "R-CHAN":
        return bool(material.get("channels"))
    return False
