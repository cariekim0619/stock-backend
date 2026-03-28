"""
Chatbot_02 종목 리포트 API
챗봇 기획에 맞춘 종목 리포트 생성 및 카카오톡 응답 포맷

기획:
- 리포트 요약 (1차) → 주제별 상세 (2차) 순서
- 5개 섹션: 투자 요약, 주가 동향, 재무 분석, 밸류에이션, 투자 의견
- 계좌 미연동 시에도 종목 검색 가능
- Agent React 적용 예정 (현재는 순차 호출 방식)
"""

import os
from typing import Dict, List, Optional
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


class ChatbotStockReport:
    """
    Chatbot_02 종목 리포트 데이터 프로바이더

    기능:
    - get_price_info(): 현재가·등락 단독 조회 (캐시 리포트 갱신용)
    - get_report_summary(): 종목 리포트 요약 (1차 결과)
    - get_section_detail(): 주제별 상세 조회
    - get_all_sections(): 전체 확인 (내부용)
    - format_entry_for_kakao(): 기능 진입 화면
    - format_no_account_for_kakao(): 계좌 미연동 안내
    - format_stock_list_for_kakao(): 관심/보유 종목 리스트 출력
    - format_empty_list_for_kakao(): 빈 목록 안내
    - format_stock_not_in_list_for_kakao(): 목록에 없는 종목 입력 안내
    - format_input_prompt_for_kakao(): 종목 직접 입력 유도
    - format_stock_not_found_for_kakao(): 종목 찾기 실패 안내
    - format_summary_for_kakao(): 리포트 요약 → 카카오톡 형식
    - format_topic_menu_for_kakao(): 주제 선택 메뉴
    - format_section_for_kakao(): 주제별 상세 → 카카오톡 형식
    """

    # 섹션 정의
    SECTIONS = {
        "investment_summary": "투자 요약",
        "price_trend": "주가 동향",
        "financial_analysis": "재무 분석",
        "valuation": "밸류에이션",
        "investment_opinion": "투자 의견",
    }

    def __init__(self):
        """Initialize"""
        # 기존 데이터 프로바이더
        from stock_chart_data import StockChartDataProvider
        self.chart_provider = StockChartDataProvider()

        # Gemini (LLM)
        self.gemini_key = os.environ.get("GEMINI_API_KEY")
        if self.gemini_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self.gemini_key)
                self.genai = genai
            except ImportError:
                self.genai = None
        else:
            self.genai = None


    # ========================================
    # 데이터 수집
    # ========================================

    def _collect_stock_data(self, symbol: str, company_name: str) -> Dict:
        """
        종목 관련 데이터 수집 (모든 섹션에서 공유)

        Returns:
            {
                "info": {...},         # 기본 정보 (현재가, 등락)
                "fundamental": {...},  # PER, PBR, ROE
                "technical": {...},    # RSI, 이동평균선
                "returns": {...},      # 기간별 수익률
            }
        """
        data = {}

        # 1. 기본 정보 (현재가, 등락)
        data["info"] = self.chart_provider.get_stock_info(symbol)

        # 2. 펀더멘탈 (PER, PBR, ROE)
        data["fundamental"] = self.chart_provider.get_fundamental_metrics(symbol)

        # 3. 기술적 지표 (RSI, 이동평균선)
        data["technical"] = self.chart_provider.get_technical_indicators(symbol)

        # 4. 기간별 수익률 계산
        data["returns"] = self._calculate_returns(symbol)

        # 5. DART 재무 지표 (부채비율, 현금흐름)
        data["dart"] = self.chart_provider.get_dart_metrics(symbol)

        return data

    def _calculate_returns(self, symbol: str) -> Dict:
        """기간별 수익률 계산 (chart_provider FDR 캐시 활용)"""
        try:
            df = self.chart_provider.get_historical_data(symbol)
            if df.empty:
                return {"error": "데이터 없음"}

            current = float(df['close'].iloc[-1])

            periods = {
                "1m": 21,   # 약 1개월 영업일
                "3m": 63,   # 약 3개월
                "1y": 252,  # 약 1년
            }

            returns = {}
            for key, days in periods.items():
                if len(df) > days:
                    past_price = float(df['close'].iloc[-days])
                    returns[key] = round((current - past_price) / past_price * 100, 1)
                else:
                    returns[key] = None

            return returns
        except Exception as e:
            return {"error": str(e)}

    # ========================================
    # LLM 텍스트 생성
    # ========================================

    def _parse_bullet_lines(self, text: str) -> List[str]:
        """LLM 응답에서 bullet 포인트 추출 (다양한 형식 대응)"""
        import re
        lines = text.strip().split('\n')
        bullets = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # •, *, -, ·, 숫자. 등으로 시작하는 줄
            cleaned = re.sub(r'^[\•\*\-\·\■\□\▪\▸]\s*', '', line)
            cleaned = re.sub(r'^\d+[\.\)]\s*', '', cleaned)
            # markdown bold 제거
            cleaned = re.sub(r'\*\*(.*?)\*\*', r'\1', cleaned)
            if cleaned != line or line.startswith(('•', '*', '-', '·')):
                bullets.append(cleaned.strip())
        return bullets

    def _parse_checkpoint(self, text: str, marker: str = "✔") -> str:
        """LLM 응답에서 체크포인트 줄 추출"""
        import re
        for line in text.strip().split('\n'):
            line = line.strip()
            if marker in line or line.startswith('✔'):
                cleaned = re.sub(r'^[✔️✔\s]+', '', line)
                cleaned = re.sub(r'^\[.*?\]\s*', '', cleaned)
                cleaned = re.sub(r'\*\*(.*?)\*\*', r'\1', cleaned)
                return cleaned.strip()
        return ""

    def _generate_llm_text(self, prompt: str) -> Optional[str]:
        """LLM 텍스트 생성 (공통)"""
        if not self.genai:
            return None

        try:
            model = self.genai.GenerativeModel(
                'gemini-2.5-flash',
                system_instruction="당신은 한국 주식시장 전문 애널리스트입니다. 초보 투자자가 이해할 수 있도록 쉽고 친근하게 설명합니다. 매수/매도 추천은 하지 않고 정보 제공만 합니다."
            )
            response = model.generate_content(
                prompt,
                generation_config={"temperature": 0.3, "max_output_tokens": 4096}
            )
            return response.text.strip()
        except Exception:
            return None

    def _generate_investment_summary_text(self, data: Dict, company_name: str) -> Dict:
        """투자 요약 텍스트 생성"""
        info = data.get("info", {})
        fundamental = data.get("fundamental", {})
        technical = data.get("technical", {})
        returns = data.get("returns", {})
        dart = data.get("dart", {})

        rsi_info = technical.get("rsi", {})

        # DART 데이터 라인 (있을 때만)
        dart_line = ""
        if "error" not in dart:
            parts = []
            if dart.get("debt_ratio") is not None:
                parts.append(f"부채비율: {dart['debt_ratio']:.1f}%")
            if dart.get("operating_cf") is not None:
                cf_sign = "+" if dart["operating_cf"] > 0 else ""
                parts.append(f"영업현금흐름: {cf_sign}{dart['operating_cf']:,.0f}원")
            if parts:
                dart_line = "\n" + " / ".join(parts)

        prompt = f"""다음은 {company_name}의 투자 데이터입니다.

현재가: {info.get('current_price', 'N/A')}원 ({info.get('price_change', 0):+}원)
PER: {fundamental.get('per', 'N/A')} / PBR: {fundamental.get('pbr', 'N/A')} / ROE: {fundamental.get('roe', 'N/A')}%
RSI: {rsi_info.get('value', 'N/A')} ({rsi_info.get('signal', {}).get('description', 'N/A')})
1개월 수익률: {returns.get('1m', 'N/A')}% / 3개월: {returns.get('3m', 'N/A')}% / 1년: {returns.get('1y', 'N/A')}%{dart_line}

조건:
- 핵심 포인트 3개를 bullet으로 작성 (각 30자 이내)
- "~에요", "~있어요" 친근한 말투
- 마지막에 "주요 체크 포인트" 1줄 추가
- 매수/매도 추천 금지
- 위에 제공된 데이터만 언급하세요. 데이터에 없는 지표는 언급하지 마세요.

형식:
• [포인트1]
• [포인트2]
• [포인트3]
✔️ [체크포인트]"""

        result = self._generate_llm_text(prompt)

        if result:
            points = self._parse_bullet_lines(result)
            checkpoint = self._parse_checkpoint(result)
            if points:
                return {"points": points[:3], "checkpoint": checkpoint}

        # fallback
        return {
            "points": [
                "최근 시장에서 관심을 받고 있는 종목이에요",
                "주요 지표를 확인하고 투자 판단에 참고해보세요",
                "자세한 내용은 각 섹션에서 확인할 수 있어요"
            ],
            "checkpoint": "주요 체크 포인트는 실적 흐름과 시장 환경 변화예요."
        }

    def _generate_financial_text(self, data: Dict, company_name: str) -> Dict:
        """재무 분석 텍스트 생성"""
        fundamental = data.get("fundamental", {})
        dart = data.get("dart", {})

        # DART 재무 데이터 섹션
        dart_section = ""
        if "error" not in dart:
            dart_lines = []
            if dart.get("debt_ratio") is not None:
                dart_lines.append(f"부채비율: {dart['debt_ratio']:.1f}%")
            if dart.get("revenue") is not None:
                dart_lines.append(f"매출액: {dart['revenue']:,.0f}원")
            if dart.get("operating_margin") is not None:
                dart_lines.append(f"영업이익률: {dart['operating_margin']:.1f}%")
            if dart.get("operating_cf") is not None:
                dart_lines.append(f"영업활동 현금흐름: {dart['operating_cf']:,.0f}원")
            if dart.get("investing_cf") is not None:
                dart_lines.append(f"투자활동 현금흐름: {dart['investing_cf']:,.0f}원")
            if dart.get("financing_cf") is not None:
                dart_lines.append(f"재무활동 현금흐름: {dart['financing_cf']:,.0f}원")
            if dart_lines:
                dart_section = "\n" + "\n".join(dart_lines)
                dart_section += f"\n(출처: {dart.get('report_label', 'DART')})"

        prompt = f"""다음은 {company_name}의 재무 데이터입니다.

PER: {fundamental.get('per', 'N/A')}
PBR: {fundamental.get('pbr', 'N/A')}
EPS: {fundamental.get('eps', 'N/A')}원
BPS: {fundamental.get('bps', 'N/A')}원
ROE: {fundamental.get('roe', 'N/A')}%{dart_section}

조건:
- 핵심 포인트 3개를 bullet으로 작성 (각 30자 이내)
- "~에요", "~있어요" 친근한 말투
- 마지막에 재무 안정성 체크포인트 1줄
- 매수/매도 추천 금지
- 위에 제공된 데이터만 언급하세요. 데이터에 없는 지표는 언급하지 마세요.

형식:
• [포인트1]
• [포인트2]
• [포인트3]
✔️ [체크포인트]"""

        result = self._generate_llm_text(prompt)

        if result:
            points = self._parse_bullet_lines(result)
            checkpoint = self._parse_checkpoint(result)
            if points:
                return {"points": points[:3], "checkpoint": checkpoint}

        return {
            "points": [
                "재무 데이터를 기반으로 분석 중이에요",
                "주요 재무 지표를 확인해보세요",
                "자세한 재무 분석은 웹에서 확인할 수 있어요"
            ],
            "checkpoint": "재무 안정성 측면의 상세 분석은 웹 리포트를 참고해주세요."
        }

    def _generate_valuation_text(self, data: Dict, company_name: str) -> str:
        """밸류에이션 해석 텍스트 생성"""
        fundamental = data.get("fundamental", {})

        prompt = f"""{company_name}의 밸류에이션 지표입니다.
PER: {fundamental.get('per', 'N/A')} / PBR: {fundamental.get('pbr', 'N/A')} / ROE: {fundamental.get('roe', 'N/A')}%

이 지표들을 종합해서 2줄 이내로 해석해주세요.
- "~에요", "~있어요" 친근한 말투
- 업종 평균 대비 수준 언급
- 매수/매도 추천 금지
- 인사말이나 서두 없이 바로 해석만 작성
- 위에 제공된 데이터만 언급하세요. 데이터에 없는 지표는 언급하지 마세요."""

        result = self._generate_llm_text(prompt)
        if result:
            import re
            cleaned = re.sub(r'\*\*(.*?)\*\*', r'\1', result)
            # 여러 줄이면 2줄까지만
            lines = [l.strip() for l in cleaned.split('\n') if l.strip()]
            return "\n".join(lines[:2])

        per = fundamental.get('per', 0)
        if per and per > 0:
            if per > 25:
                return "현재 주가는 다소 높은 수준으로 평가되고 있어요."
            elif per < 10:
                return "현재 주가는 상대적으로 저평가 구간으로 보여요."
            else:
                return "현재 주가는 업종 평균 수준으로 해석돼요."

        return "밸류에이션 데이터를 확인 중이에요."

    def _generate_opinion_text(self, data: Dict, company_name: str) -> Dict:
        """투자 의견 텍스트 생성"""
        info = data.get("info", {})
        fundamental = data.get("fundamental", {})
        technical = data.get("technical", {})
        returns = data.get("returns", {})

        rsi_info = technical.get("rsi", {})
        trend = technical.get("trend", {})

        prompt = f"""{company_name} 종합 투자 데이터:
현재가: {info.get('current_price', 'N/A')}원
PER: {fundamental.get('per', 'N/A')} / ROE: {fundamental.get('roe', 'N/A')}%
RSI: {rsi_info.get('value', 'N/A')} / 추세: {trend.get('description', 'N/A')}
1년 수익률: {returns.get('1y', 'N/A')}%

조건:
- 종합 투자 의견 bullet 3개 (각 25자 이내)
- "~에요", "~있어요" 친근한 말투
- 마지막에 리스크 관련 주의사항 1줄
- 매수/매도 추천 금지, 정보 제공만
- 위에 제공된 데이터만 언급하세요. 데이터에 없는 지표는 언급하지 마세요.

형식:
• [의견1]
• [의견2]
• [의견3]
👉 [리스크 주의사항]"""

        result = self._generate_llm_text(prompt)

        if result:
            points = self._parse_bullet_lines(result)
            risk_note = self._parse_checkpoint(result, marker="👉")
            if points:
                return {"points": points[:3], "risk_note": risk_note}

        return {
            "points": [
                "주요 지표를 종합적으로 검토해보세요",
                "시장 환경과 함께 판단하는 것이 좋아요",
                "분할 접근 전략을 고려해볼 수 있어요"
            ],
            "risk_note": "시장 변동성에 따라 리스크 관리는 필요해요."
        }

    # ========================================
    # 현재가 단독 조회 (캐시 리포트 갱신용)
    # ========================================

    def get_price_info(self, symbol: str) -> Dict:
        """
        현재가·등락 단독 조회

        LLM 호출 없이 가격 데이터만 빠르게 반환.
        백엔드에서 캐시된 리포트를 전달할 때 가격 필드만 갱신하는 용도.

        Returns:
            {"current_price": int, "price_change": int, "change_rate": float, "source": str}
        """
        info = self.chart_provider.get_stock_info(symbol)
        return {
            "current_price": info.get("current_price", 0),
            "price_change":  info.get("price_change", 0),
            "change_rate":   info.get("change_rate", 0),
            "source":        info.get("source", ""),
        }

    # ========================================
    # 리포트 요약 (1차 결과)
    # ========================================

    def get_report_summary(self, symbol: str, company_name: str) -> Dict:
        """
        종목 리포트 요약 (1차 결과)

        Args:
            symbol: 종목코드 (예: "005930")
            company_name: 회사명 (예: "삼성전자")

        Returns:
            {
                "symbol": "005930",
                "company_name": "삼성전자",
                "current_price": 128500,
                "price_change": 1500,
                "price_change_pct": 1.18,
                "return_1y": 71.7,
                "key_metrics": {"per": 16.6, "pbr": 1.42, "roe": 8.6},
                "rsi_signal": "중립",
                "investment_summary": "요약 텍스트...",
                "web_url": "https://...",
                "generated_at": "2026-02-06 10:30:00"
            }
        """
        # 데이터 수집
        data = self._collect_stock_data(symbol, company_name)

        info = data.get("info", {})
        fundamental = data.get("fundamental", {})
        technical = data.get("technical", {})
        returns = data.get("returns", {})

        if "error" in info:
            return self._error_response(info["error"])

        # RSI 신호
        rsi_info = technical.get("rsi", {})
        rsi_signal_data = rsi_info.get("signal", {})
        if isinstance(rsi_signal_data, dict):
            rsi_signal = rsi_signal_data.get("description", "데이터 없음")
        else:
            rsi_signal = str(rsi_signal_data)

        # 투자 요약 텍스트 생성
        summary_content = self._generate_investment_summary_text(data, company_name)
        summary_text = "\n".join([f"• {p}" for p in summary_content.get("points", [])])
        if summary_content.get("checkpoint"):
            summary_text += f"\n\n✔️ {summary_content['checkpoint']}"

        return {
            "symbol": symbol,
            "company_name": company_name,
            "current_price": info.get("current_price", 0),
            "price_change": info.get("price_change", 0),
            "price_change_pct": info.get("change_rate", 0),
            "return_1y": returns.get("1y"),
            "key_metrics": {
                "per": fundamental.get("per", 0),
                "pbr": fundamental.get("pbr", 0),
                "roe": fundamental.get("roe", 0),
                **({
                    "debt_ratio": data.get("dart", {}).get("debt_ratio"),
                    "operating_cf": data.get("dart", {}).get("operating_cf"),
                } if "error" not in data.get("dart", {"error": True}) else {}),
            },
            "rsi_signal": rsi_signal,
            "investment_summary": summary_text,
            "web_url": f"https://jutopia.com/stock/{symbol}/report",
            "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            # 내부 캐시용 (주제별 상세에서 재사용)
            "_raw_data": data,
        }

    # ========================================
    # 주제별 상세 조회
    # ========================================

    def get_section_detail(
        self, symbol: str, company_name: str, section: str, raw_data: Optional[Dict] = None
    ) -> Dict:
        """
        주제별 상세 조회

        Args:
            symbol: 종목코드
            company_name: 회사명
            section: 섹션 키 (investment_summary, price_trend, financial_analysis, valuation, investment_opinion)
            raw_data: 이전 수집 데이터 (캐시 재활용)

        Returns:
            섹션별 상세 데이터
        """
        if section not in self.SECTIONS:
            return self._error_response(f"잘못된 섹션: {section}")

        # 데이터 수집 (캐시 없으면 새로 수집)
        data = raw_data or self._collect_stock_data(symbol, company_name)

        section_builders = {
            "investment_summary": self._build_investment_summary,
            "price_trend": self._build_price_trend,
            "financial_analysis": self._build_financial_analysis,
            "valuation": self._build_valuation,
            "investment_opinion": self._build_investment_opinion,
        }

        builder = section_builders[section]
        content = builder(data, company_name)

        return {
            "section": section,
            "section_name": self.SECTIONS[section],
            "symbol": symbol,
            "company_name": company_name,
            "content": content,
            "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

    def _build_investment_summary(self, data: Dict, company_name: str) -> Dict:
        """투자 요약 섹션 빌드"""
        return self._generate_investment_summary_text(data, company_name)

    def _build_price_trend(self, data: Dict, company_name: str) -> Dict:
        """주가 동향 섹션 빌드"""
        returns = data.get("returns", {})
        technical = data.get("technical", {})
        rsi_info = technical.get("rsi", {})
        rsi_signal = rsi_info.get("signal", {})

        # RSI 해석 텍스트
        if isinstance(rsi_signal, dict):
            rsi_desc = rsi_signal.get("description", "데이터 없음")
        else:
            rsi_desc = str(rsi_signal)

        rsi_value = rsi_info.get("value")
        if rsi_value and rsi_value >= 70:
            interpretation = "현재 과매수 구간에 위치해 있어 조정 가능성이 있어요."
        elif rsi_value and rsi_value <= 30:
            interpretation = "현재 과매도 구간에 위치해 있어 반등 가능성이 있어요."
        else:
            interpretation = "현재 과열도 침체도 아닌 중립 구간에 위치해 있어요."

        return {
            "returns": {
                "1m": returns.get("1m"),
                "3m": returns.get("3m"),
                "1y": returns.get("1y"),
            },
            "rsi": {
                "value": rsi_value,
                "signal": rsi_desc,
                "interpretation": interpretation,
            }
        }

    def _build_financial_analysis(self, data: Dict, company_name: str) -> Dict:
        """재무 분석 섹션 빌드"""
        return self._generate_financial_text(data, company_name)

    def _build_valuation(self, data: Dict, company_name: str) -> Dict:
        """밸류에이션 섹션 빌드"""
        fundamental = data.get("fundamental", {})
        interpretation = self._generate_valuation_text(data, company_name)

        return {
            "metrics": {
                "per": fundamental.get("per", 0),
                "pbr": fundamental.get("pbr", 0),
                "roe": fundamental.get("roe", 0),
            },
            "interpretation": interpretation,
        }

    def _build_investment_opinion(self, data: Dict, company_name: str) -> Dict:
        """투자 의견 섹션 빌드"""
        return self._generate_opinion_text(data, company_name)

    # ========================================
    # 전체 확인하기
    # ========================================

    def get_all_sections(self, symbol: str, company_name: str) -> Dict:
        """
        전체 확인하기 (1~5번 각 2~3줄 압축)

        Returns:
            {
                "symbol": "005930",
                "company_name": "삼성전자",
                "sections": {
                    "investment_summary": "압축 텍스트",
                    "price_trend": "압축 텍스트",
                    ...
                },
                "generated_at": "..."
            }
        """
        data = self._collect_stock_data(symbol, company_name)
        info = data.get("info", {})
        fundamental = data.get("fundamental", {})
        returns = data.get("returns", {})
        technical = data.get("technical", {})
        rsi_info = technical.get("rsi", {})

        # 각 섹션을 2~3줄로 압축
        sections = {}

        # 투자 요약
        summary = self._generate_investment_summary_text(data, company_name)
        points = summary.get("points", [])
        sections["investment_summary"] = ", ".join(points[:2]) if points else "데이터 준비 중"

        # 주가 동향
        r1m = returns.get("1m", "N/A")
        r3m = returns.get("3m", "N/A")
        r1y = returns.get("1y", "N/A")
        rsi_val = rsi_info.get("value", "N/A")
        rsi_sig = rsi_info.get("signal", {})
        rsi_desc = rsi_sig.get("description", "N/A") if isinstance(rsi_sig, dict) else str(rsi_sig)
        sections["price_trend"] = f"1개월 {r1m}% / 3개월 {r3m}% / 1년 {r1y}%, RSI {rsi_desc}"

        # 재무 분석
        fin = self._generate_financial_text(data, company_name)
        fin_points = fin.get("points", [])
        sections["financial_analysis"] = ", ".join(fin_points[:2]) if fin_points else "데이터 준비 중"

        # 밸류에이션
        per = fundamental.get("per", "N/A")
        pbr = fundamental.get("pbr", "N/A")
        roe = fundamental.get("roe", "N/A")
        val_text = self._generate_valuation_text(data, company_name)
        sections["valuation"] = f"PER {per} / PBR {pbr} / ROE {roe}%, {val_text[:30]}"

        # 투자 의견
        opinion = self._generate_opinion_text(data, company_name)
        op_points = opinion.get("points", [])
        sections["investment_opinion"] = ", ".join(op_points[:2]) if op_points else "데이터 준비 중"

        return {
            "symbol": symbol,
            "company_name": company_name,
            "sections": sections,
            "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

    # ========================================
    # 카카오톡 포맷 — 플로우 화면
    # ========================================

    def format_entry_for_kakao(self) -> Dict:
        """
        기능 진입 화면을 카카오톡 형식으로 변환

        기획:
        - 종목 리포트 기능 안내 메시지
        - 퀵 버튼: 관심 종목 확인하기 / 보유 종목 확인하기 / 종목 직접 입력 / 종목 리포트 종료
        """
        message = """⬛️ 종목 리포트 기능에 대해 알려드릴게요

➊ 관심 있는 종목의 투자 리포트를 확인할 수 있어요
➋ 종목명을 직접 입력해서 바로 리포트를 볼 수도 있어요
➌ 보유 종목과 관심 종목은 계좌가 연동되어 있을 때 확인할 수 있어요

아래 버튼을 눌러 원하는 방법을 선택해 주세요!"""

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": message}}],
                "quickReplies": [
                    {"action": "block", "label": "관심 종목 확인하기", "messageText": "관심 종목", "blockId": "favorite_list_block"},
                    {"action": "block", "label": "보유 종목 확인하기", "messageText": "보유 종목", "blockId": "holding_list_block"},
                    {"action": "block", "label": "종목 직접 입력", "messageText": "종목 직접 입력", "blockId": "stock_input_block"},
                    {"action": "block", "label": "종목 리포트 종료", "messageText": "메인으로", "blockId": "main_block"},
                ],
            },
        }

    def format_no_account_for_kakao(self) -> Dict:
        """
        계좌 미연동 안내를 카카오톡 형식으로 변환

        기획:
        - 계좌 연결 안내 메시지
        - 퀵 버튼: 계좌 연결하기 / 종목 직접 입력 / 이전으로
        """
        message = """해당 기능은 계좌 연결 후 이용할 수 있습니다 :)

계좌를 연결하시면,
보유 종목과 관심 종목의 리포트를 바로 확인할 수 있어요!

💡 계좌를 연결하지 않아도
종목명을 직접 입력하면 리포트를 확인할 수 있어요 :D"""

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": message}}],
                "quickReplies": [
                    {"action": "block", "label": "계좌 연결하기", "messageText": "계좌 연결", "blockId": "account_link_block"},
                    {"action": "block", "label": "종목 직접 입력", "messageText": "종목 직접 입력", "blockId": "stock_input_block"},
                    {"action": "block", "label": "이전으로", "messageText": "이전", "blockId": "report_entry_block"},
                ],
            },
        }

    def format_stock_list_for_kakao(self, stocks: List[str], list_type: str = "favorite") -> Dict:
        """
        관심/보유 종목 리스트를 카카오톡 형식으로 변환

        Args:
            stocks: 종목명 리스트 (예: ["삼성전자", "SK하이닉스"])
            list_type: "favorite"(관심 종목) 또는 "holding"(보유 종목)

        기획:
        - 종목 리스트 출력 후 종목명 입력 대기
        """
        list_label = "관심" if list_type == "favorite" else "보유"
        stock_lines = "\n".join([f"- {s}" for s in stocks])

        message = f"""(사용자 이름)님의 {list_label} 종목은 다음과 같아요.

{stock_lines}

어떤 종목의 리포트를 확인할까요?
종목명을 입력해 주세요!"""

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": message}}],
            },
        }

    def format_empty_list_for_kakao(self, list_type: str = "favorite") -> Dict:
        """
        빈 종목 목록 안내를 카카오톡 형식으로 변환

        Args:
            list_type: "favorite"(관심 종목) 또는 "holding"(보유 종목)

        기획:
        - 목록이 비어 있을 때 직접 입력 유도
        - 퀵 버튼: 종목 직접 입력 / 이전으로
        """
        if list_type == "favorite":
            message = "등록된 관심 종목이 없어요.\n\n종목명을 직접 입력하거나 이전 화면으로 돌아가세요."
        else:
            message = "보유 중인 종목이 없어요.\n\n종목명을 직접 입력하거나 이전 화면으로 돌아가세요."

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": message}}],
                "quickReplies": [
                    {"action": "block", "label": "종목 직접 입력", "messageText": "종목 직접 입력", "blockId": "stock_input_block"},
                    {"action": "block", "label": "이전으로", "messageText": "이전", "blockId": "report_entry_block"},
                ],
            },
        }

    def format_stock_not_in_list_for_kakao(self) -> Dict:
        """
        목록에 없는 종목 입력 안내를 카카오톡 형식으로 변환

        기획:
        - 관심/보유 목록 표시 후 목록에 없는 종목명 입력 시
        - 퀵 버튼: 다시 입력 / 종목 직접 입력 / 이전으로
        """
        message = """입력하신 종목은 목록에 없어요.

종목명을 다시 입력하거나,
종목을 직접 검색해 주세요."""

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": message}}],
                "quickReplies": [
                    {"action": "message", "label": "다시 입력", "messageText": "다시 입력"},
                    {"action": "block", "label": "종목 직접 입력", "messageText": "종목 직접 입력", "blockId": "stock_input_block"},
                    {"action": "block", "label": "이전으로", "messageText": "이전", "blockId": "report_entry_block"},
                ],
            },
        }

    def format_input_prompt_for_kakao(self) -> Dict:
        """
        종목 직접 입력 유도 화면을 카카오톡 형식으로 변환

        기획:
        - 종목명 또는 종목 코드 입력 유도 메시지
        """
        message = """종목명을 직접 입력해 주세요.

종목명 또는 종목 코드를 입력하시면
리포트를 바로 확인하실 수 있어요!"""

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": message}}],
            },
        }

    def format_stock_not_found_for_kakao(self) -> Dict:
        """
        종목 찾기 실패 안내를 카카오톡 형식으로 변환

        기획:
        - 종목명 또는 종목 코드로 매칭 실패 시
        - 퀵 버튼: 다시 입력 / 이전으로
        """
        message = """입력하신 종목을 찾지 못했어요.
종목명을 다시 입력해 주세요!"""

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": message}}],
                "quickReplies": [
                    {"action": "message", "label": "다시 입력", "messageText": "다시 입력"},
                    {"action": "block", "label": "이전으로", "messageText": "이전", "blockId": "report_entry_block"},
                ],
            },
        }

    # ========================================
    # 카카오톡 포맷 — 리포트
    # ========================================

    def format_summary_for_kakao(self, summary: Dict) -> Dict:
        """
        리포트 요약을 카카오톡 API 2.0 형식으로 변환

        기획:
        - 요약 메시지 (현재가 + 핵심 지표 + 투자 요약)
        - 버튼 말풍선 (웹 이동)
        - 퀵 버튼: 주제별 요약 / 다른 종목 / 종료
        """
        if "error" in summary:
            return self._kakao_error_response(summary["error"])

        company = summary.get("company_name", "종목")
        symbol = summary.get("symbol", "")
        price = summary.get("current_price", 0)
        change = summary.get("price_change", 0)
        return_1y = summary.get("return_1y")
        metrics = summary.get("key_metrics", {})
        rsi = summary.get("rsi_signal", "")
        invest_summary = summary.get("investment_summary", "")

        # 가격 포맷
        price_str = f"{price:,.0f}" if price else "N/A"
        if change != 0:
            change_display = f"({change:+,.0f}원)"
        else:
            change_display = "(변동없음)"
        return_str = f"{return_1y:+.1f}%" if return_1y is not None else "N/A"

        message = f"""⬛️ {company}({symbol}) 종목 리포트 요약이에요!

• 현재가 : {price_str}원 {change_display}
• 1년 수익률 : {return_str}
• 주요 지표 : PER {metrics.get('per', 'N/A')} / PBR {metrics.get('pbr', 'N/A')} / ROE {metrics.get('roe', 'N/A')}
• 기술적 지표(RSI) : {rsi}

⬛️ 투자 요약
{invest_summary}"""

        web_url = summary.get("web_url", "https://jutopia.com")

        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": message}},
                    {
                        "basicCard": {
                            "title": "상세 리포트",
                            "description": "웹에서 더 자세한 리포트를 확인해보세요",
                            "buttons": [
                                {
                                    "action": "webLink",
                                    "label": "웹에서 상세 리포트 보기",
                                    "webLinkUrl": web_url,
                                },
                                {
                                    "action": "block",
                                    "label": "관심 종목 등록",
                                    "messageText": "관심 종목 등록",
                                    "blockId": "add_favorite_block",
                                },
                            ],
                        }
                    },
                ],
                "quickReplies": [
                    {
                        "action": "block",
                        "label": "주제별 요약 보기",
                        "messageText": "주제별 요약",
                        "blockId": "topic_menu_block",
                    },
                    {
                        "action": "block",
                        "label": "다른 종목 리포트 보기",
                        "messageText": "다른 종목",
                        "blockId": "select_stock_block",
                    },
                    {
                        "action": "block",
                        "label": "종목 리포트 종료",
                        "messageText": "메인으로",
                        "blockId": "main_block",
                    },
                ],
            },
        }

    def format_topic_menu_for_kakao(self) -> Dict:
        """
        주제 선택 메뉴를 카카오톡 형식으로 변환

        기획:
        - 5개 주제 안내 메시지
        - 퀵 버튼: 5개 주제 + 이전으로
        """
        message = """종목 리포트는
아래 5개의 주제로 나누어 확인할 수 있어요.

➊ 투자 요약
➋ 주가 동향
➌ 재무 분석
➍ 밸류에이션
➎ 투자 의견

하단의 버튼을 눌러 궁금한 내용을 확인해 주세요!"""

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": message}}],
                "quickReplies": [
                    {"action": "block", "label": "투자 요약", "messageText": "투자 요약", "blockId": "section_detail_block"},
                    {"action": "block", "label": "주가 동향", "messageText": "주가 동향", "blockId": "section_detail_block"},
                    {"action": "block", "label": "재무 분석", "messageText": "재무 분석", "blockId": "section_detail_block"},
                    {"action": "block", "label": "밸류에이션", "messageText": "밸류에이션", "blockId": "section_detail_block"},
                    {"action": "block", "label": "투자 의견", "messageText": "투자 의견", "blockId": "section_detail_block"},
                    {"action": "block", "label": "관심 종목 등록", "messageText": "관심 종목 등록", "blockId": "add_favorite_block"},
                    {"action": "block", "label": "이전으로", "messageText": "이전", "blockId": "report_summary_block"},
                ],
            },
        }

    def format_section_for_kakao(self, detail: Dict) -> Dict:
        """
        주제별 상세를 카카오톡 형식으로 변환

        기획:
        - 주제 상세 메시지
        - 퀵 버튼: 5개 주제 + 전체 확인 + 다른 종목 + 종료
        """
        if "error" in detail:
            return self._kakao_error_response(detail["error"])

        section = detail.get("section", "")
        section_name = detail.get("section_name", "")
        content = detail.get("content", {})

        # 섹션별 메시지 생성
        message = self._format_section_message(section, section_name, content)

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": message}}],
                "quickReplies": [
                    {"action": "block", "label": "주제별 요약 보기", "messageText": "주제별 요약", "blockId": "topic_menu_block"},
                    {"action": "block", "label": "관심 종목 등록", "messageText": "관심 종목 등록", "blockId": "add_favorite_block"},
                    {"action": "block", "label": "다른 종목 보기", "messageText": "다른 종목", "blockId": "select_stock_block"},
                    {"action": "block", "label": "종목 리포트 종료", "messageText": "메인으로", "blockId": "main_block"},
                ],
            },
        }

    def _format_section_message(self, section: str, section_name: str, content: Dict) -> str:
        """섹션별 카카오톡 메시지 생성"""
        if section == "investment_summary":
            points = content.get("points", [])
            checkpoint = content.get("checkpoint", "")
            points_text = "\n".join([f"• {p}" for p in points])
            msg = f"⬛️ {section_name}이에요.\n\n{points_text}"
            if checkpoint:
                msg += f"\n\n✔️ {checkpoint}"
            return msg

        elif section == "price_trend":
            returns = content.get("returns", {})
            rsi = content.get("rsi", {})
            r1m = returns.get("1m", "N/A")
            r3m = returns.get("3m", "N/A")
            r1y = returns.get("1y", "N/A")
            interpretation = rsi.get("interpretation", "")
            return f"""⬛️ 주가 동향을 살펴볼게요.

• 1개월 수익률: {r1m}%
• 3개월 수익률: {r3m}%
• 1년 수익률: {r1y}%

기술적 지표(RSI) 기준으로는
{interpretation}"""

        elif section == "financial_analysis":
            points = content.get("points", [])
            checkpoint = content.get("checkpoint", "")
            points_text = "\n".join([f"• {p}" for p in points])
            msg = f"⬛️ {section_name}이에요.\n\n{points_text}"
            if checkpoint:
                msg += f"\n\n✔️ {checkpoint}"
            return msg

        elif section == "valuation":
            metrics = content.get("metrics", {})
            interpretation = content.get("interpretation", "")
            return f"""⬛️ 밸류에이션 관점에서 보면,

• PER: {metrics.get('per', 'N/A')}
• PBR: {metrics.get('pbr', 'N/A')}
• ROE: {metrics.get('roe', 'N/A')}%

{interpretation}"""

        elif section == "investment_opinion":
            points = content.get("points", [])
            risk_note = content.get("risk_note", "")
            points_text = "\n".join([f"• {p}" for p in points])
            msg = f"⬛️ 종합 투자 의견이에요.\n\n{points_text}"
            if risk_note:
                msg += f"\n\n👉 {risk_note}"
            return msg

        return f"⬛️ {section_name} 데이터를 준비 중이에요."

    def format_all_sections_for_kakao(self, all_sections: Dict) -> Dict:
        """전체 확인하기를 카카오톡 형식으로 변환"""
        if "error" in all_sections:
            return self._kakao_error_response(all_sections["error"])

        company = all_sections.get("company_name", "종목")
        sections = all_sections.get("sections", {})

        lines = [f"⬛️ {company} 종목 리포트 전체 요약이에요.\n"]

        section_labels = {
            "investment_summary": "➊ 투자 요약",
            "price_trend": "➋ 주가 동향",
            "financial_analysis": "➌ 재무 분석",
            "valuation": "➍ 밸류에이션",
            "investment_opinion": "➎ 투자 의견",
        }

        for key, label in section_labels.items():
            text = sections.get(key, "데이터 준비 중")
            lines.append(f"{label}\n{text}\n")

        message = "\n".join(lines)

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": message}}],
                "quickReplies": [
                    {"action": "block", "label": "투자 요약", "messageText": "투자 요약", "blockId": "section_detail_block"},
                    {"action": "block", "label": "주가 동향", "messageText": "주가 동향", "blockId": "section_detail_block"},
                    {"action": "block", "label": "재무 분석", "messageText": "재무 분석", "blockId": "section_detail_block"},
                    {"action": "block", "label": "밸류에이션", "messageText": "밸류에이션", "blockId": "section_detail_block"},
                    {"action": "block", "label": "투자 의견", "messageText": "투자 의견", "blockId": "section_detail_block"},
                    {"action": "block", "label": "다른 종목 보기", "messageText": "다른 종목", "blockId": "select_stock_block"},
                    {"action": "block", "label": "종목 리포트 종료", "messageText": "메인으로", "blockId": "main_block"},
                ],
            },
        }

    # ========================================
    # 에러 처리
    # ========================================

    def _error_response(self, reason: str) -> Dict:
        """에러 응답"""
        return {
            "error": reason,
            "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

    def _kakao_error_response(self, reason: str) -> Dict:
        """카카오톡 에러 응답"""
        return {
            "version": "2.0",
            "template": {
                "outputs": [{
                    "simpleText": {
                        "text": f"❌ {reason}\n잠시 후 다시 시도해주세요."
                    }
                }]
            },
        }


# ========================================
# 테스트
# ========================================

if __name__ == "__main__":
    import json

    print("=" * 60)
    print("Chatbot_02 종목 리포트 테스트")
    print("=" * 60)
    print()

    chatbot = ChatbotStockReport()

    # 테스트: 삼성전자
    symbol = "005930"
    company = "삼성전자"

    # 1. 리포트 요약
    print("[1단계] 리포트 요약")
    print("-" * 40)
    summary = chatbot.get_report_summary(symbol, company)
    if "error" not in summary:
        print(f"현재가: {summary.get('current_price'):,}원 ({summary.get('price_change'):+,}원)")
        print(f"1년 수익률: {summary.get('return_1y')}%")
        print(f"주요 지표: PER {summary['key_metrics']['per']} / PBR {summary['key_metrics']['pbr']} / ROE {summary['key_metrics']['roe']}")
        print(f"RSI: {summary.get('rsi_signal')}")
        print(f"\n투자 요약:\n{summary.get('investment_summary')}")
    else:
        print(f"에러: {summary['error']}")
    print()

    # 2. 주제별 상세
    raw_data = summary.get("_raw_data")
    print("[2단계] 주제별 상세")
    print("-" * 40)
    for section_key, section_name in ChatbotStockReport.SECTIONS.items():
        detail = chatbot.get_section_detail(symbol, company, section_key, raw_data)
        print(f"\n[{section_name}]")
        print(json.dumps(detail.get("content", {}), ensure_ascii=False, indent=2)[:300])
    print()

    # 3. 전체 확인하기
    print("[3단계] 전체 확인하기")
    print("-" * 40)
    all_sections = chatbot.get_all_sections(symbol, company)
    for key, text in all_sections.get("sections", {}).items():
        print(f"  {ChatbotStockReport.SECTIONS[key]}: {text[:50]}")
    print()

    # 4. 카카오톡 형식
    print("[4단계] 카카오톡 응답")
    print("-" * 40)
    kakao_summary = chatbot.format_summary_for_kakao(summary)
    print(json.dumps(kakao_summary, ensure_ascii=False, indent=2)[:800] + "...")
    print()

    print("=" * 60)
    print("✅ 테스트 완료")
    print("=" * 60)
