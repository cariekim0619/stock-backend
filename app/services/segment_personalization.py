"""Segment personalization helpers for Stockpia chatbot.

Mock-up v2:
- Global cache is split by five immutable risk profiles.
- beginner / experienced remains in user.json for future survey expansion.
- Prompt instructions are only sent to LLMs, while user-visible notes are clean
  result guidance and never expose prompt wording such as "작성해 주세요".
"""
from __future__ import annotations
from typing import Any, Dict, Optional
import copy

DEFAULT_SEGMENT = "risk-neutral"
RISK_PROFILES: Dict[str, Dict[str, Any]] = {
    "risk-safe": {
        "label": "안정형",
        "score": 20,
        "tone": "손실 가능성, 변동성, 재무 안정성을 먼저 설명하고 어려운 용어는 쉽게 풀어쓴다.",
        "report_note": "안정형 기준으로 손실 가능성과 변동성 확인을 우선했어요.",
        "news_note": "안정형 기준으로는 뉴스의 긍정 이슈보다 실적 확인과 변동성 리스크를 먼저 보는 게 좋아요.",
        "community_note": "안정형 기준으로는 커뮤니티 반응을 참고만 하고, 공시와 실적을 먼저 확인하는 게 좋아요.",
    },
    "risk-conservative": {
        "label": "안정추구형",
        "score": 40,
        "tone": "안정성을 우선 설명하되 성장 요인과 수익 기회도 균형 있게 정리한다.",
        "report_note": "안정추구형 기준으로 안정성과 성장 요인을 함께 정리했어요.",
        "news_note": "안정추구형 기준으로는 성장 기대와 함께 실적 지속성, 수급 부담을 같이 확인해 보세요.",
        "community_note": "안정추구형 기준으로는 기대감보다 근거 있는 실적·수급 변화인지 확인해 보세요.",
    },
    "risk-neutral": {
        "label": "위험중립형",
        "score": 60,
        "tone": "기회와 위험을 균형 있게 비교하고 과도한 확신 표현은 피한다.",
        "report_note": "위험중립형 기준으로 기회와 위험을 균형 있게 정리했어요.",
        "news_note": "위험중립형 기준으로는 호재와 악재를 균형 있게 비교해 보는 게 좋아요.",
        "community_note": "위험중립형 기준으로는 긍정·부정 의견의 근거를 같이 비교해 보는 게 좋아요.",
    },
    "risk-active": {
        "label": "적극투자형",
        "score": 80,
        "tone": "성장성, 모멘텀, 수급, 변동성을 함께 설명하되 리스크 관리 관점을 포함한다.",
        "report_note": "적극투자형 기준으로 성장성, 모멘텀, 변동성을 함께 정리했어요.",
        "news_note": "적극투자형 기준으로는 모멘텀과 수급을 보되 변동성 확대 가능성을 함께 확인해 보세요.",
        "community_note": "적극투자형 기준으로는 시장 관심도와 모멘텀을 보되 과열 여부도 함께 확인해 보세요.",
    },
    "risk-aggressive": {
        "label": "공격투자형",
        "score": 100,
        "tone": "공격적 관점의 기회 요인을 보되 손실 확대 가능성, 과열, 손절 기준을 반드시 함께 다룬다.",
        "report_note": "공격투자형 기준으로 기회 요인과 리스크 관리 포인트를 함께 정리했어요.",
        "news_note": "공격투자형 기준으로는 단기 기회 요인과 함께 손실 확대 가능성, 손절 기준을 같이 점검해 보세요.",
        "community_note": "공격투자형 기준으로는 강한 기대감 뒤의 변동성, 손실 확대 위험도 같이 점검해 보세요.",
    },
}


def normalize_segment(segment: Optional[str]) -> str:
    s = (segment or DEFAULT_SEGMENT).strip().lower().replace("_", "-")
    if not s.startswith("risk-"):
        if "risk-" in s:
            s = "risk-" + s.split("risk-", 1)[1].split("-", 1)[0]
        else:
            s = f"risk-{s}"
    return s if s in RISK_PROFILES else DEFAULT_SEGMENT


def get_segment_profile(segment: Optional[str], profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    seg = normalize_segment(segment)
    out = copy.deepcopy(RISK_PROFILES[seg])
    out["segment"] = seg
    if isinstance(profile, dict):
        for key in ("level", "level_label", "risk_profile_5", "risk_label", "risk_score"):
            if profile.get(key) not in (None, ""):
                out[key] = profile[key]
    out.setdefault("risk_label", out["label"])
    out.setdefault("risk_score", out["score"])
    return out


def build_prompt_suffix(segment: Optional[str], *, domain: str = "common", profile: Optional[Dict[str, Any]] = None) -> str:
    p = get_segment_profile(segment, profile)
    domain_label = {
        "report": "종목 리포트",
        "news": "뉴스 요약",
        "community": "커뮤니티 요약",
        "glossary": "용어 설명",
    }.get(domain, "응답")
    return (
        "\n\n[사용자 투자성향 반영 지침]\n"
        f"- 대상 기능: {domain_label}\n"
        f"- 투자성향: {p['label']} ({p['score']}점 구간)\n"
        f"- 설명 톤: {p['tone']}\n"
        "- 같은 원자료를 쓰더라도 이 성향에 맞게 강조점과 표현을 달리한다.\n"
        "- 매수/매도 단정 표현은 금지하고 정보 제공 관점으로 작성한다.\n"
    )


def get_personalization_note(segment: Optional[str], *, domain: str = "common") -> str:
    p = get_segment_profile(segment)
    if domain == "news":
        return p["news_note"]
    if domain == "community":
        return p["community_note"]
    return p["report_note"]


def _append_note(text: str, note: str) -> str:
    base = (text or "").rstrip()
    if not note or note in base:
        return base
    return f"{base}\n\n{note}".strip()


def apply_personalization_to_raw_report(raw_report: Dict[str, Any], segment: Optional[str], profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """raw_report_service 결과의 섹션 텍스트 자체를 5단계 성향별로 달라지게 만든다."""
    if not isinstance(raw_report, dict):
        return raw_report
    p = get_segment_profile(segment, profile)
    note = get_personalization_note(segment, domain="report")
    report = raw_report.get("report")
    if not isinstance(report, dict):
        return raw_report
    sections = report.get("sections")
    if not isinstance(sections, dict):
        return raw_report

    code = normalize_segment(segment)
    extra_by_segment = {
        "risk-safe": "이 구간에서는 상승 여력보다 손실 가능성과 재무 안정성을 먼저 확인하는 관점이 중요해요.",
        "risk-conservative": "이 구간에서는 안정성과 성장 기대가 함께 유지되는지 확인하는 관점이 중요해요.",
        "risk-neutral": "이 구간에서는 기대 요인과 위험 요인을 같은 비중으로 비교하는 관점이 중요해요.",
        "risk-active": "이 구간에서는 성장성·모멘텀을 보되 변동성 확대 가능성을 같이 관리하는 관점이 중요해요.",
        "risk-aggressive": "이 구간에서는 기회 요인을 적극적으로 볼 수 있지만 손실 확대, 과열, 손절 기준까지 같이 점검하는 관점이 중요해요.",
    }
    extra = extra_by_segment.get(code, extra_by_segment[DEFAULT_SEGMENT])

    if "summary" in sections:
        sections["summary"] = _append_note(sections.get("summary", ""), f"{note}\n{extra}")
    if "investment_opinion" in sections:
        sections["investment_opinion"] = _append_note(sections.get("investment_opinion", ""), extra)
    report["personalization"] = {"segment": normalize_segment(segment), "label": p["label"], "score": p["score"]}
    return raw_report


def apply_personalization_to_kakao(skill: Dict[str, Any], segment: Optional[str], *, domain: str = "common") -> Dict[str, Any]:
    if not isinstance(skill, dict):
        return skill
    note = get_personalization_note(segment, domain=domain)
    template = skill.get("template") or {}
    outputs = template.get("outputs") or []
    for output in outputs:
        if not isinstance(output, dict):
            continue
        if isinstance(output.get("simpleText"), dict):
            txt = (output["simpleText"].get("text") or "").rstrip()
            output["simpleText"]["text"] = _append_note(txt, note)
            break
        if isinstance(output.get("basicCard"), dict):
            desc = (output["basicCard"].get("description") or "").rstrip()
            output["basicCard"]["description"] = _append_note(desc, note)
            break
    template["outputs"] = outputs
    skill["template"] = template
    return skill
