import os
import re
from typing import Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()


# ============================================================
# Lambda 명령 상수와 완전 일치 — _normalize_glossary_payload 무력화
# ============================================================
# Lambda 측 정의(live_lambda_function_patched_v4.py 라인 1535~1537):
#   _DICT_INPUT_CMD          = "용어 직접 입력"
#   _DICT_OTHER_TERM_CMD     = "다른 용어 질문"
#   _DICT_OTHER_CATEGORY_CMD = "다른 카테고리 보기"
#   CMD_MAIN                 = "메인으로"
# 본 EC2 코드는 이 라벨을 그대로 사용함.
CMD_INPUT = "용어 직접 입력"
CMD_OTHER_TERM = "다른 용어 질문"
CMD_OTHER_CATEGORY = "다른 카테고리 보기"
CMD_MAIN = "메인으로"


class ChatbotGlossary:
    """
    Chatbot_03 주식 용어 사전 데이터 프로바이더 (v6 일원화)

    기능:
    - search_and_explain(): 용어 검색 + RAG 설명 생성
    - format_entry_for_kakao(): 기능 진입 안내
    - format_category_for_kakao(): 카테고리별 용어 목록
    - format_explanation_for_kakao(): 용어 설명 카카오톡 응답 (2말풍선 분할)
    - format_disambiguate_for_kakao(): 여러 의미 선택 카카오톡 응답
    - format_not_found_for_kakao(): 검색 실패 카카오톡 응답
    """

    # 카카오톡 simpleText 1개당 안전 길이 (실제 한도는 1,000자, 안전마진 20자)
    SIMPLE_TEXT_MAX = 980

    # 풍선 1개 prompt 응답 가이드 (LLM 사용 시)
    MAX_PROMPT_RESPONSE_CHARS = 700

    # 분할 마커
    SECTION_MARKERS = ("➊", "➋", "➌", "➍", "➎")
    SPLIT_MARKER = "➍"

    # multiple 분기 신뢰도 임계치 (Stage 1 v5와 동일)
    MULTIPLE_SCORE_THRESHOLD = 8

    # outputs 최대 개수 (카카오 i 제약: 3)
    MAX_OUTPUTS = 3

    # 카테고리별 대표 용어
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
    }

    # 톡 버튼 표시용 라벨
    DISPLAY_LABELS = {
        "시장가": "시장가 / 지정가",
        "이동평균": "이동평균선(MA)",
        "지지선": "지지선 / 저항선",
    }

    # 결과 화면 안내 문구 (Lambda _append_glossary_result_guides 대체)
    RESULT_GUIDE_TEXT = (
        "궁금한 용어를 다시 입력해 주세요.\n"
        "다른 용어도 질문해보세요."
    )

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

    # ====================================================
    # 메인 API
    # ====================================================

    def search_and_explain(self, user_input: str) -> Dict:
        """v5와 동일 — 알고리즘 변경 없음"""
        query = user_input.strip()
        if not query:
            return {"status": "not_found"}

        # 1. 정확 검색
        entry = self.glossary.lookup(query)
        if entry:
            explanation = self._generate_explanation(entry)
            return {
                "status": "found",
                "term": entry["term"],
                "explanation": explanation,
                "kb_data": entry,
            }

        # 2. 문장형 질문에서 핵심 용어 추출
        extracted = self._extract_term_from_sentence(query)
        if extracted:
            entry = self.glossary.lookup(extracted)
            if entry:
                explanation = self._generate_explanation(entry)
                return {
                    "status": "found",
                    "term": entry["term"],
                    "explanation": explanation,
                    "kb_data": entry,
                }

        # 3. 유사 검색
        similar = self._find_similar_with_score(query, limit=5)
        if similar:
            top = similar[0]
            top_score = top.get("score", 0)

            if (
                query.lower() in top["term"].lower()
                or top["term"].lower() in query.lower()
            ):
                top_entry = self.glossary.lookup(top["term"])
                if top_entry:
                    explanation = self._generate_explanation(top_entry)
                    return {
                        "status": "found",
                        "term": top_entry["term"],
                        "explanation": explanation,
                        "kb_data": top_entry,
                    }

            if top_score >= self.MULTIPLE_SCORE_THRESHOLD:
                candidates = []
                for r in similar[:4]:
                    candidates.append({
                        "term": r["term"],
                        "full_name": r.get("full_name", ""),
                        "category": r.get("category", ""),
                    })
                return {
                    "status": "multiple",
                    "candidates": candidates,
                }

            return {"status": "not_found"}

        return {"status": "not_found"}

    def get_category_terms(self, category_name: str) -> Optional[List[str]]:
        return self.CATEGORIES.get(category_name)

    # ====================================================
    # 공통 유틸 (v5와 동일)
    # ====================================================

    def _find_similar_with_score(self, query: str, limit: int = 5) -> List[Dict]:
        results = []
        query_lower = query.lower()

        for key, value in self.glossary._data.items():
            score = 0
            full_name = value.get("full_name", "")
            english = value.get("english", "")
            description = value.get("description", "")

            key_lower = key.lower()
            full_lower = full_name.lower()
            eng_lower = english.lower()
            desc_lower = description.lower()

            if key_lower and (query_lower in key_lower or key_lower in query_lower):
                score += 10
            if full_lower and (query_lower in full_lower or full_lower in query_lower):
                score += 8
            if eng_lower and (query_lower in eng_lower or eng_lower in query_lower):
                score += 6
            if desc_lower and query_lower in desc_lower:
                score += 3

            if score > 0:
                results.append({
                    "term": key,
                    "full_name": full_name,
                    "category": value.get("category", ""),
                    "score": score,
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def _safe_truncate_text(self, text: str, max_len: int = None) -> str:
        if max_len is None:
            max_len = self.SIMPLE_TEXT_MAX

        if not text:
            return text

        if len(text) <= max_len:
            return text

        trimmed = text[:max_len].rstrip()

        if "\n" in trimmed:
            last_newline = trimmed.rfind("\n")
            if last_newline > max_len - 80:
                trimmed = trimmed[:last_newline].rstrip()

        return trimmed + "\n…"

    def _truncate_at_word_boundary(self, text: str, max_len: int) -> str:
        """
        v7 신규: rt_desc 같은 한 줄 설명을 자연스러운 종결로 자름.
        - 길이가 max_len 이하면 그대로 반환
        - 초과 시 max_len 안에서 가장 가까운 종결 부호(., 다, 요, 음, 임) 위치에서 자름
        - 적절한 종결점이 없으면 max_len에서 자르고 "..." 부착
        """
        if not text:
            return text
        s = text.strip()
        if len(s) <= max_len:
            return s

        # 종결 부호 후보 — 한국어 문장 종결 우선
        candidates = ["다.", "요.", "음.", "임.", "다", "요", "."]
        window = s[:max_len]
        best_pos = -1
        for marker in candidates:
            pos = window.rfind(marker)
            if pos > best_pos:
                best_pos = pos + len(marker)
        # max_len의 60% 이전에서 잘리면 너무 짧으니 그냥 max_len에서 자름
        if best_pos >= int(max_len * 0.6):
            return s[:best_pos].rstrip()
        return s[:max_len].rstrip() + "..."

    def _split_explanation_by_marker(self, text: str) -> tuple:
        if not text:
            return text, ""

        idx = text.find(self.SPLIT_MARKER)
        if idx < 0:
            return text, ""

        head = text[:idx].rstrip()
        body = text[idx:].strip()

        if len(head) < 30 or len(body) < 30:
            return text, ""

        return head, body

    def _clean_llm_text(self, text: str) -> str:
        if not text:
            return text
        text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
        text = "\n".join(line.rstrip() for line in text.splitlines())
        return text.strip()

    def _append_result_guide(self, text: str) -> str:
        """
        결과 화면 마지막 simpleText에 안내 문구 부착.
        Lambda _append_glossary_result_guides 의 대체.
        """
        base = (text or "").rstrip()
        guide = self.RESULT_GUIDE_TEXT
        # 이미 포함되어 있으면 중복 안 함
        if "다른 용어도 질문해보세요" in base or "궁금한 용어를 다시 입력" in base:
            return base
        return self._safe_truncate_text(f"{base}\n\n{guide}", max_len=self.SIMPLE_TEXT_MAX)

    # ====================================================
    # RAG (v5와 동일 — _generate_explanation는 _fallback_explanation만 호출)
    # ====================================================

    def _generate_explanation(self, entry: Dict) -> str:
        return self._fallback_explanation(entry)

    def _fallback_explanation(self, entry: Dict) -> str:
        term = entry.get("term", "")
        full_name = entry.get("full_name", "")
        description = entry.get("description", "")
        formula = entry.get("formula", "")
        example = entry.get("example", "")
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

        # ➋ 어떤 쓰임이나요
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

        text += f"➋ 어떤 쓰임이나요 ?\n- {usage}\n\n"

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

        text += f"➍ 주의할 점\n- {caution}\n\n"

        # ➎ 헷갈리기 쉬운 용어
        text += "➎ 헷갈리기 쉬운 용어\n"
        if related_terms:
            for rt in related_terms[:2]:
                rt_entry = self.glossary.lookup(rt)
                if rt_entry:
                    # v7: 50→80자로 늘리고 자연 종결로 자름 (잘림 방지)
                    rt_desc_full = rt_entry.get("description", "")
                    rt_desc = self._truncate_at_word_boundary(rt_desc_full, max_len=80)
                    text += f"- {rt} : {rt_desc}\n"
                else:
                    text += f"- {rt}\n"
        else:
            text += "- (연관 용어 없음)\n"

        return text.rstrip()

    def _extract_term_from_sentence(self, sentence: str) -> Optional[str]:
        eng_matches = re.findall(r"[A-Za-z]{2,}", sentence)
        for m in eng_matches:
            if self.glossary.lookup(m):
                return m

        all_terms = self.glossary.get_all_terms()
        for t in sorted(all_terms, key=len, reverse=True):
            if t in sentence:
                return t

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

    # ====================================================
    # 카카오톡 포맷 (★ v6 일원화 ★)
    # ====================================================

    def format_entry_for_kakao(self) -> Dict:
        """
        기능 진입 안내 — 4개 카테고리 + 용어 직접 입력 + 메인으로

        Lambda 명령 라벨로 통일:
          - "용어 직접 입력" (CMD_INPUT) → Lambda _DICT_INPUT_CMD와 일치
          - "메인으로" (CMD_MAIN) → Lambda CMD_MAIN과 일치
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
                            "• 어떤 쓰임인지\n"
                            "• 헷갈리기 쉬운 포인트\n\n"
                            "하단의 톡 버튼을 눌러\n"
                            "궁금한 용어를 확인하세요 !"
                        )
                    }
                }],
                "quickReplies": [
                    self._qr("지표 · 숫자 용어", "지표 · 숫자 용어", "glossary_category_block"),
                    self._qr("매수 · 매도 관련 용어", "매수 · 매도 관련 용어", "glossary_category_block"),
                    self._qr("손익 · 수익률 관련", "손익 · 수익률 관련", "glossary_category_block"),
                    self._qr("차트 · 기술적 용어", "차트 · 기술적 용어", "glossary_category_block"),
                    self._qr(CMD_INPUT, CMD_INPUT, "glossary_input_block"),
                    self._qr(CMD_MAIN, CMD_MAIN, "main_block"),
                ],
            },
        }

    def format_category_for_kakao(self, category_name: str) -> Dict:
        """
        카테고리별 용어 목록

        결과 화면 quickReplies — Lambda 명령 라벨로 통일:
          - 용어 6개 (블록 진입)
          - CMD_OTHER_CATEGORY ("다른 카테고리 보기")
          - CMD_MAIN ("메인으로")
          ※ "다시 입력" / "종료" 는 사용하지 않음
        """
        terms = self.CATEGORIES.get(category_name, [])
        display_category = category_name.replace(" · ", "·")

        quick_replies = []
        for term in terms:
            label = self.DISPLAY_LABELS.get(term, term)
            quick_replies.append(self._qr(label, term, "glossary_term_block"))

        quick_replies.append(self._qr(CMD_OTHER_CATEGORY, CMD_OTHER_CATEGORY, "glossary_entry_block"))
        quick_replies.append(self._qr(CMD_MAIN, CMD_MAIN, "main_block"))

        # 결과 화면용 안내 문구 부착 (Lambda _append 대체)
        body_text = (
            f"📖 {display_category} 중\n"
            "어떤 개념이 궁금하신가요?\n\n"
            "하단에 6개의 예시 단어가 있어요.\n"
            "버튼을 눌러 용어를 확인하거나,\n"
            "용어를 직접 입력해주세요 !"
        )

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": self._safe_truncate_text(body_text)}}],
                "quickReplies": quick_replies,
            },
        }

    def format_explanation_for_kakao(self, result: Dict) -> Dict:
        """
        용어 설명 — v7: 3 simpleText 분할

        풍선 구조:
          [1] 📖 헤더 + ➊ 정의 + ➋ 쓰임 + ➌ 예시
          [2] ➍ 주의할 점 + ➎ 헷갈리기 쉬운 용어
          [3] 안내 문구 ("궁금한 용어를 다시 입력해 주세요. 다른 용어도 질문해보세요.")

        quickReplies — Lambda 명령 라벨로 통일:
          - CMD_OTHER_TERM ("다른 용어 질문")
          - CMD_MAIN ("메인으로")
        """
        full_text = result.get("explanation") or ""
        head, body = self._split_explanation_by_marker(full_text)

        outputs = []

        # 풍선 [1]: head (📖 ~ ➌ 예시)
        if head:
            outputs.append({"simpleText": {"text": self._safe_truncate_text(head)}})

        # 풍선 [2]: body (➍ ~ ➎) — 안내문 부착 X
        if body:
            outputs.append({"simpleText": {"text": self._safe_truncate_text(body)}})

        # 풍선 [3]: 안내문 단독
        if outputs:
            outputs.append({"simpleText": {"text": self.RESULT_GUIDE_TEXT}})
        else:
            # 안전망: 분할 실패 시 단일 풍선 + 안내문 한 풍선
            outputs = [
                {"simpleText": {"text": self._safe_truncate_text(full_text)}},
                {"simpleText": {"text": self.RESULT_GUIDE_TEXT}},
            ]

        return {
            "version": "2.0",
            "template": {
                "outputs": outputs[:self.MAX_OUTPUTS],
                "quickReplies": [
                    self._qr(CMD_OTHER_TERM, CMD_OTHER_TERM, "glossary_entry_block"),
                    self._qr(CMD_MAIN, CMD_MAIN, "main_block"),
                ],
            },
        }

    def format_disambiguate_for_kakao(self, candidates: List[Dict]) -> Dict:
        """
        여러 의미 선택

        quickReplies — Lambda 명령 라벨로 통일:
          - 후보 4개 (블록 진입)
          - CMD_INPUT ("용어 직접 입력")
          - CMD_MAIN ("메인으로")
          ※ "다시 입력" 라벨은 v6에서 폐기 (Lambda가 어차피 제거함)
        """
        quick_replies = []
        for c in candidates[:4]:
            term = c["term"]
            full_name = c.get("full_name", "")
            label = f"{term} ({full_name})" if full_name else term
            if len(label) > 20:
                label = label[:17] + "..."
            quick_replies.append(self._qr(label, term, "glossary_term_block"))

        quick_replies.append(self._qr(CMD_INPUT, CMD_INPUT, "glossary_input_block"))
        quick_replies.append(self._qr(CMD_MAIN, CMD_MAIN, "main_block"))

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": (
                    "🔍 비슷한 용어가 여러 개 있어요\n"
                    "아래 중에서 알고 싶은 용어를 골라주세요.\n\n"
                    "원하는 용어가 없다면\n"
                    "용어를 직접 입력해도 좋아요."
                )}}],
                "quickReplies": quick_replies,
            },
        }

    def format_not_found_for_kakao(self) -> Dict:
        """
        검색 실패

        quickReplies — Lambda 명령 라벨로 통일:
          - CMD_INPUT ("용어 직접 입력")
          - 카테고리 4개
          - CMD_MAIN ("메인으로")
        """
        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": (
                    "⚠️ 해당 용어를\n"
                    "사전에서 찾지 못했어요\n\n"
                    "공식 용어가 아닐 수 있어요.\n"
                    "다른 표현으로 다시 입력해 주세요."
                )}}],
                "quickReplies": [
                    self._qr(CMD_INPUT, CMD_INPUT, "glossary_input_block"),
                    self._qr("지표·숫자 용어", "지표 · 숫자 용어", "glossary_category_block"),
                    self._qr("거래 관련 용어", "매수 · 매도 관련 용어", "glossary_category_block"),
                    self._qr("손익 · 수익률 관련", "손익 · 수익률 관련", "glossary_category_block"),
                    self._qr("차트 · 기술적 용어", "차트 · 기술적 용어", "glossary_category_block"),
                    self._qr(CMD_MAIN, CMD_MAIN, "main_block"),
                ],
            },
        }

    # ====================================================
    # quickReply 빌더 (단일 진입점)
    # ====================================================

    @staticmethod
    def _qr(label: str, message_text: str, block_id: Optional[str] = None) -> Dict:
        """
        quickReply 단일 빌더. 모든 format_* 가 이걸 거쳐가도록 통일.

        - action="block" + blockId 지정 → 카카오 i 어드민 블록 분기
        - blockId 없으면 action="message" (단순 텍스트 발화)
        - label은 카카오 i 표시 길이 제약(통상 14자) 고려해서 호출자가 자름
        """
        qr = {
            "label": label,
            "messageText": message_text,
        }
        if block_id:
            qr["action"] = "block"
            qr["blockId"] = block_id
        else:
            qr["action"] = "message"
        return qr


# ====================================================
# 테스트
# ====================================================

if __name__ == "__main__":
    import json

    print("=" * 60)
    print("Chatbot_03 v6 일원화 테스트")
    print("=" * 60)

    chatbot = ChatbotGlossary()
    print(f"\nKB 용어 수: {chatbot.glossary.get_term_count()}개\n")

    # 1. entry — 라벨이 Lambda 명령과 일치하는지
    print("[1] format_entry_for_kakao — Lambda 명령 일치 확인")
    print("-" * 50)
    entry = chatbot.format_entry_for_kakao()
    qr_labels = [q["label"] for q in entry["template"]["quickReplies"]]
    print(f"  quickReplies: {qr_labels}")
    assert "용어 직접 입력" in qr_labels, "CMD_INPUT 누락"
    assert "메인으로" in qr_labels, "CMD_MAIN 누락"
    assert "종료" not in qr_labels, "❌ '종료' 라벨이 남아있음 (v5 잔재)"
    print("  ✅ 라벨 일원화 정상")
    print()

    # 2. found 결과 — 2풍선 + 안내문 부착
    print("[2] format_explanation_for_kakao — 2풍선 + 안내문")
    print("-" * 50)
    result = chatbot.search_and_explain("PER")
    if result["status"] == "found":
        kakao = chatbot.format_explanation_for_kakao(result)
        outs = kakao["template"]["outputs"]
        print(f"  outputs: {len(outs)}개")
        for i, o in enumerate(outs, 1):
            txt = o["simpleText"]["text"]
            print(f"  [{i}] {len(txt)}자: ...{txt[-60:]}")
        # 마지막 풍선에 안내문 들어있는지
        last_txt = outs[-1]["simpleText"]["text"]
        if "다른 용어도 질문해보세요" in last_txt or "궁금한 용어를 다시 입력" in last_txt:
            print("  ✅ 안내 문구 부착됨")
        else:
            print("  ⚠️ 안내 문구 누락")

        # quickReplies 확인
        qr_labels = [q["label"] for q in kakao["template"]["quickReplies"]]
        print(f"  quickReplies: {qr_labels}")
        assert "다른 용어 질문" in qr_labels
        assert "메인으로" in qr_labels
        print("  ✅ 결과 화면 quickReplies 정상")
    print()

    # 3. not_found
    print("[3] format_not_found_for_kakao")
    print("-" * 50)
    nf = chatbot.format_not_found_for_kakao()
    qr_labels = [q["label"] for q in nf["template"]["quickReplies"]]
    print(f"  quickReplies: {qr_labels}")
    assert "다시 입력" not in qr_labels, "❌ '다시 입력' 라벨 남아있음 (v5 잔재)"
    assert "용어 직접 입력" in qr_labels
    print("  ✅ '다시 입력' 폐기 정상")
    print()

    # 4. disambiguate
    print("[4] format_disambiguate_for_kakao")
    print("-" * 50)
    dis = chatbot.format_disambiguate_for_kakao([{"term": "PER", "full_name": "주가수익비율"}])
    qr_labels = [q["label"] for q in dis["template"]["quickReplies"]]
    print(f"  quickReplies: {qr_labels}")
    assert "다시 입력" not in qr_labels
    assert "용어 직접 입력" in qr_labels
    print("  ✅ disambiguate quickReplies 정상")
    print()

    # 5. category
    print("[5] format_category_for_kakao")
    print("-" * 50)
    cat = chatbot.format_category_for_kakao("지표 · 숫자 용어")
    qr_labels = [q["label"] for q in cat["template"]["quickReplies"]]
    print(f"  quickReplies: {qr_labels}")
    assert "메인으로" in qr_labels
    assert "다른 카테고리 보기" in qr_labels
    assert "종료" not in qr_labels
    print("  ✅ category quickReplies 정상")
    print()

    print("=" * 60)
    print("✅ v6 일원화 테스트 완료")
    print("=" * 60)
