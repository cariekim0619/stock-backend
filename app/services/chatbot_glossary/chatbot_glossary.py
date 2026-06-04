"""
Chatbot_03 주식 용어 사전 API
챗봇 기획에 맞춘 용어 검색 및 카카오톡 응답 포맷

기획:
- 4개 카테고리 + 용어 직접 입력
- KB(glossary.json) 기반 검색 + RAG(LLM) 포맷 재구성
- 고정 출력 포맷: ➊정의 ➋언제쓰이나요 ➌예시 ➍주의할점 ➎헷갈리기쉬운용어
- 검색 결과 분기: 1개 매칭 / 여러 의미 / 검색 실패
"""

import os
import re
from typing import Dict, List, Optional
from app.services.segment_personalization import build_prompt_suffix
from dotenv import load_dotenv

load_dotenv()


class ChatbotGlossary:
    """
    Chatbot_03 주식 용어 사전 데이터 프로바이더

    기능:
    - search_and_explain(): 용어 검색 + RAG 설명 생성
    - format_entry_for_kakao(): 기능 진입 안내
    - format_category_for_kakao(): 카테고리별 용어 목록
    - format_explanation_for_kakao(): 용어 설명 카카오 응답
    - format_disambiguate_for_kakao(): 여러 의미 선택 카카오 응답
    - format_not_found_for_kakao(): 검색 실패 카카오 응답
    """

    # 카카오 simpleText 최대 글자 수를 고려한 안전 길이
    # 카카오 말풍선이 잘리지 않도록 EC2 원문부터 짧게 만든다.
    # Lambda가 다시 2~3개 말풍선으로 나누지만, 각 섹션 자체가 길면 모바일에서 잘린다.
    MAX_PROMPT_RESPONSE_CHARS = 560
    MAX_FINAL_RESPONSE_CHARS = 760
    MAX_DEFINITION_CHARS = 150
    MAX_USAGE_CHARS = 80
    MAX_EXAMPLE_CHARS = 80
    MAX_CAUTION_CHARS = 80
    MAX_RELATED_DESC_CHARS = 42

    # 기획안 카테고리별 대표 용어
    CATEGORIES = {
        "지표 · 숫자 용어": [
            "PER", "PBR", "ROE", "EPS", "배당수익률", "시가총액",
        ],
        "매수 · 매도 관련 용어": [
            "매수", "매도", "호가", "시장가", "분할매수", "물타기",
        ],
        "손익 · 수익률 관련": [
            "실현손익", "평가손익", "수익률", "손절", "익절", "평단가",
        ],
        "차트 · 기술적 용어": [
            "캔들차트", "이동평균", "거래량", "지지선", "RSI", "볼린저밴드",
        ],
        "투자상품": [
            "ETF", "레버리지", "레버리지ETF", "인버스ETF", "ELW", "파생상품",
        ],
    }

    # 퀵 버튼 표시용 라벨 (검색키 → 표시명)
    DISPLAY_LABELS = {
        "시장가": "시장가 / 지정가",
        "이동평균": "이동평균선(MA)",
        "지지선": "지지선 / 저항선",
        "레버리지ETF": "레버리지 ETF",
        "인버스ETF": "인버스 ETF",
        "ELW": "ELW",
    }

    # 모호하거나 검색 실패한 입력에서 보여줄 안전한 주목 용어
    # 짧은 입력값으로 glossary.json 전체를 유사 검색하면 전문/제도 용어가 노출되기 쉬워
    # 사용자에게 익숙한 핵심 용어만 고정 추천한다.
    TRENDING_TERMS = [
        "PER", "PBR", "ROE", "RSI", "이동평균", "거래량",
        "손절", "익절", "물타기", "평단가", "레버리지", "ETF",
    ]

    def __init__(self):
        """Initialize"""
        from app.services.chatbot_glossary.glossary_api import GlossaryAPI
        self.glossary = GlossaryAPI()

        # Gemini (LLM) - RAG용
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
    # 메인 API
    # ========================================

    def search_and_explain(self, user_input: str, segment: str = "risk-neutral", profile: Optional[Dict] = None) -> Dict:
        """
        용어 검색 + 설명 생성 (메인 API)

        Args:
            user_input: 사용자 입력 (단어 또는 문장)

        Returns:
            {
                "status": "found" | "multiple" | "not_found",
                "term": "PER",                  # found인 경우
                "explanation": "📖 PER...",     # found인 경우
                "kb_data": {...},               # found인 경우 원본 KB 데이터
                "candidates": [...],            # multiple인 경우
            }
        """
        query = user_input.strip()

        # 빈 입력 방지
        if not query:
            return {"status": "not_found"}

        query_norm = re.sub(r"[^0-9A-Za-z가-힣]", "", query).strip()

        # 한 글자 입력은 긴 전문용어와 엉뚱하게 매칭되기 쉬우므로 추천/모호성 플로우로 보내지 않는다.
        # 예: 사용자가 I를 입력했을 때 ELW의 영문명 Equity Linked Warrant로 자동 연결되면 안 된다.
        if not self._allow_exact_short_query(query):
            return {"status": "not_found"}

        # 1. 정확 검색
        entry = self.glossary.lookup(query)
        if entry:
            explanation = self._generate_explanation(entry, segment=segment, profile=profile)
            return {
                "status": "found",
                "term": entry["term"],
                "explanation": explanation,
                "kb_data": entry,
            }

        # 2. 주목 용어 prefix 우선 보정
        # 예: "레버"는 긴 전문용어인 레버리지비율보다 일반 사용자가 기대하는 "레버리지"로 연결한다.
        curated_matches = []
        for term in self.TRENDING_TERMS:
            term_norm = re.sub(r"[^0-9A-Za-z가-힣]", "", term).strip().lower()
            if query_norm.lower() and term_norm.startswith(query_norm.lower()):
                entry = self.glossary.lookup(term)
                if entry:
                    curated_matches.append((len(term_norm), term, entry))
        if curated_matches:
            _, _, entry = sorted(curated_matches, key=lambda x: (x[0], x[1]))[0]
            explanation = self._generate_explanation(entry, segment=segment, profile=profile)
            return {
                "status": "found",
                "term": entry["term"],
                "explanation": explanation,
                "kb_data": entry,
            }

        # 3. 문장형 질문에서 핵심 용어 추출
        extracted = self._extract_term_from_sentence(query)
        if extracted:
            entry = self.glossary.lookup(extracted)
            if entry:
                explanation = self._generate_explanation(entry, segment=segment, profile=profile)
                return {
                    "status": "found",
                    "term": entry["term"],
                    "explanation": explanation,
                    "kb_data": entry,
                }

        # 4. 유사 검색
        similar = self.glossary.find_similar(query, limit=5)
        if similar:
            # 상위 1개가 일반 투자자가 자주 쓰는 핵심 용어이고 prefix가 명확할 때만 바로 설명한다.
            # 예: "볼린저" → "볼린저밴드".
            # 예외적으로 "시스템" → "시스템적 중요 금융회사" 같은 제도/전문용어 자동 진입은 막는다.
            top = similar[0]
            qn = re.sub(r"[^0-9A-Za-z가-힣]", "", query).lower()
            tn = re.sub(r"[^0-9A-Za-z가-힣]", "", top.get("term", "")).lower()
            if qn and tn.startswith(qn) and self._is_curated_term(top.get("term", "")):
                top_entry = self.glossary.lookup(top["term"])
                if top_entry:
                    explanation = self._generate_explanation(top_entry, segment=segment, profile=profile)
                    return {
                        "status": "found",
                        "term": top_entry["term"],
                        "explanation": explanation,
                        "kb_data": top_entry,
                    }

            # 여러 후보는 후보 원문을 노출하지 않고 format_disambiguate_for_kakao에서 주목 용어로 대체한다.
            return {
                "status": "multiple",
                "candidates": similar[:4],
            }

        # 5. 못 찾음
        return {"status": "not_found"}

    def get_category_terms(self, category_name: str) -> Optional[List[str]]:
        """카테고리별 용어 목록 반환"""
        return self.CATEGORIES.get(category_name)

    def _compact_text(self, value: str, limit: int, *, ellipsis: bool = True) -> str:
        """사용자에게 보여줄 한 항목을 문장/어절 기준으로 짧게 정리한다."""
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            return ""
        if len(text) <= limit:
            return text

        # 문장 경계가 앞쪽에 있으면 거기서 끊는다.
        for sep in (". ", "다. ", "요. ", "! ", "? "):
            idx = text.find(sep)
            if 20 <= idx + len(sep) <= limit:
                return text[:idx + len(sep)].strip()

        cut = text[:limit].rstrip()
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0].rstrip()
        if not cut:
            cut = text[:limit].rstrip()
        return cut

    _RELATED_SHORT_DESCRIPTIONS = {
        "ROE": "자기자본으로 이익을 얼마나 냈는지 보는 지표",
        "총자산": "기업이 보유한 전체 자산",
        "옵션": "정해진 가격으로 살 권리 또는 팔 권리",
        "파생상품": "기초자산 가격에 따라 가치가 변하는 상품",
        "이동평균": "일정 기간 주가의 평균선",
        "이동평균선": "일정 기간 주가의 평균선",
        "캔들차트": "시가·고가·저가·종가를 봉으로 나타낸 차트",
        "표준편차": "값이 평균에서 얼마나 흩어져 있는지 나타내는 지표",
        "ETF": "지수나 자산을 따라가도록 만든 상장 펀드",
        "레버리지": "차입·배율로 손익 변동폭을 키우는 방식",
        "손절": "손실 확대를 막기 위해 매도하는 것",
        "평단가": "여러 번 매수한 평균 매입 가격",
    }

    def _compact_related_desc(self, value: str, term: str = "") -> str:
        term = str(term or "").strip()
        if term in self._RELATED_SHORT_DESCRIPTIONS:
            return self._RELATED_SHORT_DESCRIPTIONS[term]
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            return ""
        # 긴 설명을 억지로 말줄임표로 자르지 않는다. 들어맞는 짧은 문장만 쓰고, 아니면 설명을 생략한다.
        short = self._compact_text(text, self.MAX_RELATED_DESC_CHARS, ellipsis=False)
        return short if len(short) <= self.MAX_RELATED_DESC_CHARS else ""

    def _is_curated_term(self, term: str) -> bool:
        curated = set(self.TRENDING_TERMS)
        for terms in self.CATEGORIES.values():
            curated.update(terms)
        return str(term or "").strip() in curated

    def _allow_exact_short_query(self, query: str) -> bool:
        """한 글자 입력은 자동 매칭하지 않는다. 단, 실제 사전에 있는 한글 1글자 용어만 예외 허용."""
        raw = str(query or "").strip()
        norm = re.sub(r"[^0-9A-Za-z가-힣]", "", raw).strip()
        if len(norm) >= 2 or re.fullmatch(r"[A-Za-z]{2,}", raw):
            return True
        # 영문 한 글자는 I/l 같은 오입력으로 ELW 등과 엉뚱하게 매칭되기 쉬워 금지
        if re.fullmatch(r"[A-Za-z]", raw):
            return False
        return raw in self.glossary.get_all_terms()

    # ========================================
    # 공통 유틸
    # ========================================

    def _safe_truncate_text(self, text: str, max_len: int = None) -> str:
        """
        카카오톡 메시지 길이 초과 방지용 후처리
        너무 길면 잘라서 말줄임 처리
        """
        if max_len is None:
            max_len = self.MAX_FINAL_RESPONSE_CHARS

        if not text:
            return text

        if len(text) <= max_len:
            return text

        # 너무 길면 자연스럽게 잘라내기
        trimmed = text[:max_len].rstrip()

        # 마지막 줄이 어중간하게 끊기면 정리
        if "\n" in trimmed:
            last_newline = trimmed.rfind("\n")
            if last_newline > max_len - 80:
                trimmed = trimmed[:last_newline].rstrip()

        return trimmed

    def _clean_llm_text(self, text: str) -> str:
        """
        LLM 응답 후처리
        - markdown bold 제거
        - 불필요한 공백 정리
        """
        if not text:
            return text

        # markdown bold 제거
        text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)

        # 줄 끝 공백 제거
        text = "\n".join(line.rstrip() for line in text.splitlines())

        return text.strip()

    # ========================================
    # RAG: 용어 설명 생성
    # ========================================

    def _generate_explanation(self, entry: Dict, segment: str = "risk-neutral", profile: Optional[Dict] = None) -> str:
        """
        KB 데이터 → 기획안 고정 포맷으로 변환 (RAG)

        출력 포맷:
        📖 [용어명]에 대한 설명이에요.

        ➊ 정의
        - ...

        ➋ 언제 쓰이나요 ?
        - ...

        ➌ 예시
        - ...

        ➍ 주의할 점
        - ...

        ➎ 헷갈리기 쉬운 용어
        - A : ...
        - B : ...
        """
        # 운영 기본값은 KB 기반 deterministic 포맷이다.
        # LLM이 간혹 ➋/➍ 제목을 바꾸거나 긴 문장을 만들어 카카오 말풍선에서 잘리는 문제가 있어
        # 명시적으로 GLOSSARY_USE_LLM=1일 때만 사용한다.
        if os.getenv("GLOSSARY_USE_LLM", "0").strip() == "1" and self.genai:
            llm_text = self._rag_explanation(entry, segment=segment, profile=profile)
            if llm_text:
                return llm_text
        return self._fallback_explanation(entry)

    def _rag_explanation(self, entry: Dict, segment: str = "risk-neutral", profile: Optional[Dict] = None) -> Optional[str]:
        """LLM 기반 RAG 설명 생성"""
        term = entry.get("term", "")
        full_name = entry.get("full_name", "")
        description = self._compact_text(entry.get("description", ""), self.MAX_DEFINITION_CHARS)
        formula = self._compact_text(entry.get("formula", ""), 80)
        example = self._compact_text(entry.get("example", ""), self.MAX_EXAMPLE_CHARS)
        interpretation = entry.get("interpretation", {})
        related_terms = entry.get("related_terms", [])

        # KB 데이터 조합
        kb_text = f"용어: {term}"
        if full_name and full_name != term:
            kb_text += f" ({full_name})"
        kb_text += f"\n정의: {description}"

        if formula:
            kb_text += f"\n공식: {formula}"

        if example:
            kb_text += f"\n예시: {example}"

        if interpretation:
            kb_text += "\n해석:"
            for k, v in interpretation.items():
                kb_text += f"\n  - {k}: {v}"

        # 연관 용어 정보
        related_info = []
        for rt in related_terms[:2]:
            rt_entry = self.glossary.lookup(rt)
            if rt_entry:
                rt_desc = self._compact_related_desc(rt_entry.get("description", ""), rt)
                related_info.append(
                    f"{rt} ({rt_entry.get('full_name', '')}): {rt_desc}"
                )
            else:
                related_info.append(rt)

        rt_1 = related_terms[0] if len(related_terms) > 0 else "A"
        rt_2 = related_terms[1] if len(related_terms) > 1 else "B"

        prompt = f"""아래 용어 사전 데이터를 바탕으로 주식 초보자도 이해할 수 있는 설명을 작성해주세요.

[용어 사전 데이터]
{kb_text}

[연관 용어]
{chr(10).join(related_info) if related_info else '없음'}

[출력 형식 — 반드시 아래 형식 그대로 따라주세요]
➊ 정의
- (객관적 정의 1문장. 공식이 있으면 포함)

➋ 언제 쓰이나요 ?
- (대표 사용 상황 1문장)

➌ 예시
- (구체적인 예시 1문장)

➍ 주의할 점
- (오해하기 쉬운 포인트 1문장)

➎ 헷갈리기 쉬운 용어
- {rt_1} : (차이점 1문장, 45자 이내)
- {rt_2} : (차이점 1문장, 45자 이내)

[규칙]
- 위에 제공된 용어 사전 데이터만 근거로 작성하세요
- 새로운 정의를 만들지 마세요
- 투자 판단이나 의견을 제시하지 마세요
- 쉽고 친근한 말투를 사용하세요
- 각 항목은 1문장으로 간결하게 작성하세요
- 전체 답변은 {self.MAX_PROMPT_RESPONSE_CHARS}자 이내로 작성하세요
- 카카오톡 메시지 길이 제한을 고려하여 불필요하게 길게 쓰지 마세요""" + build_prompt_suffix(segment, domain="glossary", profile=profile)

        try:
            model = self.genai.GenerativeModel(
                "gemini-2.5-flash",
                system_instruction=(
                    "당신은 주식 용어 사전 도우미입니다. "
                    "검증된 용어 데이터를 바탕으로 초보자에게 쉽게 설명합니다. "
                    "새로운 정의를 만들거나 투자 판단을 제시하지 않습니다. "
                    f"전체 답변은 반드시 {self.MAX_PROMPT_RESPONSE_CHARS}자 이내로 작성합니다. "
                    "출력 형식은 ➊정의 ➋언제 쓰이나요 ➌예시 ➍주의할 점 ➎헷갈리기 쉬운 용어 형식을 유지합니다."
                )
            )

            response = model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.2,
                    "max_output_tokens": 320,
                }
            )

            text = self._clean_llm_text(response.text)

            # 포맷 검증
            if "➊" in text and "➋" in text:
                display_name = term
                if full_name and full_name != term:
                    display_name = f"{term} ({full_name})"

                final_text = f"📖 {display_name}에 대한 설명이에요.\n\n{text}"
                return self._safe_truncate_text(final_text)

            return None

        except Exception:
            return None

    def _fallback_explanation(self, entry: Dict) -> str:
        """LLM 불가 시 KB 데이터 직접 포맷"""
        term = entry.get("term", "")
        full_name = entry.get("full_name", "")
        description = self._compact_text(entry.get("description", ""), self.MAX_DEFINITION_CHARS)
        formula = self._compact_text(entry.get("formula", ""), 80)
        example = self._compact_text(entry.get("example", ""), self.MAX_EXAMPLE_CHARS)
        interpretation = entry.get("interpretation", {})
        related_terms = entry.get("related_terms", [])

        display_name = term
        if full_name and full_name != term:
            display_name = f"{term} ({full_name})"

        text = f"📖 {display_name}에 대한 설명이에요.\n\n"

        # ➊ 정의
        definition = description
        if formula:
            definition += f"\n  {formula}"
        text += f"➊ 정의\n- {definition}\n\n"

        # ➋ 언제 쓰이나요
        usage = ""
        for key in ["use", "benchmark", "trading"]:
            if key in interpretation:
                usage = interpretation[key]
                break

        if not usage and interpretation:
            first_key = list(interpretation.keys())[0]
            usage = interpretation[first_key]

        if not usage:
            usage = f"{term}은(는) 주식 분석에서 자주 사용되는 용어입니다."
        usage = self._compact_text(usage, self.MAX_USAGE_CHARS)

        text += f"➋ 언제 쓰이나요 ?\n- {usage}\n\n"

        # ➌ 예시
        if example:
            text += f"➌ 예시\n- {example}\n\n"
        else:
            text += "➌ 예시\n- (예시 준비 중)\n\n"

        # ➍ 주의할 점
        caution = ""
        for key in ["caution", "risk", "note", "warning", "danger"]:
            if key in interpretation:
                caution = interpretation[key]
                break

        if not caution:
            caution = "업종이나 시장 상황에 따라 해석이 달라질 수 있어요."
        caution = self._compact_text(caution, self.MAX_CAUTION_CHARS)

        text += f"➍ 주의할 점\n- {caution}\n\n"

        # ➎ 헷갈리기 쉬운 용어
        text += "➎ 헷갈리기 쉬운 용어\n"
        if related_terms:
            for rt in related_terms[:2]:
                rt_entry = self.glossary.lookup(rt)
                if rt_entry:
                    rt_desc = (rt_entry.get("description", "") or "").strip()
                    rt_desc = self._compact_related_desc(rt_desc, rt)
                    text += f"- {rt} : {rt_desc}\n" if rt_desc else f"- {rt}\n"
                else:
                    text += f"- {rt}\n"
        else:
            text += "- (연관 용어 없음)\n"

        return text.rstrip()

    def _extract_term_from_sentence(self, sentence: str) -> Optional[str]:
        """문장에서 핵심 용어 추출"""
        sentence_norm = re.sub(r"[^0-9A-Za-z가-힣]", "", sentence or "").strip()
        if len(sentence_norm) < 2 and not re.fullmatch(r"[A-Za-z]{2,}", (sentence or "").strip()):
            return None

        # 1. 영문 약어 추출 (PER, RSI 등)
        eng_matches = re.findall(r"[A-Za-z]{2,}", sentence)
        for m in eng_matches:
            if self.glossary.lookup(m):
                return m

        # 2. KB 용어와 직접 매칭 (긴 것부터)
        all_terms = self.glossary.get_all_terms()
        for t in sorted(all_terms, key=len, reverse=True):
            if t in sentence:
                return t

        # 3. LLM 추출
        if self.genai:
            try:
                model = self.genai.GenerativeModel("gemini-2.5-flash")
                prompt = (
                    "다음 문장에서 주식 관련 핵심 용어 1개만 추출하세요. "
                    "용어만 출력하세요.\n\n"
                    f'"{sentence}"'
                )
                response = model.generate_content(
                    prompt,
                    generation_config={"temperature": 0, "max_output_tokens": 50}
                )
                extracted = response.text.strip()
                if extracted and len(extracted) < 20:
                    return extracted
            except Exception:
                pass

        return None

    # ========================================
    # 카카오톡 포맷
    # ========================================

    def _quick_reply_for_term(self, term: str) -> Dict:
        label = self.DISPLAY_LABELS.get(term, term)
        if len(label) > 20:
            label = label[:17] + "..."
        return {
            "action": "block",
            "label": label,
            "messageText": term,
            "blockId": "glossary_term_block",
        }

    def _trending_quick_replies(self, limit: int = 6) -> List[Dict]:
        out = []
        seen = set()
        for term in self.TRENDING_TERMS:
            if term in seen:
                continue
            if not self.glossary.lookup(term):
                continue
            out.append(self._quick_reply_for_term(term))
            seen.add(term)
            if len(out) >= limit:
                break
        return out

    def format_entry_for_kakao(self) -> Dict:
        """
        기능 진입 안내 카카오 응답

        기획: 주식 용어 사전 버튼 클릭 시
        """
        return {
            "version": "2.0",
            "template": {
                "outputs": [{
                    "simpleText": {
                        "text": (
                            "📖 주식 용어 사전 입니다 :)\n"
                            "아래에 있는 내용을 담아 용어를 설명해요.\n\n"
                            "• 정확한 정의\n"
                            "• 언제 쓰이는지\n"
                            "• 헷갈리기 쉬운 포인트\n\n"
                            "하단의 버튼을 누르거나,\n"
                            "궁금한 용어를 채팅창에 바로 입력해 주세요 !"
                        )
                    }
                }],
                "quickReplies": [
                    {
                        "action": "block",
                        "label": "지표 · 숫자 용어",
                        "messageText": "지표 · 숫자 용어",
                        "blockId": "glossary_category_block",
                    },
                    {
                        "action": "block",
                        "label": "매수 · 매도 관련 용어",
                        "messageText": "매수 · 매도 관련 용어",
                        "blockId": "glossary_category_block",
                    },
                    {
                        "action": "block",
                        "label": "손익 · 수익률 관련",
                        "messageText": "손익 · 수익률 관련",
                        "blockId": "glossary_category_block",
                    },
                    {
                        "action": "block",
                        "label": "차트 · 기술적 용어",
                        "messageText": "차트 · 기술적 용어",
                        "blockId": "glossary_category_block",
                    },
                    {
                        "action": "block",
                        "label": "메인으로",
                        "messageText": "메인으로",
                        "blockId": "main_block",
                    },
                ],
            },
        }

    def format_category_for_kakao(self, category_name: str) -> Dict:
        """
        카테고리별 용어 목록 카카오 응답

        기획: 카테고리 선택 시 해당 용어 리스트 퀵 버튼
        """
        terms = self.CATEGORIES.get(category_name, [])

        # 카테고리 표시명
        display_category = category_name.replace(" · ", "·")

        quick_replies = []
        for term in terms:
            label = self.DISPLAY_LABELS.get(term, term)
            quick_replies.append({
                "action": "block",
                "label": label,
                "messageText": term,
                "blockId": "glossary_term_block",
            })

        quick_replies.append({
            "action": "block",
            "label": "다른 카테고리 보기",
            "messageText": "다른 카테고리",
            "blockId": "glossary_entry_block",
        })
        quick_replies.append({
            "action": "block",
            "label": "종료",
            "messageText": "종료",
            "blockId": "end_block",
        })

        return {
            "version": "2.0",
            "template": {
                "outputs": [{
                    "simpleText": {
                        "text": (
                            f"📖 {display_category} 중\n"
                            "어떤 개념이 궁금하신가요?\n\n"
                            "하단에 6개의 예시 단어가 있어요.\n"
                            "버튼을 눌러 용어를 확인하거나,\n"
                            "용어를 직접 입력해주세요 !"
                        )
                    }
                }],
                "quickReplies": quick_replies,
            },
        }

    def format_explanation_for_kakao(self, result: Dict) -> Dict:
        """
        용어 설명 카카오 응답

        기획: 용어 설명 후 [다른 용어 질문 / 종료] 퀵 버튼
        """
        return {
            "version": "2.0",
            "template": {
                "outputs": [{
                    "simpleText": {
                        "text": self._safe_truncate_text(result["explanation"])
                    }
                }],
                "quickReplies": self._trending_quick_replies(limit=6) + [
                    {
                        "action": "block",
                        "label": "다른 카테고리 보기",
                        "messageText": "다른 카테고리",
                        "blockId": "glossary_entry_block",
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

    def format_disambiguate_for_kakao(self, candidates: List[Dict]) -> Dict:
        """
        여러 의미 선택 플로우는 사용자 경험상 혼란이 커서 더 이상 후보를 그대로 노출하지 않는다.
        대신 많이 쓰는 주목 용어를 제안하고, 사용자가 원하는 용어는 채팅창에 바로 입력하게 한다.
        """
        return {
            "version": "2.0",
            "template": {
                "outputs": [{
                    "simpleText": {
                        "text": (
                            "입력하신 표현과 정확히 일치하는 용어를 찾지 못했어요.\n\n"
                            "아래 주목받는 용어를 눌러 확인하거나,\n"
                            "궁금한 용어를 채팅창에 바로 입력해 주세요."
                        )
                    }
                }],
                "quickReplies": self._trending_quick_replies(limit=8) + [
                    {
                        "action": "block",
                        "label": "다른 카테고리 보기",
                        "messageText": "다른 카테고리",
                        "blockId": "glossary_entry_block",
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

    def format_not_found_for_kakao(self) -> Dict:
        """
        검색 실패 카카오 응답
        - 직접 입력 퀵버튼을 노출하지 않는다.
        - 이상한 유사 후보 대신 검증된 주목 용어를 추천한다.
        """
        return {
            "version": "2.0",
            "template": {
                "outputs": [{
                    "simpleText": {
                        "text": (
                            "⚠️ 해당 용어를 사전에서 찾지 못했어요.\n\n"
                            "아래 주목받는 용어를 눌러 확인하거나,\n"
                            "궁금한 용어를 채팅창에 바로 입력해 주세요."
                        )
                    }
                }],
                "quickReplies": self._trending_quick_replies(limit=8) + [
                    {
                        "action": "block",
                        "label": "지표·숫자 용어",
                        "messageText": "지표 · 숫자 용어",
                        "blockId": "glossary_category_block",
                    },
                    {
                        "action": "block",
                        "label": "거래 관련 용어",
                        "messageText": "매수 · 매도 관련 용어",
                        "blockId": "glossary_category_block",
                    },
                    {
                        "action": "block",
                        "label": "차트 · 기술",
                        "messageText": "차트 · 기술적 용어",
                        "blockId": "glossary_category_block",
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


# ========================================
# 테스트
# ========================================

if __name__ == "__main__":
    import json

    print("=" * 60)
    print("Chatbot_03 주식 용어 사전 테스트")
    print("=" * 60)
    print()

    chatbot = ChatbotGlossary()
    print(f"KB 용어 수: {chatbot.glossary.get_term_count()}개")
    print()

    # 1. 진입 메시지
    print("[1단계] 기능 진입")
    print("-" * 40)
    entry_resp = chatbot.format_entry_for_kakao()
    print(entry_resp["template"]["outputs"][0]["simpleText"]["text"])
    print(f"퀵 버튼: {[q['label'] for q in entry_resp['template']['quickReplies']]}")
    print()

    # 2. 카테고리 선택
    print("[2단계] 카테고리 선택: 지표 · 숫자 용어")
    print("-" * 40)
    cat_resp = chatbot.format_category_for_kakao("지표 · 숫자 용어")
    print(cat_resp["template"]["outputs"][0]["simpleText"]["text"])
    print(f"퀵 버튼: {[q['label'] for q in cat_resp['template']['quickReplies']]}")
    print()

    # 3. 용어 검색 - 정확 매칭
    print("[3단계] 용어 검색: PER")
    print("-" * 40)
    result = chatbot.search_and_explain("PER")
    print(f"상태: {result['status']}")
    if result["status"] == "found":
        print(result["explanation"][:300] + "...")
    print()

    # 4. 문장형 질문
    print("[4단계] 문장형 질문: PER이 높으면 무슨 뜻이야?")
    print("-" * 40)
    result = chatbot.search_and_explain("PER이 높으면 무슨 뜻이야?")
    print(f"상태: {result['status']}")
    if result["status"] == "found":
        print(f"매칭 용어: {result['term']}")
    print()

    # 5. 유사 검색
    print("[5단계] 유사 검색: 이익")
    print("-" * 40)
    result = chatbot.search_and_explain("이익")
    print(f"상태: {result['status']}")
    if result["status"] == "multiple":
        for c in result["candidates"]:
            print(f"  - {c['term']} ({c.get('full_name', '')})")
    print()

    # 6. 검색 실패
    print("[6단계] 검색 실패: 존재하지않는용어")
    print("-" * 40)
    result = chatbot.search_and_explain("존재하지않는용어")
    print(f"상태: {result['status']}")
    print()

    # 7. 카카오 응답 형식 확인
    print("[7단계] 카카오 응답 형식")
    print("-" * 40)
    result = chatbot.search_and_explain("물타기")
    if result["status"] == "found":
        kakao = chatbot.format_explanation_for_kakao(result)
        print(json.dumps(kakao, ensure_ascii=False, indent=2)[:600] + "...")
    print()

    # 8. 못 찾음 카카오 응답
    print("[8단계] 못 찾음 카카오 응답")
    print("-" * 40)
    not_found = chatbot.format_not_found_for_kakao()
    print(json.dumps(not_found, ensure_ascii=False, indent=2)[:400])
    print()

    print("=" * 60)
    print("테스트 완료")
    print("=" * 60)
