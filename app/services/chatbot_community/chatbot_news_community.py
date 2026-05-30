"""
Chatbot_05 뉴스/커뮤니티 API
챗봇 기획에 맞춘 데이터 제공 및 카카오톡 응답 포맷

기획:
- 커뮤니티 → 뉴스 순서로 제공
- 커뮤니티: 감정 톤 + 대표 의견 2-3개
- 뉴스: 핵심 이슈 3-5건
- 실시간성 표현
"""

import os
import re
from typing import Dict, List, Optional
from datetime import datetime
from dotenv import load_dotenv
from app.utils.gemini_compat import GeminiCompatClient
from app.utils.ticker_normalizer import resolve_symbol_and_name
from app.services.segment_personalization import build_prompt_suffix, get_personalization_note, normalize_segment

load_dotenv()


class ChatbotNewsCommunity:
    """
    Chatbot_05 뉴스/커뮤니티 데이터 프로바이더

    기능:
    - get_community_summary(): 커뮤니티 분위기 + 대표 의견
    - get_news_summary(): 주요 뉴스 핵심 이슈
    - format_for_kakao(): 카카오톡 API 2.0 형식 변환
    """

    def __init__(self):
        """Initialize"""
        # 기존 데이터 프로바이더 사용
        from app.services.chatbot_community.stock_news_data import StockNewsDataProvider
        self.data_provider = StockNewsDataProvider()

        # Gemini (LLM)
        self.gemini_key = os.environ.get("GEMINI_API_KEY")
        if self.gemini_key:
            try:
                self.genai = GeminiCompatClient(self.gemini_key)
            except Exception as e:
                print(f"Warning: Gemini 초기화 실패 - {e}")
                self.genai = None
        else:
            self.genai = None


    def _clean_search_text(self, value: str, limit: int = 60) -> str:
        text = re.sub(r"<[^>]+>", " ", str(value or ""))
        text = re.sub(r"https?://\S+", " ", text)
        text = re.sub(r"\s+", " ", text).strip().strip("-–—|:· ")
        if len(text) > limit:
            text = text[: max(0, limit - 1)].rstrip() + "…"
        return text

    def _is_low_quality_title(self, text: str) -> bool:
        clean = self._clean_search_text(text, limit=160)
        low = clean.lower()
        if not clean or len(clean) < 6:
            return True
        noisy = [
            "의견 예상치", "컨센서스", "시장종합", "네이버 블로그", "traderfeels",
            "목표주가 -", "주가전망, 목표주가", "주가 전망, 목표주가",
            "investing.com", "기업개요", "증권사 리포트", "관련이 아니",
            "특정 기업 소식 없이", "기본 관점", "주요 경제뉴스",
        ]
        if any(x.lower() in low for x in noisy):
            return True
        if clean.rstrip().endswith(("-", "–", "—", "|")):
            return True
        return False

    def _fallback_key_opinions(self, items: List[Dict], company_name: str) -> List[str]:
        opinions: List[str] = []
        generic = {"주가 전망과 전략이 중요", "변동성 크고 투자주의 필요", "투자주의 필요", "전략이 중요"}
        for item in items or []:
            if self._is_low_quality_title(item.get("title", "")) and ("블로그" in str(item.get("source", "")) or "blog" in str(item.get("url", "")).lower()):
                continue
            candidates = [item.get("content", ""), item.get("title", "")]
            for raw in candidates:
                for part in re.split(r"[\.\!\?。\n]+", str(raw or "")):
                    line = self._clean_search_text(part, limit=38)
                    if not line or line in generic or self._is_low_quality_title(line):
                        continue
                    if company_name and line == company_name:
                        continue
                    opinions.append(line)
                    break
                if opinions and opinions[-1] == line:
                    break
            if len(opinions) >= 3:
                break
        if not opinions:
            opinions.append("실적과 수급을 지켜보는 분위기예요")
        return opinions[:3]

    def _fallback_key_issues(self, items: List[Dict], company_name: str) -> List[Dict]:
        issues: List[Dict] = []
        for item in items or []:
            if self._is_low_quality_title(item.get("title", "")) and ("블로그" in str(item.get("source", "")) or "blog" in str(item.get("url", "")).lower()):
                continue
            title = self._clean_search_text(item.get("title", ""), limit=45)
            if self._is_low_quality_title(title):
                content = str(item.get("content", ""))
                title = ""
                for part in re.split(r"[\.\!\?。\n]+", content):
                    candidate = self._clean_search_text(part, limit=45)
                    if candidate and not self._is_low_quality_title(candidate):
                        title = candidate
                        break
            if not title:
                continue
            issues.append({
                "title": title,
                "source": item.get("source", ""),
                "url": item.get("url", ""),
                "impact": item.get("impact", "MEDIUM"),
            })
            if len(issues) >= 5:
                break
        return issues

    # ========================================
    # 커뮤니티 요약
    # ========================================

    def get_community_summary(
        self,
        symbol: str,
        company_name: str,
        segment: str = "risk-neutral",
        profile: Optional[Dict] = None,
    ) -> Dict:
        """
        커뮤니티 요약 (챗봇용)

        Args:
            symbol: 종목코드 (예: "005930")
            company_name: 회사명 (예: "삼성전자")

        Returns:
            {
                "symbol": "005930",
                "company_name": "삼성전자",
                "sentiment_tone": "긍정",  # 긍정/중립/부정
                "sentiment_emoji": "😊",
                "summary_text": "전반적으로 긍정적인 의견이 많아요",
                "key_opinions": [
                    "실적 바닥은 지난 것 같다",
                    "외국인 수급이 계속 유입 중",
                    "단기 급등은 부담"
                ],
                "timestamp": "방금 전까지",
                "web_url": "https://..."
            }
        """

        segment = normalize_segment(segment)

        # 커뮤니티 데이터 조회
        community_data = self.data_provider.get_community(
            symbol=symbol,
            company_name=company_name,
            page=1,
            limit=10
        )

        if "error" in community_data:
            return self._error_response(community_data["error"])

        # 감정 톤 분석
        items = community_data.get("items", [])
        sentiment_tone = self._calculate_overall_sentiment(items)
        sentiment_emoji = self._get_sentiment_emoji(sentiment_tone)

        # 대표 의견 추출 (2-3개)
        key_opinions = self._extract_key_opinions(items, company_name, segment=segment, profile=profile)

        # 요약 텍스트 생성
        summary_text = self._generate_sentiment_summary(sentiment_tone, items)
        # v5: 성향 라벨/고정 문구를 사용자 응답에 직접 덧붙이지 않는다.
        # 개인화는 LLM prompt_suffix에서 강조점과 해석 톤으로 반영한다.

        # 실시간성 표현
        timestamp = self._get_realtime_expression()

        return {
            "symbol": symbol,
            "company_name": company_name,
            "sentiment_tone": sentiment_tone,
            "sentiment_emoji": sentiment_emoji,
            "summary_text": summary_text,
            "key_opinions": key_opinions[:3],  # 최대 3개
            "timestamp": timestamp,
            "web_url": f"https://securities.koreainvestment.com/app/mtsrenewal.jsp?type=06&SSO_SCREENNO=0800",
            "fetched_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "segment": segment,
        }

    def _calculate_overall_sentiment(self, items: List[Dict]) -> str:
        """전체 감정 톤 계산"""
        if not items:
            return "중립"

        sentiment_counts = {"positive": 0, "neutral": 0, "negative": 0}
        for item in items:
            sentiment = item.get("sentiment", "neutral")
            sentiment_counts[sentiment] += 1

        # 가장 많은 감정 톤
        max_sentiment = max(sentiment_counts, key=sentiment_counts.get)

        # 한글 변환
        sentiment_map = {
            "positive": "긍정",
            "neutral": "중립",
            "negative": "부정"
        }
        return sentiment_map.get(max_sentiment, "중립")

    def _get_sentiment_emoji(self, tone: str) -> str:
        """감정 톤에 맞는 이모지"""
        emoji_map = {
            "긍정": "😊",
            "중립": "😐",
            "부정": "😟"
        }
        return emoji_map.get(tone, "😐")

    def _generate_sentiment_summary(self, tone: str, items: List[Dict]) -> str:
        """감정 톤 요약 텍스트"""
        tone_templates = {
            "긍정": "전반적으로 긍정적인 의견이 많아요",
            "중립": "긍정과 부정 의견이 비슷해요",
            "부정": "조심스러운 의견이 많아요"
        }
        base_summary = tone_templates.get(tone, "다양한 의견이 있어요")

        # 주요 이유 추출 (AI 요약 활용)
        if items and len(items) > 0:
            # 간단한 키워드 분석으로 이유 추가
            reason = self._extract_main_reason(items)
            if reason:
                return f"{base_summary}\n• {reason}"

        return base_summary

    def _extract_main_reason(self, items: List[Dict]) -> str:
        """주요 이유 키워드 추출"""
        keywords = {
            "실적": ["실적", "매출", "영업이익", "순이익"],
            "수급": ["외국인", "기관", "수급", "매수"],
            "전망": ["전망", "기대", "예상", "목표"],
            "우려": ["우려", "리스크", "부담", "하락"]
        }

        keyword_counts = {k: 0 for k in keywords}

        for item in items[:5]:  # 상위 5개만
            content = item.get("content", "").lower()
            for category, words in keywords.items():
                if any(word in content for word in words):
                    keyword_counts[category] += 1

        if max(keyword_counts.values()) > 0:
            top_reason = max(keyword_counts, key=keyword_counts.get)
            reason_templates = {
                "실적": "실적 개선 기대감이 주요 이유예요",
                "수급": "외국인/기관의 매수세가 이어지고 있어요",
                "전망": "긍정적인 전망이 많이 나오고 있어요",
                "우려": "일부 우려 요인이 언급되고 있어요"
            }
            return reason_templates.get(top_reason, "")

        return ""

    def _extract_key_opinions(self, items: List[Dict], company_name: str, segment: str = "risk-neutral", profile: Optional[Dict] = None) -> List[str]:
        """대표 의견 추출 (LLM 활용)"""
        if not items or not self.genai:
            # LLM 없으면 간단한 추출
            return self._fallback_key_opinions(items, company_name)

        # LLM으로 핵심 의견 추출
        contents = []
        for item in items[:10]:
            title = item.get("title", "")
            content = item.get("content", "")[:100]
            contents.append(f"- {title}: {content}")

        prompt = f"""
다음은 '{company_name}' 종목에 대한 투자자 의견들입니다.
실제 게시글 내용에서 반복되는 구체적인 투자자 의견만 3개 뽑아주세요.

{chr(10).join(contents)}

조건:
- 출력은 의견 3줄만 작성
- 각 의견은 18자 이내
- 헤더, 설명, 따옴표, 번호, 종목명 반복 금지
- "주가 전망과 전략이 중요", "변동성 크고 투자주의 필요"처럼 모든 종목에 적용되는 일반론 금지
- 원문에 근거가 부족하면 억지로 만들지 말고 1~2개만 작성

좋은 예시:
실적 바닥은 지난 듯
외국인 수급 유입 중
단기 급등은 부담
""" + build_prompt_suffix(segment, domain="community", profile=profile)
        try:
            model = self.genai.GenerativeModel('gemini-2.5-flash')
            response = model.generate_content(prompt)
            import re
            opinions = []
            for line in response.text.strip().split('\n'):
                line = line.strip()
                if not line:
                    continue
                # LLM이 반복하는 헤더/라벨/일반론 제거
                if any(tok in line for tok in ["대표", "의견 3", "3개", "다음은", "종목에 대한"]):
                    continue
                line = re.sub(r'^\d+[\.\)]\s*', '', line)
                line = re.sub(r'^[\-\•\*]\s*', '', line).strip().strip("\"'“”‘’")
                line = self._clean_search_text(line, limit=40)
                generic = {"주가 전망과 전략이 중요", "변동성 크고 투자주의 필요", "투자주의 필요", "전략이 중요"}
                if line and line not in generic and not self._is_low_quality_title(line) and len(line) <= 40:
                    opinions.append(line)
            return opinions[:3]
        except Exception:
            # 실패 시 제목 사용
            return self._fallback_key_opinions(items, company_name)

    def _get_realtime_expression(self) -> str:
        """실시간성 표현"""
        expressions = [
            "방금 전까지",
            "최근 몇 시간 기준으로",
            "오늘 기준"
        ]
        # 현재 시간에 따라 선택 (간단히 첫 번째 사용)
        return expressions[0]

    # ========================================
    # 뉴스 요약
    # ========================================

    def get_news_summary(
        self,
        symbol: str,
        company_name: str,
        segment: str = "risk-neutral",
        profile: Optional[Dict] = None,
    ) -> Dict:
        """
        뉴스 요약 (챗봇용)

        Args:
            symbol: 종목코드
            company_name: 회사명

        Returns:
            {
                "symbol": "005930",
                "company_name": "삼성전자",
                "key_issues": [
                    {
                        "title": "2분기 실적이 시장 예상치를 상회했어요",
                        "source": "한국경제",
                        "url": "https://...",
                        "impact": "HIGH"
                    }
                ],
                "timestamp": "최근",
                "web_url": "https://..."
            }
        """

        segment = normalize_segment(segment)

        # 뉴스 데이터 조회
        news_data = self.data_provider.get_news(
            symbol=symbol,
            company_name=company_name,
            page=1,
            limit=15  # 필터링 후 줄어들 수 있으므로 넉넉히
        )

        if "error" in news_data:
            return self._error_response(news_data["error"])

        items = news_data.get("items", [])

        # 투자 영향도 HIGH/MEDIUM 뉴스만 선택
        filtered_news = self._filter_high_impact_news(items)

        # 핵심 이슈로 변환 (3-5개)
        key_issues = self._convert_to_key_issues(filtered_news[:5], company_name, segment=segment, profile=profile)
        return {
            "symbol": symbol,
            "company_name": company_name,
            "key_issues": key_issues,
            "timestamp": "최근",
            "web_url": f"https://securities.koreainvestment.com/app/mtsrenewal.jsp?type=06&SSO_SCREENNO=0800",
            "fetched_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "segment": segment,
        }

    def _filter_high_impact_news(self, items: List[Dict]) -> List[Dict]:
        """투자 영향도 HIGH/MEDIUM 뉴스 필터링"""
        # 고영향 키워드
        high_impact_keywords = [
            "실적", "영업이익", "순이익", "매출",
            "급등", "급락", "목표가", "투자의견",
            "배당", "자사주", "증자", "공시",
            "인수", "합병", "M&A", "승인"
        ]

        filtered = []
        for item in items:
            title = item.get("title", "").lower()
            content = item.get("content", "").lower()
            text = f"{title} {content}"

            # 키워드 매칭 개수
            match_count = sum(1 for kw in high_impact_keywords if kw in text)

            if match_count >= 1:  # 1개 이상 매칭
                item["impact"] = "HIGH" if match_count >= 2 else "MEDIUM"
                filtered.append(item)

        # HIGH 우선 정렬
        filtered.sort(key=lambda x: 0 if x.get("impact") == "HIGH" else 1)
        return filtered

    def _convert_to_key_issues(self, items: List[Dict], company_name: str, segment: str = "risk-neutral", profile: Optional[Dict] = None) -> List[Dict]:
        """뉴스를 핵심 이슈로 변환"""
        if not items or not self.genai:
            # LLM 없으면 제목 그대로
            return self._fallback_key_issues(items, company_name)

        # LLM으로 핵심 이슈 추출
        news_list = []
        for i, item in enumerate(items, 1):
            title = item.get("title", "")
            content = item.get("content", "")[:100]
            news_list.append(f"{i}. [{title}] {content}")

        prompt = f"""
다음은 '{company_name}' 관련 뉴스 후보입니다.
'{company_name}'와 직접 관련된 뉴스만 투자자가 이해하기 쉽게 한 문장으로 요약해주세요.
직접 관련이 없으면 해당 줄은 반드시 '제외'라고만 쓰세요.

{chr(10).join(news_list)}

조건:
- 각 이슈는 25자 이내
- "~했어요", "~예요" 형태의 친근한 말투
- 광범위한 경제뉴스, 업종 전체 뉴스, 기업명이 없는 뉴스는 제외
- "관련이 아니에요", "특정 기업 소식 없이", "기본 관점" 같은 문구 출력 금지
- 핵심만 간결하게

예시:
2분기 실적이 시장 예상치를 상회했어요
제외
반도체 업황 회복 기대가 언급됐어요
""" + build_prompt_suffix(segment, domain="news", profile=profile)
        try:
            model = self.genai.GenerativeModel('gemini-2.5-flash')
            response = model.generate_content(prompt)
            import re
            summaries = []
            for line in response.text.strip().split('\n'):
                line = line.strip()
                if not line:
                    continue
                if '핵심' in line and '이슈' in line and line.endswith(':'):
                    continue
                line = re.sub(r'^\d+[\.\)]\s*', '', line)
                line = re.sub(r'^[\-\•\*]\s*', '', line).strip().strip("\"'“”‘’")
                if not line or line == "제외":
                    continue
                if any(bad in line for bad in ["관련이 아니", "특정 기업 소식 없이", "기본 관점", "주요 경제뉴스"]):
                    continue
                summaries.append(line)

            # 원본 데이터와 결합
            key_issues = []
            for i, item in enumerate(items):
                summary = summaries[i] if i < len(summaries) else item.get("title", "")
                summary = self._clean_search_text(summary, limit=45)
                if any(bad in str(summary) for bad in ["관련이 아니", "특정 기업 소식 없이", "기본 관점", "주요 경제뉴스", "제외"]):
                    continue
                if self._is_low_quality_title(summary):
                    fallback_one = self._fallback_key_issues([item], company_name)
                    if not fallback_one:
                        continue
                    summary = fallback_one[0]["title"]
                key_issues.append({
                    "title": summary,
                    "source": item.get("source", ""),
                    "url": item.get("url", ""),
                    "impact": item.get("impact", "MEDIUM")
                })
            return key_issues

        except Exception:
            # 실패 시 제목 그대로
            return self._fallback_key_issues(items, company_name)

    # ========================================
    # 카카오톡 포맷
    # ========================================

    def format_community_for_kakao(self, summary: Dict, user_name: str = "투자자") -> Dict:
        """
        커뮤니티 요약을 카카오톡 API 2.0 형식으로 변환

        기획:
        - 1차 메시지: 커뮤니티 분위기
        - 2차 메시지: 대표 의견 2-3개
        - 퀵 버튼: 뉴스도 보기 / 다른 종목 / 웹에서 자세히 / 기능 종료
        """
        if "error" in summary:
            return self._kakao_error_response(summary["error"])

        company_name = summary.get("company_name", "종목")
        sentiment_emoji = summary.get("sentiment_emoji", "😐")
        summary_text = summary.get("summary_text", "")
        segment = normalize_segment(summary.get("segment"))
        opinions = summary.get("key_opinions", [])
        timestamp = summary.get("timestamp", "최근")

        # 1차 메시지: 커뮤니티 분위기
        message_1 = f"""{timestamp} {company_name}에 대한
커뮤니티 분위기부터 알려드릴게요 {sentiment_emoji}

{summary_text}"""

        # 2차 메시지: 대표 의견
        opinions_text = "\n".join([f"- \"{op}\"" for op in opinions]) or "- 아직 뚜렷한 반복 의견은 많지 않아요"
        message_2 = f"""대표적인 의견을 몇 개 보면 아래와 같아요 :)

{opinions_text}

자세한 커뮤니티는 하단의 퀵 버튼을 눌러 웹에서 확인하세요 !"""

        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": message_1
                        }
                    },
                    {
                        "simpleText": {
                            "text": message_2
                        }
                    }
                ],
                "quickReplies": [
                    {
                        "action": "block",
                        "label": "뉴스도 보기",
                        "messageText": f"{company_name} 뉴스",
                        "blockId": "news_block"  # 실제 블록 ID로 교체
                    },
                    {
                        "action": "block",
                        "label": "다른 종목 보기",
                        "messageText": "다른 종목",
                        "blockId": "select_stock_block"
                    },
                    {
                        "action": "webLink",
                        "label": "웹에서 자세히 보기",
                        "webLinkUrl": summary.get("web_url", "https://securities.koreainvestment.com/app/mtsrenewal.jsp?type=06&SSO_SCREENNO=0800")
                    },
                    {
                        "action": "block",
                        "label": "기능 종료",
                        "messageText": "메인으로",
                        "blockId": "main_block"
                    }
                ]
            }
        }

    def format_news_for_kakao(self, summary: Dict) -> Dict:
        """
        뉴스 요약을 카카오톡 API 2.0 형식으로 변환

        기획:
        - 1차 메시지: 주요 뉴스 핵심 이슈 (3-5개)
        - 퀵 버튼: 기사 원문 / 다른 종목 / 웹에서 더 보기 / 기능 종료
        """
        if "error" in summary:
            return self._kakao_error_response(summary["error"])

        company_name = summary.get("company_name", "종목")
        key_issues = summary.get("key_issues", [])
        timestamp = summary.get("timestamp", "최근")
        segment = normalize_segment(summary.get("segment"))

        if not key_issues:
            message = f"최근 {company_name}와 직접 관련된 주요 뉴스를 찾지 못했어요.\n\n잠시 후 다시 확인해 주세요."
        else:
            issues_text = "\n".join([f"• {issue['title']}" for issue in key_issues])
            message = f"""{timestamp} {company_name} 관련
주요 뉴스도 정리해봤어요.

{issues_text}"""

        # 첫 번째 뉴스 URL (기사 원문 보기용)
        first_news_url = key_issues[0].get("url", "") if key_issues else ""

        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": message
                        }
                    }
                ],
                "quickReplies": [
                    {
                        "action": "webLink",
                        "label": "기사 원문 보기",
                        "webLinkUrl": first_news_url or "https://securities.koreainvestment.com/app/mtsrenewal.jsp?type=06&SSO_SCREENNO=0800"
                    },
                    {
                        "action": "block",
                        "label": "다른 종목 보기",
                        "messageText": "다른 종목",
                        "blockId": "select_stock_block"
                    },
                    {
                        "action": "webLink",
                        "label": "웹에서 더 보기",
                        "webLinkUrl": summary.get("web_url", "https://securities.koreainvestment.com/app/mtsrenewal.jsp?type=06&SSO_SCREENNO=0800")
                    },
                    {
                        "action": "block",
                        "label": "기능 종료",
                        "messageText": "메인으로",
                        "blockId": "main_block"
                    }
                ]
            }
        }

    def _error_response(self, reason: str) -> Dict:
        """에러 응답"""
        return {
            "error": reason,
            "fetched_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
            }
        }


# ========================================
# 테스트
# ========================================

if __name__ == "__main__":
    import json

    print("=" * 60)
    print("Chatbot_05 뉴스/커뮤니티 테스트")
    print("=" * 60)
    print()

    chatbot = ChatbotNewsCommunity()

    # 테스트: 삼성전자
    symbol = "005930"
    company = "삼성전자"

    # 1. 커뮤니티 요약
    print("[1단계] 커뮤니티 요약")
    print("-" * 40)
    community = chatbot.get_community_summary(symbol, company)
    print(f"감정 톤: {community.get('sentiment_tone')} {community.get('sentiment_emoji')}")
    print(f"요약: {community.get('summary_text')}")
    print(f"대표 의견:")
    for op in community.get("key_opinions", []):
        print(f"  - {op}")
    print()

    # 2. 뉴스 요약
    print("[2단계] 뉴스 요약")
    print("-" * 40)
    news = chatbot.get_news_summary(symbol, company)
    print(f"핵심 이슈 {len(news.get('key_issues', []))}건:")
    for issue in news.get("key_issues", []):
        print(f"  [{issue['impact']}] {issue['title']}")
    print()

    # 3. 카카오톡 형식
    print("[3단계] 카카오톡 응답 형식")
    print("-" * 40)
    print("\n[커뮤니티 응답]")
    kakao_comm = chatbot.format_community_for_kakao(community)
    print(json.dumps(kakao_comm, ensure_ascii=False, indent=2)[:800] + "...")
    print()

    print("[뉴스 응답]")
    kakao_news = chatbot.format_news_for_kakao(news)
    print(json.dumps(kakao_news, ensure_ascii=False, indent=2)[:800] + "...")
    print()

    print("=" * 60)
    print("✅ 테스트 완료")
    print("=" * 60)
