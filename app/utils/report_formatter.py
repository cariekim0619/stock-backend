from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import re


class ReportFormatter:
    """
    S02 종목 리포트 스펙에 맞게
    내부 리포트 JSON -> Kakao 스킬 응답(JSON) 으로 변환하는 유틸리티
    """

    # ------------------------------------------------------------------
    # 1) 기존 카드형 리포트 (itemCard 여러 장) – 필요시 그대로 사용 가능
    # ------------------------------------------------------------------
    @staticmethod
    def build_success_response(report_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Case 1. 정상 응답 (멀티 itemCard 카드 뷰)
        """
        item_cards: List[Dict[str, Any]] = [
            ReportFormatter._build_summary_card(report_data),
            ReportFormatter._build_price_card(report_data),
            ReportFormatter._build_financial_card(report_data),
            ReportFormatter._build_valuation_card(report_data),
            ReportFormatter._build_opinion_card(report_data),
        ]

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"itemCard": card} for card in item_cards],
                "quickReplies": ReportFormatter._build_common_quick_replies(),
            },
        }

    # ------------------------------------------------------------------
    # 2) 새 포맷 – 단일 simpleText 리포트 (요청하신 형태)
    # ------------------------------------------------------------------
    @staticmethod
    def build_from_raw_report(ticker: str, report_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        raw_report_service.generate_raw_report() 결과를
        사용자가 읽기 쉬운 하나의 simpleText Kakao 스킬 JSON으로 변환한다.

        예시:

        📊 삼성전자 투자 리포트
        생성일시: 2025-11-25 00:28:03
        재무제표: 있음

        ### [1. 투자 요약]
        ...

        ### [5. 투자 의견]
        **종합 투자 의견: 매수 (BUY)**
        **목표주가: 115,000원**
        """
        # report_data 구조가 예상과 다르면 에러 응답으로 대체
        if not isinstance(report_data, dict):
            return ReportFormatter.build_error_response()

        # ---------------- 기본 정보 ----------------
        name = (
            report_data.get("name")
            or report_data.get("raw_data", {}).get("basic", {}).get("name")
            or ticker
        )
        generated_at = report_data.get("generated_at") or ""

        report_block = report_data.get("report", {}) or {}
        sections = report_block.get("sections", {}) or {}
        has_financials = bool(
            report_block.get("has_financials")
            or report_data.get("raw_data", {}).get("financial_text")
        )

        # ✅ 3번 섹션 기본 문구
        FIN_PLACEHOLDER = (
            "재무제표(매출, 영업이익, 순이익 등)에 대한 상세 분석은 "
            "향후 DART 재무제표 데이터를 연동해 확장할 수 있습니다."
        )

        def _get_section(key: str, fallback: str) -> str:
            txt = sections.get(key) or ""
            txt = str(txt).strip()
            return txt if txt else fallback

        summary_text = _get_section("summary", "요약 정보가 없습니다.")
        price_analysis_text = _get_section("price_analysis", "주가 동향 분석 정보가 없습니다.")
        # ✅ 재무 상태 분석 섹션은 항상 이 문장을 fallback 으로 사용
        financial_analysis_text = _get_section("financial_analysis", FIN_PLACEHOLDER)
        valuation_text = _get_section("valuation", "밸류에이션 정보가 없습니다.")
        investment_opinion_text = _get_section("investment_opinion", "투자 의견 정보가 없습니다.")

        # ---------------- 종합 의견 / 목표주가 / Upside ----------------
        opinion, target_price = ReportFormatter._extract_opinion_and_target(investment_opinion_text)

        raw_basic = report_data.get("raw_data", {}).get("basic", {}) or {}
        current_price = raw_basic.get("current_price")
        upside = None
        if opinion or target_price:
            upside = ReportFormatter._calc_upside(current_price, target_price)

        # ---------------- 최종 텍스트 조립 ----------------
        lines: List[str] = []

        # 헤더
        lines.append(f"📊 {name} 투자 리포트")
        if generated_at:
            lines.append(f"생성일시: {generated_at}")
        lines.append(f"재무제표: {'있음' if has_financials else '없음'}")
        lines.append("")

        # 1. 투자 요약
        lines.append("### [1. 투자 요약]")
        lines.append(summary_text)
        lines.append("")

        # 2. 주가 동향 분석
        lines.append("### [2. 주가 동향 분석]")
        lines.append(price_analysis_text)
        lines.append("")

        # 3. 재무 상태 분석
        lines.append("### [3. 재무 상태 분석]")
        lines.append(financial_analysis_text)
        lines.append("")

        # 4. 밸류에이션
        lines.append("### [4. 밸류에이션]")
        lines.append(valuation_text)
        lines.append("")

        # 5. 투자 의견
        lines.append("### [5. 투자 의견]")
        lines.append(investment_opinion_text or "투자 의견 정보가 없습니다.")

        # 종합 의견 / 목표주가 강조 (가능한 경우)
        if opinion or target_price:
            lines.append("")
            if opinion:
                lines.append(f"**종합 투자 의견: {opinion}**")
            if target_price:
                lines.append(f"**목표주가: {target_price}**")
            if upside and upside != "N/A":
                lines.append(f"(현재가 대비 Upside: {upside})")

        full_text = "\n".join(lines).strip()

        # Kakao simpleText 글자 수 제한(약 1000자) 고려하여 잘라내기
        max_len = 980
        if len(full_text) > max_len:
            full_text = full_text[: max_len - 1] + "…"

        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": full_text}}
                ],
                "quickReplies": ReportFormatter._build_common_quick_replies(),
            },
        }

    # ------------------------------------------------------------------
    # 3) No data / Error 응답
    # ------------------------------------------------------------------
    @staticmethod
    def build_no_data_response(ticker: str) -> Dict[str, Any]:
        text = f"앗, 아직 '{ticker}'에 대한 리포트 데이터가 없어요 🥲 다른 종목 리포트를 보시겠어요?"

        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": text}}
                ],
                "quickReplies": [
                    {"label": "다른 종목 리포트", "action": "block", "blockId": "S02"},
                    {"label": "도움말", "action": "block", "blockId": "HELP"},
                ],
            },
        }

    @staticmethod
    def build_error_response() -> Dict[str, Any]:
        text = (
            "지금 리포트를 불러오는 중에 문제가 발생했어요 😢\n"
            "잠시 후 다시 시도하시거나, 다른 종목을 조회해볼까요?"
        )

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": text}}],
                "quickReplies": [
                    {"label": "다시 시도", "action": "block", "blockId": "S02"},
                    {"label": "다른 종목 리포트", "action": "block", "blockId": "S02"},
                    {"label": "도움말", "action": "block", "blockId": "HELP"},
                ],
            },
        }

    # ------------------------------------------------------------------
    # 4) ItemCard 생성 부분 (기존 카드형 UI 유지용)
    # ------------------------------------------------------------------
    @staticmethod
    def _build_summary_card(report_data: Dict[str, Any]) -> Dict[str, Any]:
        sections = report_data.get("report", {}).get("sections", {}) or {}
        raw = report_data.get("raw_data", {}) or {}
        price_trend = raw.get("price_trend", {}) or {}
        basic = raw.get("basic", {}) or {}

        summary_text = sections.get("summary", "") or "요약 정보가 없습니다."
        one_line = ReportFormatter._one_line_summary(summary_text)

        one_year = price_trend.get("1y")
        mcap_rank = basic.get("market_cap_rank")
        mcap = basic.get("market_cap")
        name = report_data.get("name") or basic.get("name") or ""

        def fmt_pct(v: Optional[float]) -> str:
            return f"{v:+.2f}%" if isinstance(v, (int, float)) else "N/A"

        def fmt_won(v: Optional[float]) -> str:
            return f"{v:,.0f}원" if isinstance(v, (int, float)) else "N/A"

        item_list = [
            {"title": "종목명", "description": name or "-"},
            {"title": "최근 1년 수익률", "description": fmt_pct(one_year)},
            {"title": "시가총액", "description": fmt_won(mcap)},
            {"title": "시총 순위", "description": f"{mcap_rank}위" if mcap_rank else "N/A"},
            {"title": "요약", "description": one_line or "요약 정보가 없습니다."},
        ]

        return {
            "imageTitle": {"title": "투자 요약", "description": "해당 종목에 대한 핵심 요약입니다."},
            "title": "",
            "description": f"LLM 한 문장 요약: {one_line}" if one_line else "핵심 요약을 확인해 보세요.",
            "itemList": item_list,
        }

    @staticmethod
    def _build_price_card(report_data: Dict[str, Any]) -> Dict[str, Any]:
        sections = report_data.get("report", {}).get("sections", {}) or {}
        raw = report_data.get("raw_data", {}) or {}
        price_trend = raw.get("price_trend", {}) or {}
        technical = raw.get("technical", {}) or {}

        desc_src = sections.get("price_analysis", "") or "주가 동향 분석 정보가 없습니다."
        one_line = ReportFormatter._one_line_summary(desc_src)

        def fmt_pct(v: Optional[float]) -> str:
            return f"{v:+.2f}%" if isinstance(v, (int, float)) else "N/A"

        item_list = [
            {"title": "1개월 수익률", "description": fmt_pct(price_trend.get("1m"))},
            {"title": "3개월 수익률", "description": fmt_pct(price_trend.get("3m"))},
            {"title": "1년 수익률", "description": fmt_pct(price_trend.get("1y"))},
            {"title": "52주 고점 대비", "description": fmt_pct(price_trend.get("from_high"))},
            {
                "title": "RSI",
                "description": f"{technical.get('rsi', 'N/A')} ({technical.get('rsi_signal', 'N/A')})",
            },
        ]

        return {
            "imageTitle": {"title": "주가 동향 분석", "description": "최근 주가 흐름과 기술적 지표를 분석합니다."},
            "title": "",
            "description": f"LLM 한 문장 요약: {one_line}" if one_line else "최근 주가 흐름을 요약했습니다.",
            "itemList": item_list,
        }

    @staticmethod
    def _build_financial_card(report_data: Dict[str, Any]) -> Dict[str, Any]:
        sections = report_data.get("report", {}).get("sections", {}) or {}
        desc_src = sections.get("financial_analysis", "") or "재무제표 요약 정보가 없습니다."
        one_line = ReportFormatter._one_line_summary(desc_src)

        # 실제 숫자 대신, 텍스트 요약 기반의 설명으로 구성
        item_list = [
            {"title": "매출", "description": "텍스트 요약 기반으로 매출 흐름을 설명합니다."},
            {"title": "영업이익", "description": "영업이익 추이와 수익성 변화를 요약합니다."},
            {"title": "순이익", "description": "당기순이익 및 이익 안정성을 요약합니다."},
            {"title": "현금흐름", "description": "영업/투자/재무 현금흐름 특징을 요약합니다."},
            {"title": "재무 안정성", "description": "부채비율·유동비율 등 재무 건전성을 설명합니다."},
        ]

        return {
            "imageTitle": {"title": "재무제표", "description": "기업 실적 기반 재무 흐름을 요약합니다."},
            "title": "",
            "description": f"LLM 한 문장 요약: {one_line}" if one_line else "재무 상태를 요약했습니다.",
            "itemList": item_list,
        }

    @staticmethod
    def _build_valuation_card(report_data: Dict[str, Any]) -> Dict[str, Any]:
        raw = report_data.get("raw_data", {}) or {}
        metrics = raw.get("metrics", {}) or {}

        def fmt(v: Any) -> str:
            return "N/A" if v is None else str(v)

        per = fmt(metrics.get("per"))
        pbr = fmt(metrics.get("pbr"))
        roe = fmt(metrics.get("roe"))
        eps = fmt(metrics.get("eps"))
        bps = fmt(metrics.get("bps"))

        desc = "PER·PBR·ROE 기준으로 현재 주가의 적정성을 평가합니다. 상세 수치는 아래 항목을 참고하세요."

        item_list = [
            {"title": "PER", "description": f"{per}배"},
            {"title": "PBR", "description": f"{pbr}배"},
            {"title": "ROE", "description": f"{roe}%"},
            {"title": "EPS/BPS", "description": f"EPS {eps} / BPS {bps}"},
            {"title": "참고", "description": "동일 업종/시장 대비 상대 밸류에이션을 함께 고려하세요."},
        ]

        return {
            "imageTitle": {"title": "밸류에이션", "description": "PER·PBR·ROE로 주가 적정성을 판단합니다."},
            "title": "",
            "description": f"LLM 한 문장 요약: {desc}",
            "itemList": item_list,
        }

    @staticmethod
    def _build_opinion_card(report_data: Dict[str, Any]) -> Dict[str, Any]:
        sections = report_data.get("report", {}).get("sections", {}) or {}
        opinion_text = sections.get("investment_opinion", "") or ""

        opinion, target_price = ReportFormatter._extract_opinion_and_target(opinion_text)
        raw = report_data.get("raw_data", {}) or {}
        basic = raw.get("basic", {}) or {}
        current_price = basic.get("current_price")

        upside_str = ReportFormatter._calc_upside(current_price, target_price)
        desc = ReportFormatter._one_line_summary(opinion_text) or "투자의견 정보가 없습니다."

        item_list = [
            {"title": "종합 의견", "description": opinion or "N/A"},
            {"title": "목표 주가", "description": target_price or "N/A"},
            {"title": "Upside", "description": upside_str},
            {"title": "투자 리스크", "description": "리포트 본문에서 제시한 주요 리스크를 확인하세요."},
            {"title": "모니터링 포인트", "description": "업황·실적·신사업 진행 상황을 지속적으로 체크하세요."},
        ]

        return {
            "imageTitle": {"title": "투자의견", "description": "LLM 기반 종합 투자 의견입니다."},
            "title": "",
            "description": f"LLM 한 문장 요약: {desc}",
            "itemList": item_list,
        }

    # ------------------------------------------------------------------
    # 5) 공통 Quick Replies
    # ------------------------------------------------------------------
    @staticmethod
    def _build_common_quick_replies() -> List[Dict[str, Any]]:
        return [
            {"label": "뉴스/커뮤니티 보기", "action": "block", "blockId": "S06"},
            {"label": "다른 종목 리포트", "action": "block", "blockId": "S02"},
            {"label": "관심종목 추가", "action": "block", "blockId": "S10"},
            {"label": "도움말", "action": "block", "blockId": "HELP"},
        ]

    # ------------------------------------------------------------------
    # 6) Helper Functions
    # ------------------------------------------------------------------
    @staticmethod
    def _one_line_summary(text: str, max_len: int = 80) -> str:
        if not text:
            return ""
        for sep in [". ", "。", "\n"]:
            if sep in text:
                text = text.split(sep)[0]
                break
        return text[: max_len] + ("..." if len(text) > max_len else "")

    @staticmethod
    def _extract_opinion_and_target(text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        투자 의견/목표주가를 investment_opinion 텍스트에서 최대한 추출.
        - '종합 투자 의견: 매수 (BUY)' 같은 패턴 우선
        - 그 외에는 매수/보유/매도/BUY/HOLD/SELL 키워드 기반 추론
        - '목표주가 115000원', '목표가: 115,000원' 등 패턴에서 숫자 추출
        """
        if not text:
            return None, None

        opinion: Optional[str] = None
        target: Optional[str] = None

        # 1) 명시적 "종합 투자 의견: ..." 패턴
        m = re.search(r"종합\s*투자\s*의견[:：]\s*([^\n]+)", text)
        if m:
            opinion = m.group(1).strip()

        # 2) 키워드 기반 추론 (opinion이 아직 없는 경우)
        if not opinion:
            if re.search(r"매수|buy", text, re.IGNORECASE):
                opinion = "매수 (BUY)"
            elif re.search(r"비중\s*확대|outperform|overweight", text, re.IGNORECASE):
                opinion = "비중확대"
            elif re.search(r"중립|hold", text, re.IGNORECASE):
                opinion = "중립 (HOLD)"
            elif re.search(r"매도|sell", text, re.IGNORECASE):
                opinion = "매도 (SELL)"

        # 3) 목표주가 / 목표가 숫자 추출
        m = re.search(r"(목표주가|목표가)[:：]?\s*([\d,]+)\s*원?", text)
        if m:
            num = m.group(2).replace(",", "")
            try:
                target = f"{int(num):,}원"
            except ValueError:
                target = f"{m.group(2)}원"
        else:
            # '목표주가 ~원' 처럼 떨어져 있을 수도 있음
            m = re.search(r"(목표주가|목표가)[^0-9]*([\d,]+)", text)
            if m:
                num = m.group(2).replace(",", "")
                try:
                    target = f"{int(num):,}원"
                except ValueError:
                    target = f"{m.group(2)}원"

        return opinion, target

    @staticmethod
    def _calc_upside(current_price: Optional[int], target_price_str: Optional[str]) -> str:
        """
        현재가와 목표주가 문자열(예: '115,000원')을 받아 Upside(%) 계산.
        """
        if not current_price or not target_price_str:
            return "N/A"

        try:
            target_num = int(
                target_price_str.replace(",", "").replace("원", "")
            )
        except Exception:
            return "N/A"

        if current_price <= 0:
            return "N/A"

        diff = (target_num - current_price) / current_price * 100.0
        sign = "+" if diff >= 0 else ""
        return f"{sign}{diff:.1f}%"
