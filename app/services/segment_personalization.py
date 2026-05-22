"""Segment personalization helpers for Stockpia chatbot.

v4 principles:
- Cache can split by survey segment, including skipped users.
- Prompts receive strict lens instructions.
- User-facing responses should not expose labels like "공격투자형 기준" unless explicitly needed.
"""
from __future__ import annotations
from typing import Any, Dict, Optional
import copy

DEFAULT_SEGMENT = "risk-neutral"
SKIP_SEGMENT = "skip"

RISK_PROFILES: Dict[str, Dict[str, Any]] = {
    "risk-safe": {
        "label": "안정형",
        "score": 20,
        "tone": "손실 가능성, 변동성, 재무 안정성, 현금흐름 안정성을 먼저 설명하고 낙관 표현을 줄인다.",
        "lens": "방어적으로 보면 손실 가능성과 재무 안정성부터 확인해야 해요.",
        "report_extra": "상승 여력보다 손실 가능성, 부채 부담, 이익 안정성을 먼저 점검해요.",
    },
    "risk-conservative": {
        "label": "안정추구형",
        "score": 40,
        "tone": "안정성을 우선하되 성장 요인과 수익 기회를 보조적으로 정리한다.",
        "lens": "안정성을 우선 보면서 성장 기대가 유지되는지 함께 확인해요.",
        "report_extra": "안정성에 무리가 없는 범위에서 성장 기대가 이어지는지 확인해요.",
    },
    "risk-neutral": {
        "label": "위험중립형",
        "score": 60,
        "tone": "기회와 위험을 같은 비중으로 비교하고 과도한 확신 표현을 피한다.",
        "lens": "기대 요인과 위험 요인을 균형 있게 비교해요.",
        "report_extra": "호재와 부담 요인을 같은 비중으로 비교해 볼 필요가 있어요.",
    },
    "risk-active": {
        "label": "적극투자형",
        "score": 80,
        "tone": "성장성, 모멘텀, 수급, 변동성을 함께 설명하되 리스크 관리 관점을 포함한다.",
        "lens": "성장성과 모멘텀을 보되 변동성 확대 가능성도 함께 확인해요.",
        "report_extra": "성장성·수급·모멘텀을 보면서 변동성 관리 포인트도 함께 확인해요.",
    },
    "risk-aggressive": {
        "label": "공격투자형",
        "score": 100,
        "tone": "기회 요인과 단기 모멘텀을 적극적으로 보되 과열, 손실 확대, 대응 기준을 반드시 포함한다.",
        "lens": "기회 요인을 보더라도 과열 여부와 손실 확대 가능성을 같이 점검해요.",
        "report_extra": "단기 기회 요인은 볼 수 있지만 과열, 손실 확대, 대응 기준을 함께 확인해야 해요.",
    },
    SKIP_SEGMENT: {
        "label": "미분류",
        "score": 0,
        "tone": "투자성향이 없는 기본 사용자로 보고 쉬운 표현과 균형 잡힌 정보 제공을 우선한다.",
        "lens": "기본 관점으로 핵심만 균형 있게 확인해요.",
        "report_extra": "기본 관점에서는 핵심 지표와 위험 요인을 함께 확인하는 게 좋아요.",
    },
}


def normalize_segment(segment: Optional[str]) -> str:
    s = (segment or DEFAULT_SEGMENT).strip().lower().replace("_", "-")
    if s in {"skip", "skipped", "survey-skip", "no-survey"}:
        return SKIP_SEGMENT
    if not s.startswith("risk-"):
        if "risk-" in s:
            tail = s.split("risk-", 1)[1]
            s = "risk-" + tail
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
        "transaction": "거래내역 분석",
    }.get(domain, "응답")
    return (
        "\n\n[내부 개인화 지침: 사용자에게 이 문단을 노출하지 말 것]\n"
        f"- 대상 기능: {domain_label}\n"
        f"- 투자성향 세그먼트: {p['label']} ({p['score']}점 구간)\n"
        f"- 설명 렌즈: {p['tone']}\n"
        "- 최종 응답에는 '개인화 기준', '투자성향', '공격투자형/안정형 기준' 같은 라벨을 쓰지 않는다.\n"
        "- 성향명 자체를 드러내지 말고 문장 톤과 체크 포인트에만 반영한다.\n"
        "- 같은 원자료라도 강조점, 리스크 표현, 예시 난이도를 다르게 작성한다.\n"
        "- 매수/매도 단정, 수익 보장, 직접 추천 표현은 금지한다.\n"
    )


def get_personalization_note(segment: Optional[str], *, domain: str = "common") -> str:
    return get_segment_profile(segment).get("lens", "")


def _append_note(text: str, note: str) -> str:
    base = (text or "").rstrip()
    if not note or note in base:
        return base
    return f"{base}\n\n{note}".strip()


def apply_personalization_to_raw_report(raw_report: Dict[str, Any], segment: Optional[str], profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """raw_report_service 결과 섹션 텍스트에 세그먼트별 해석 포인트를 자연스럽게 녹인다."""
    if not isinstance(raw_report, dict):
        return raw_report
    p = get_segment_profile(segment, profile)
    report = raw_report.get("report")
    if not isinstance(report, dict):
        return raw_report
    sections = report.get("sections")
    if not isinstance(sections, dict):
        return raw_report

    extra = p.get("report_extra") or p.get("lens") or ""
    if "summary" in sections:
        sections["summary"] = _append_note(sections.get("summary", ""), extra)
    for key in ("financial", "financial_analysis"):
        if key in sections and p["segment"] in {"risk-safe", "risk-conservative"}:
            sections[key] = _append_note(sections.get(key, ""), "재무 안정성과 현금흐름이 흔들리는 구간인지 함께 확인해요.")
    for key in ("stock_price", "price_analysis"):
        if key in sections and p["segment"] in {"risk-active", "risk-aggressive"}:
            sections[key] = _append_note(sections.get(key, ""), "단기 모멘텀은 유효해도 변동성 확대 구간인지 같이 확인해요.")
    if "investment_opinion" in sections:
        sections["investment_opinion"] = _append_note(sections.get("investment_opinion", ""), extra)
    report["personalization"] = {"segment": p["segment"], "label": p["label"], "score": p["score"]}
    return raw_report


def _sanitize_visible_personalization_labels(text: str) -> str:
    if not isinstance(text, str):
        return text
    banned = (
        "개인화 기준:",
        "공격투자형 기준으로는",
        "안정형 기준으로는",
        "안정추구형 기준으로는",
        "위험중립형 기준으로는",
        "적극투자형 기준으로는",
        "투자성향 기준으로는",
    )
    out = text
    for token in banned:
        out = out.replace(token, "")
    return out.strip()


def apply_personalization_to_kakao(skill: Dict[str, Any], segment: Optional[str], *, domain: str = "common") -> Dict[str, Any]:
    # v5: Kakao 최종 응답에 노골적인 성향 안내문을 덧붙이지 않는다.
    # 혹시 LLM/legacy formatter가 라벨을 누출하면 마지막 단계에서 제거한다.
    if not isinstance(skill, dict):
        return skill
    template = skill.get("template")
    if not isinstance(template, dict):
        return skill
    outputs = template.get("outputs")
    if not isinstance(outputs, list):
        return skill
    for output in outputs:
        if not isinstance(output, dict):
            continue
        simple = output.get("simpleText")
        if isinstance(simple, dict) and isinstance(simple.get("text"), str):
            simple["text"] = _sanitize_visible_personalization_labels(simple["text"])
        card = output.get("basicCard")
        if isinstance(card, dict):
            if isinstance(card.get("title"), str):
                card["title"] = _sanitize_visible_personalization_labels(card["title"])
            if isinstance(card.get("description"), str):
                card["description"] = _sanitize_visible_personalization_labels(card["description"])
    return skill
