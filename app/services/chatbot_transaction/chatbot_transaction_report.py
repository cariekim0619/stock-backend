"""
Chatbot_04 거래내역 / 요약 리포트 API
챗봇 기획에 맞춘 거래내역 조회 및 카카오톡 응답 포맷

기획:
- 계좌 연동 필수 (미연동 시 종목 입력 단계 진행 불가)
- 거래내역 요약 (말풍선 1) → 거래 패턴 해설 (말풍선 2) 순서
- 기간: 30일 고정 (30일 미만 시 자동 대체)
- RAG 적용: 거래 흐름, 매매 패턴, 체크 포인트
- 웹 링크: Web_04 상세 리포트 페이지 연결
"""

import os
from typing import Dict, List, Optional, Any
from datetime import datetime
from dotenv import load_dotenv
from app.services.segment_personalization import build_prompt_suffix, normalize_segment, get_personalization_note

load_dotenv()


class ChatbotTransactionReport:
    """
    Chatbot_04 거래내역/요약 리포트 데이터 프로바이더

    기능:
    - get_transaction_report(): 거래내역 + 요약 리포트 (말풍선 1+2)
    - format_entry_for_kakao(): 계좌 연동 시 기능 진입 화면
    - format_account_not_linked_kakao(): 계좌 미연동 응답
    - format_stock_not_found_for_kakao(): 종목 매칭 실패 응답
    - format_no_transaction_kakao(): 거래내역 없음 응답
    - format_report_for_kakao(): 카카오톡 API 2.0 형식 변환 (말풍선 1+2+버튼)
    """

    DEFAULT_PERIOD_DAYS = 30

    def __init__(self):
        """Initialize"""
        self.hantu = None

        # Gemini (LLM) - RAG 해설용
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

    def _get_hantu(self):
        if self.hantu is not None:
            return self.hantu

        from app.services.chatbot_report.HantuStock import HantuStock

        # 거래내역은 추천종목/시세용 KIS_ENV와 분리한다.
        # 기본 .env는 vps로 유지해 추천종목을 안정화하고,
        # 거래내역만 KIS_TRANSACTION_* 실전 계정으로 조회할 수 있게 한다.
        tx_env = (os.environ.get("KIS_TRANSACTION_ENV") or "").strip()
        tx_key = (os.environ.get("KIS_TRANSACTION_APP_KEY") or "").strip()
        tx_secret = (os.environ.get("KIS_TRANSACTION_APP_SECRET") or "").strip()
        tx_account = (os.environ.get("KIS_TRANSACTION_ACCOUNT_ID") or "").strip()
        tx_suffix = (os.environ.get("KIS_TRANSACTION_ACCOUNT_SUFFIX") or "01").strip() or "01"

        if tx_key and tx_secret and tx_account:
            self.hantu = HantuStock(
                api_key=tx_key,
                secret_key=tx_secret,
                account_id=tx_account,
                account_suffix=tx_suffix,
                env=tx_env or "prod",
            )
        else:
            self.hantu = HantuStock()

        return self.hantu

    # ========================================
    # 메인 API: 거래내역 리포트
    # ========================================

    def get_transaction_report(self, symbol: str, company_name: str, period: str = "1m", segment: str = "risk-neutral", profile: Optional[Dict[str, Any]] = None) -> Dict:
        """
        거래내역 + 요약 리포트 (챗봇 말풍선 1+2 데이터)

        Args:
            symbol: 종목코드 (예: "005930")
            company_name: 회사명 (예: "삼성전자")

        Returns:
            {
                "symbol": "005930",
                "company_name": "삼성전자",
                "period_days": 30,
                "period_insufficient": false,
                "summary": {
                    "total_trades": 5,
                    "buy_trades": 3,
                    "sell_trades": 2,
                    "buy_amount": 5010000,
                    "sell_amount": 1700000,
                    "realized_profit": -3310000
                },
                "rag_analysis": {
                    "flow": "거래 흐름 해설",
                    "pattern": "매매 패턴 해설",
                    "checkpoint": "체크 포인트"
                },
                "web_url": "https://securities.koreainvestment.com/app/mtsrenewal.jsp?type=06&SSO_SCREENNO=0800",
                "generated_at": "..."
            }
        """
        segment = normalize_segment(segment)
        # 1단계: 거래내역 조회
        try:
            transactions = self._get_hantu().get_transaction_history(period=period)
        except Exception as e:
            print(f"[ERROR] get_transaction_report: transaction history failed: {e}")
            return self._error_response(str(e), segment=segment, period=period, symbol=symbol, company_name=company_name)

        # 2단계: 해당 종목 필터링
        stock_transactions = [
            t for t in (transactions or []) if str(t.get("pdno", "")).strip() == str(symbol).strip()
        ]

        # 거래내역 없음
        if not stock_transactions:
            return {
                "symbol": symbol,
                "company_name": company_name,
                "no_transaction": True,
                "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "segment": segment,
                "period": period,
            }

        # 3단계: 실제 거래 기간 계산
        dates = sorted([t["ord_dt"] for t in stock_transactions])
        first_date = datetime.strptime(dates[0], "%Y%m%d")
        last_date = datetime.strptime(dates[-1], "%Y%m%d")
        actual_days = (last_date - first_date).days + 1

        period_insufficient = actual_days < self.DEFAULT_PERIOD_DAYS

        # 4단계: 거래 요약 계산
        summary = self._calculate_summary(stock_transactions)

        # 5단계: RAG 분석 (거래 패턴 해설)
        rag_analysis = self._generate_rag_analysis(
            stock_transactions, summary, company_name, actual_days, segment=segment, profile=profile
        )

        return {
            "symbol": symbol,
            "company_name": company_name,
            "period_days": min(actual_days, self.DEFAULT_PERIOD_DAYS),
            "period_insufficient": period_insufficient,
            "summary": summary,
            "rag_analysis": rag_analysis,
            "web_url": f"https://securities.koreainvestment.com/app/mtsrenewal.jsp?type=06&SSO_SCREENNO=0800",
            "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "segment": segment,
            "period": period,
        }

    # ========================================
    # 거래 요약 계산
    # ========================================

    def _calculate_summary(self, transactions: List[Dict]) -> Dict:
        """종목 거래 요약 계산"""
        buy_trades = 0
        sell_trades = 0
        buy_amount = 0
        sell_amount = 0

        for t in transactions:
            is_buy = str(t.get("sll_buy_dvsn_cd", "")) == "02"
            try:
                amt = float(t.get("tot_ccld_amt") or 0)
            except Exception:
                amt = 0

            if is_buy:
                buy_trades += 1
                buy_amount += amt
            else:
                sell_trades += 1
                sell_amount += amt

        realized_profit = sell_amount - buy_amount if sell_amount > 0 else 0

        return {
            "total_trades": len(transactions),
            "buy_trades": buy_trades,
            "sell_trades": sell_trades,
            "buy_amount": buy_amount,
            "sell_amount": sell_amount,
            "realized_profit": realized_profit,
        }

    # ========================================
    # RAG 분석 (거래 패턴 해설)
    # ========================================

    def _generate_rag_analysis(
        self,
        transactions: List[Dict],
        summary: Dict,
        company_name: str,
        period_days: int,
        segment: str = "risk-neutral",
        profile: Optional[Dict[str, Any]] = None,
    ) -> Dict:
        """
        RAG 기반 거래 패턴 분석

        기획서 말풍선 2 구조:
        ➊ 거래 흐름 - 거래가 집중된 시기, 패턴
        ➋ 매매 패턴 - 분할매수, 매수/매도 비율 등
        ➌ 체크 포인트 - 리스크, 주의사항
        """
        # 거래 데이터 요약 텍스트 생성
        trade_info = self._build_trade_info_text(transactions, summary, period_days)

        if not self.genai:
            return self._fallback_analysis(summary, segment=segment)

        prompt = f"""다음은 '{company_name}' 종목의 최근 {period_days}일간 거래내역 요약입니다.

{trade_info}

위 데이터를 기반으로 아래 3가지를 각각 1-2줄로 작성해주세요.

➊ 거래 흐름: 거래가 집중된 시기나 패턴을 관찰적으로 설명
➋ 매매 패턴: 분할매수, 매도 비중 등 패턴을 설명
➌ 체크 포인트: 이 거래 패턴에서 주의할 점

조건:
- "~에요", "~있어요" 친근한 말투
- 판단/추천이 아닌 관찰 + 설명만
- 각 항목 25자 이내
- 매수/매도 추천 금지

형식:
➊ [거래 흐름 내용]
➋ [매매 패턴 내용]
➌ [체크 포인트 내용]""" + build_prompt_suffix(segment, domain="transaction", profile=profile)

        try:
            model = self.genai.GenerativeModel(
                'gemini-2.5-flash',
                system_instruction="당신은 한국 주식시장 거래내역 분석가입니다. 숫자와 데이터에 기반하여 관찰된 패턴만 설명합니다. 매수/매도 추천은 하지 않습니다."
            )
            response = model.generate_content(
                prompt,
                generation_config={"temperature": 0.3, "max_output_tokens": 1024}
            )
            return self._parse_rag_response(response.text.strip())
        except Exception:
            return self._fallback_analysis(summary, segment=segment)

    def _build_trade_info_text(
        self, transactions: List[Dict], summary: Dict, period_days: int
    ) -> str:
        """거래 데이터를 텍스트로 변환 (RAG 입력용)"""
        lines = []
        lines.append(f"거래 기간: 최근 {period_days}일")
        lines.append(f"총 거래 횟수: {summary['total_trades']}회 (매수 {summary['buy_trades']}회, 매도 {summary['sell_trades']}회)")
        lines.append(f"총 매수금액: {summary['buy_amount']:,.0f}원")
        lines.append(f"총 매도금액: {summary['sell_amount']:,.0f}원")
        lines.append(f"실현손익: {summary['realized_profit']:+,.0f}원")
        lines.append("")

        # 개별 거래 내역
        lines.append("거래 상세:")
        for t in transactions:
            side = "매수" if str(t.get("sll_buy_dvsn_cd", "")) == "02" else "매도"
            date = str(t.get("ord_dt") or "")
            formatted_date = f"{date[:4]}-{date[4:6]}-{date[6:]}" if len(date) >= 8 else date
            qty = t.get("tot_ccld_qty") or 0
            price = float(t.get("avg_prvs") or 0)
            amount = float(t.get("tot_ccld_amt") or 0)
            lines.append(f"  {formatted_date} {side} {qty}주 @ {price:,.0f}원 (총 {amount:,.0f}원)")

        return "\n".join(lines)

    def _parse_rag_response(self, text: str) -> Dict:
        """RAG 응답 파싱"""
        import re

        result = {
            "flow": "",
            "pattern": "",
            "checkpoint": "",
        }

        # 마크다운 bold 제거
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)

        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue

            if '➊' in line or '거래 흐름' in line:
                cleaned = re.sub(r'^[➊❶①1]\s*', '', line)
                cleaned = re.sub(r'거래\s*흐름\s*[:：]?\s*', '', cleaned)
                cleaned = cleaned.strip(' -:：')
                if cleaned:
                    result["flow"] = cleaned
            elif '➋' in line or '매매 패턴' in line:
                cleaned = re.sub(r'^[➋❷②2]\s*', '', line)
                cleaned = re.sub(r'매매\s*패턴\s*[:：]?\s*', '', cleaned)
                cleaned = cleaned.strip(' -:：')
                if cleaned:
                    result["pattern"] = cleaned
            elif '➌' in line or '체크 포인트' in line or '체크포인트' in line:
                cleaned = re.sub(r'^[➌❸③3]\s*', '', line)
                cleaned = re.sub(r'체크\s*포인트\s*[:：]?\s*', '', cleaned)
                cleaned = cleaned.strip(' -:：')
                if cleaned:
                    result["checkpoint"] = cleaned

        # fallback
        if not result["flow"]:
            result = self._fallback_analysis(None)

        return result

    def _fallback_analysis(self, summary: Optional[Dict], segment: str = "risk-neutral") -> Dict:
        """LLM 실패 시 기본 분석"""
        if summary and summary.get("buy_trades", 0) > summary.get("sell_trades", 0):
            flow = "최근 거래는 매수 중심으로 이루어지고 있어요."
            pattern = "같은 종목을 여러 가격대에서 나눠 매수한 기록이 있어요."
        elif summary and summary.get("sell_trades", 0) > 0:
            flow = "최근 거래는 매수와 매도가 함께 이루어지고 있어요."
            pattern = "매수 후 일부 매도하며 수익을 실현하는 패턴이에요."
        else:
            flow = "최근 거래는 특정 구간에 집중되어 있어요."
            pattern = "거래 패턴을 분석 중이에요."

        checkpoint = "거래 횟수가 많을수록 단기 가격 변동의 영향을 더 받을 수 있어요."
        # v5: 성향 라벨/고정 문구는 사용자 응답에 직접 덧붙이지 않는다.
        # 거래내역 개인화는 LLM prompt_suffix에서 분석 톤으로 반영한다.

        return {
            "flow": flow,
            "pattern": pattern,
            "checkpoint": checkpoint,
        }

    # ========================================
    # 카카오톡 포맷
    # ========================================

    def format_entry_for_kakao(self) -> Dict:
        """
        계좌 연동 시 기능 진입 화면

        기획: 보유 종목 확인 퀵 버튼 포함, 종목명 직접 입력 유도
        """
        return {
            "version": "2.0",
            "template": {
                "outputs": [{
                    "simpleText": {
                        "text": "📑 거래내역 / 요약 리포트 기능이에요\n\n어떤 종목에 대한 거래내역을 설명해드릴까요 ?\n\n보유 종목을 확인하려면,\n하단의 [보유 종목 확인] 버튼을 눌러 확인 후 종목명을 입력해 주세요."
                    }
                }],
                "quickReplies": [
                    {
                        "action": "block",
                        "label": "보유 종목 확인",
                        "messageText": "보유 종목 확인",
                        "blockId": "holding_list_block",
                    },
                ],
            },
        }

    def format_stock_not_found_for_kakao(self) -> Dict:
        """
        종목 매칭 실패 시 카카오톡 응답
        """
        return {
            "version": "2.0",
            "template": {
                "outputs": [{
                    "simpleText": {
                        "text": "⚠️ 입력하신 종목을 찾지 못했어요.\n\n종목명을 다시 입력해 주세요"
                    }
                }],
                "quickReplies": [
                    {
                        "action": "block",
                        "label": "메인으로",
                        "messageText": "메인으로",
                        "blockId": "main_block",
                    },
                ],
            },
        }

    def format_report_for_kakao(self, report: Dict) -> Dict:
        """
        거래내역 리포트를 카카오톡 API 2.0 형식으로 변환

        기획:
        - 말풍선 1: 거래내역 요약
        - 말풍선 2: 거래 패턴 해설 (RAG)
        - 말풍선 3: 웹 상세 리포트 버튼 (basicCard)
        - 퀵 버튼: 다른 종목 보기 / 종료
        """
        if report.get("error"):
            return self._kakao_error_response(report.get("error") or "거래내역 조회 중 오류가 발생했어요.")

        if report.get("no_transaction"):
            return self.format_no_transaction_kakao(
                report.get("symbol", ""),
                report.get("company_name", "종목"),
                report.get("period", "1m"),
            )

        company = report.get("company_name", "종목")
        symbol = report.get("symbol", "")
        period_days = report.get("period_days", 30)
        requested_period = str(report.get("period") or "1m").strip()
        period_label_map = {"today": "오늘", "1w": "최근 1주일", "1m": "최근 1개월", "3m": "최근 3개월", "1y": "최대 기간"}
        display_period = period_label_map.get(requested_period, f"최근 {period_days}일")
        summary = report.get("summary", {})
        rag = report.get("rag_analysis", {})
        web_url = report.get("web_url", "https://securities.koreainvestment.com/app/mtsrenewal.jsp?type=06&SSO_SCREENNO=0800")
        period_insufficient = report.get("period_insufficient", False)

        # 말풍선 1: 거래내역 요약
        buy_amount_str = f"{summary.get('buy_amount', 0):,.0f}"
        sell_amount_str = f"{summary.get('sell_amount', 0):,.0f}"
        profit = summary.get("realized_profit", 0)
        profit_sign = "+" if profit >= 0 else ""

        message_1 = f"📑 {company} 거래내역 요약이에요!\n\n"
        message_1 += f"• 거래 기간 : {display_period}\n"
        message_1 += f"• 거래 횟수 : 총{summary.get('total_trades', 0)}회"
        message_1 += f" (매수{summary.get('buy_trades', 0)}회/ 매도{summary.get('sell_trades', 0)}회)\n"
        message_1 += f"• 총 매수금액 : {buy_amount_str}원\n"
        message_1 += f"• 총 매도금액 : {sell_amount_str}원\n"
        message_1 += f"• 실현손익(매도 기준) : {profit_sign}{profit:,.0f}원"

        if period_insufficient:
            message_1 = f"선택한 기간 안에서 거래내역을 확인했어요.\n\n이 거래내역을 기준으로\n요약 리포트를 작성해드릴게요 !\n잠시만 기다려주세요!\n\n{message_1}"

        # 말풍선 2: 거래 패턴 해설 (RAG)
        message_2 = "📑 요약 리포트\n\n"
        message_2 += f"➊ 거래 흐름\n- {rag.get('flow', '분석 중이에요.')}\n\n"
        message_2 += f"➋ 매매 패턴\n- {rag.get('pattern', '분석 중이에요.')}\n\n"
        message_2 += f"➌ 체크 포인트\n- {rag.get('checkpoint', '분석 중이에요.')}"

        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": message_1}},
                    {"simpleText": {"text": message_2}},
                    {
                        "basicCard": {
                            "buttons": [
                                {
                                    "action": "webLink",
                                    "label": "웹에서 상세 리포트 보기",
                                    "webLinkUrl": web_url,
                                }
                            ]
                        }
                    },
                ],
                "quickReplies": [
                    {
                        "action": "block",
                        "label": "다른 종목 보기",
                        "messageText": "다른 종목",
                        "blockId": "select_stock_block",
                    },
                    {
                        "action": "block",
                        "label": "종료",
                        "messageText": "종료",
                        "blockId": "end_block",
                    },
                ],
            },
        }

    def format_account_not_linked_kakao(self) -> Dict:
        """
        계좌 미연동 시 카카오톡 응답

        기획: 종목 입력 단계로 진행하지 않음
        """
        return {
            "version": "2.0",
            "template": {
                "outputs": [{
                    "simpleText": {
                        "text": "📑 거래내역 / 요약 리포트는\n계좌 연결 후 이용할 수 있어요 :)\n\n계좌를 연결하시면,\n최근 거래내역을 기반으로 종목별 리포트를 바로 제공해드릴게요 !"
                    }
                }],
                "quickReplies": [
                    {
                        "action": "block",
                        "label": "계좌 연결하기",
                        "messageText": "계좌 연결",
                        "blockId": "account_connect_block",
                    },
                    {
                        "action": "block",
                        "label": "기본 화면으로",
                        "messageText": "메인으로",
                        "blockId": "main_block",
                    },
                ],
            },
        }

    def format_no_transaction_kakao(self, symbol: str, company_name: str, period: str = "1m") -> Dict:
        """
        거래내역 없음 시 카카오톡 응답
        """
        period_label = {"today": "오늘", "1w": "최근 1주일", "1m": "최근 1개월", "3m": "최근 3개월", "1y": "최대 기간"}.get(period, "선택한 기간")
        return {
            "version": "2.0",
            "template": {
                "outputs": [{
                    "simpleText": {
                        "text": f"⚠️ {period_label} 동안\n{company_name}의 거래내역이 없어요.\n\n기간을 더 길게 선택하거나 다른 종목을 입력해주세요."
                    }
                }],
                "quickReplies": [
                    {
                        "action": "block",
                        "label": "메인으로",
                        "messageText": "메인으로",
                        "blockId": "main_block",
                    },
                ],
            },
        }

    def _error_response(self, reason: str, *, segment: str = "risk-neutral", period: str = "1m", symbol: str = "", company_name: str = "") -> Dict:
        """에러 응답"""
        return {
            "error": reason,
            "symbol": symbol,
            "company_name": company_name or symbol or "종목",
            "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "segment": segment,
            "period": period,
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
    print("Chatbot_04 거래내역/요약 리포트 테스트")
    print("=" * 60)
    print()

    chatbot = ChatbotTransactionReport()

    # 테스트: 삼성전자
    symbol = "005930"
    company = "삼성전자"

    # 1. 거래내역 리포트
    print("[1단계] 거래내역 리포트")
    print("-" * 40)
    report = chatbot.get_transaction_report(symbol, company)

    if report.get("no_transaction"):
        print(f"거래내역 없음: {company}")
    else:
        summary = report.get("summary", {})
        print(f"기간: 최근 {report.get('period_days')}일")
        print(f"기간 부족: {report.get('period_insufficient')}")
        print(f"총 거래: {summary.get('total_trades')}회 (매수 {summary.get('buy_trades')} / 매도 {summary.get('sell_trades')})")
        print(f"매수금액: {summary.get('buy_amount', 0):,.0f}원")
        print(f"매도금액: {summary.get('sell_amount', 0):,.0f}원")
        print(f"실현손익: {summary.get('realized_profit', 0):+,.0f}원")
        print()

        rag = report.get("rag_analysis", {})
        print("[RAG 분석]")
        print(f"  ➊ 거래 흐름: {rag.get('flow')}")
        print(f"  ➋ 매매 패턴: {rag.get('pattern')}")
        print(f"  ➌ 체크 포인트: {rag.get('checkpoint')}")
    print()

    # 2. 카카오톡 형식
    print("[2단계] 카카오톡 응답")
    print("-" * 40)
    kakao = chatbot.format_report_for_kakao(report)
    print(json.dumps(kakao, ensure_ascii=False, indent=2)[:800] + "...")
    print()

    # 3. 계좌 미연동 응답
    print("[3단계] 계좌 미연동 응답")
    print("-" * 40)
    not_linked = chatbot.format_account_not_linked_kakao()
    print(json.dumps(not_linked, ensure_ascii=False, indent=2))
    print()

    print("=" * 60)
    print("테스트 완료")
    print("=" * 60)
