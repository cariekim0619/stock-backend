# -*- coding: utf-8 -*-
"""
app/services/segment_personalization.py  (Stage 4 신규)

사전 설문(experience × risk) 기반 응답 차별화 헬퍼.

[설계 원칙]
1. 단일 위치 분기 — system_instruction 한 줄에 segment 라벨 합성
2. 최소 침습 — 기존 prompt 텍스트는 그대로, 앞에 한 줄 prepend만
3. 안전 폴백 — segment="default" 또는 누락이면 기존 동작 100% 유지

[segment 키 포맷]
  "exp-{none|rookie|experienced}_risk-{safe|balanced|aggressive}"
  또는 "default"

[사용 예]
  from app.services.segment_personalization import (
      build_system_instruction,
      build_prompt_prefix,
      get_default_recommend_category,
  )

  # 1. LLM 호출 직전 system_instruction 합성
  base_instruction = "당신은 한국 주식시장 전문 애널리스트입니다..."
  final_instruction = build_system_instruction(base_instruction, segment)

  # 2. prompt 본문 앞에 한 줄 추가 (선택)
  final_prompt = build_prompt_prefix(segment) + base_prompt

  # 3. 추천 카테고리 기본값
  cat = get_default_recommend_category(segment)  # "거래량" | "수익률" | None
"""

from typing import Optional, Tuple


# ============================================================
# segment 파싱
# ============================================================

def parse_segment(segment: Optional[str]) -> Tuple[str, str]:
    """
    "exp-none_risk-safe" → ("none", "safe")
    "default" 또는 누락/형식 오류 → ("default", "default")

    Returns:
        (experience, risk) — 둘 다 문자열
    """
    if not segment or not isinstance(segment, str):
        return ("default", "default")

    s = segment.strip().lower()
    if s == "default" or not s.startswith("exp-"):
        return ("default", "default")

    try:
        # "exp-none_risk-safe" 형태
        parts = s.split("_")
        if len(parts) != 2:
            return ("default", "default")

        exp_part = parts[0]  # "exp-none"
        risk_part = parts[1]  # "risk-safe"

        if not exp_part.startswith("exp-") or not risk_part.startswith("risk-"):
            return ("default", "default")

        exp = exp_part[4:]   # "none"
        risk = risk_part[5:]  # "safe"

        valid_exp = {"none", "rookie", "experienced"}
        valid_risk = {"safe", "balanced", "aggressive"}

        if exp not in valid_exp:
            exp = "default"
        if risk not in valid_risk:
            risk = "default"

        return (exp, risk)
    except Exception:
        return ("default", "default")


# ============================================================
# experience 라벨 — 용어 난이도 + 분량
# ============================================================

_EXP_INSTRUCTION_FRAGMENTS = {
    "none": (
        "사용자는 주식 투자 경험이 전혀 없는 입문자입니다. "
        "PER, ROE, RSI 같은 전문 용어가 등장하면 반드시 한 문장으로 풀어쓰고, "
        "비유와 일상적인 표현을 적극적으로 사용하세요. "
        "어려운 숫자보다 '얼마나 좋은지/나쁜지'를 친근하게 알려주세요."
    ),
    "rookie": (
        "사용자는 투자 경험 1년 미만의 초보자입니다. "
        "기본 용어(PER, PBR 등)는 그대로 쓰되 처음 나올 때 짧은 설명을 괄호로 덧붙이세요. "
        "복잡한 분석은 핵심만 간결하게 전달하세요."
    ),
    "experienced": (
        "사용자는 투자 경험 1년 이상으로 기본 용어와 지표 해석에 익숙합니다. "
        "용어 설명은 생략하고 분석의 핵심과 시장 맥락에 집중하세요. "
        "필요하면 업종 평균이나 전년 동기 대비 같은 비교 정보를 포함하세요."
    ),
    "default": "",  # 빈 문자열 — 기존 동작
}


# ============================================================
# risk 라벨 — 강조 포인트
# ============================================================

_RISK_INSTRUCTION_FRAGMENTS = {
    "safe": (
        "사용자는 안정 지향 투자자입니다. "
        "부채비율, 배당, 변동성, 하방 리스크를 우선 언급하고, "
        "고변동·고성장 종목의 위험 요인을 명확히 짚어주세요."
    ),
    "balanced": (
        "사용자는 균형 지향 투자자입니다. "
        "리스크와 수익률을 균형 있게 언급하세요."
    ),
    "aggressive": (
        "사용자는 공격 지향 투자자입니다. "
        "성장성, 상승 모멘텀, 거래량 변화 같은 적극적 매매 신호를 우선 언급하세요. "
        "다만 리스크 경고도 반드시 포함하세요(서비스 정책)."
    ),
    "default": "",
}


# ============================================================
# 메인 API
# ============================================================

def build_system_instruction(base_instruction: str, segment: Optional[str]) -> str:
    """
    기존 system_instruction에 segment 라벨을 합성.

    Args:
        base_instruction: 원본 system_instruction
                         (예: "당신은 한국 주식시장 전문 애널리스트입니다...")
        segment: 사용자 segment

    Returns:
        segment 라벨이 prepend된 instruction.
        segment="default"이면 base_instruction 그대로 반환.
    """
    exp, risk = parse_segment(segment)

    fragments = []
    exp_frag = _EXP_INSTRUCTION_FRAGMENTS.get(exp, "")
    risk_frag = _RISK_INSTRUCTION_FRAGMENTS.get(risk, "")

    if exp_frag:
        fragments.append(exp_frag)
    if risk_frag:
        fragments.append(risk_frag)

    if not fragments:
        return base_instruction

    segment_block = " ".join(fragments)
    return f"{segment_block}\n\n{base_instruction}"


def build_prompt_prefix(segment: Optional[str]) -> str:
    """
    prompt 본문 앞에 붙일 한 줄 가이드.
    system_instruction에 이미 segment 라벨이 들어가므로 보통은 빈 문자열.
    추가 강조가 필요한 경우에만 호출.

    Returns:
        "" 또는 "[참고] ..." 형태
    """
    exp, risk = parse_segment(segment)
    if exp == "default" and risk == "default":
        return ""

    hints = []
    if exp == "none":
        hints.append("쉬운 비유 사용")
    elif exp == "experienced":
        hints.append("간결한 전문가 톤")

    if risk == "safe":
        hints.append("리스크 우선 언급")
    elif risk == "aggressive":
        hints.append("모멘텀/성장성 우선 언급")

    if not hints:
        return ""

    return f"[사용자 맞춤 안내: {' · '.join(hints)}]\n"


def get_default_recommend_category(segment: Optional[str]) -> Optional[str]:
    """
    risk 축에 따른 추천 카테고리 기본값.
    main 흐름에서 사용자가 추천 진입했을 때 미리 카테고리 한 개를 채워둘 때 사용.

    Returns:
        "거래량" | "수익률" | "안정성" | None(default)
    """
    _, risk = parse_segment(segment)
    mapping = {
        "safe": "안정성",       # 배당/저변동 종목
        "balanced": None,       # 사용자에게 선택 맡김
        "aggressive": "거래량",  # 거래량 급등 종목
    }
    return mapping.get(risk)


def get_summary_max_chars(segment: Optional[str]) -> int:
    """
    리포트 요약 최대 글자 수 권장값.
    experience별 분량 차별화.

    Returns:
        권장 max_chars (실제 강제는 안 함, 가이드용)
    """
    exp, _ = parse_segment(segment)
    mapping = {
        "none": 800,         # 입문자 — 짧게
        "rookie": 1200,      # 초보 — 중간
        "experienced": 1500, # 숙련 — 길게 OK
        "default": 1200,
    }
    return mapping.get(exp, 1200)


# ============================================================
# 디버깅용 — segment 라벨 사람 친화 표시
# ============================================================

def describe_segment(segment: Optional[str]) -> str:
    """segment 키를 한글로 풀어쓰기 (로그/디버깅용)"""
    exp, risk = parse_segment(segment)
    exp_label = {
        "none": "입문자",
        "rookie": "초보",
        "experienced": "숙련자",
        "default": "미설정",
    }.get(exp, "?")
    risk_label = {
        "safe": "안정",
        "balanced": "균형",
        "aggressive": "공격",
        "default": "미설정",
    }.get(risk, "?")
    return f"{exp_label}·{risk_label}"


if __name__ == "__main__":
    # 빠른 검증
    test_cases = [
        ("exp-none_risk-safe",       "입문자·안정"),
        ("exp-experienced_risk-aggressive", "숙련자·공격"),
        ("default",                   "미설정·미설정"),
        (None,                        "미설정·미설정"),
        ("invalid-format",            "미설정·미설정"),
        ("exp-rookie_risk-balanced",  "초보·균형"),
    ]
    print("=" * 50)
    print("parse_segment / describe_segment 검증")
    print("=" * 50)
    for seg, expected in test_cases:
        got = describe_segment(seg)
        ok = "✅" if got == expected else "❌"
        print(f"  {ok} '{seg}' → {got} (목표: {expected})")
    print()

    # build_system_instruction 검증
    base = "당신은 한국 주식시장 전문 애널리스트입니다. 초보 투자자가 이해할 수 있도록 쉽고 친근하게 설명합니다."
    print("=" * 50)
    print("build_system_instruction 검증")
    print("=" * 50)
    for seg in ["default", "exp-none_risk-safe", "exp-experienced_risk-aggressive"]:
        result = build_system_instruction(base, seg)
        print(f"\n[{seg}]")
        print(f"  길이: {len(result)}자")
        print(f"  앞 100자: {result[:100]}...")

    # 추천 카테고리 검증
    print()
    print("=" * 50)
    print("get_default_recommend_category 검증")
    print("=" * 50)
    for seg in ["exp-none_risk-safe", "exp-rookie_risk-balanced", "exp-none_risk-aggressive", "default"]:
        cat = get_default_recommend_category(seg)
        print(f"  '{seg}' → {cat}")
