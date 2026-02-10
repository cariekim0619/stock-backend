"""
Tavily 웹 검색 모듈
종목 관련 최신 뉴스 및 정보를 검색합니다.

✅ 요구사항 반영:
- (company_name, ticker) 입력이 '실존 종목'인지 KRX(FDR)로 먼저 검증
- 오타(예: 삼삼전자) 또는 존재하지 않는 코드면 Tavily/LLM 로직을 타지 않고 즉시 "없는 종목" 반환
- ticker가 종목명으로 들어와도 정식 (회사명, 6자리 코드)로 정규화
- KRX 리스트는 1회 로드 후 캐싱(lru_cache)해서 성능 유지
"""

import os
from typing import Dict, Optional, Tuple
from datetime import datetime
from dotenv import load_dotenv
from functools import lru_cache

load_dotenv()


# =========================
# KRX Listing Cache / Normalize
# =========================

@lru_cache(maxsize=1)
def _krx_listing_cached():
    """
    KRX 종목 리스트 1회 로드 후 캐싱
    - 서버 재시작 전까지는 같은 데이터 재사용
    """
    try:
        import FinanceDataReader as fdr
    except Exception:
        return None

    try:
        return fdr.StockListing("KRX")
    except Exception:
        return None


def _resolve_symbol_and_name(company_name: str, ticker: str) -> Optional[Tuple[str, str]]:
    """
    입력으로 들어온 (company_name, ticker)를
    '실존 종목' 기준으로 (정식회사명, 6자리코드)로 정규화한다.

    반환:
      - (official_name, code) 또는 None(= 없는 종목)

    정책:
    - ticker가 6자리 코드면: Code 존재 검증 후 Name 확정
    - ticker가 종목명처럼 들어오면: Name 정확 매칭으로 Code 찾기
    - company_name도 보조로 Name 매칭 시도
    """
    stocks = _krx_listing_cached()
    if stocks is None:
        # 검증 자체가 불가능한 상태(FDR 미설치/로드 실패)
        return None

    name_in = (company_name or "").strip()
    tick_in = (ticker or "").strip()

    # 1) ticker가 6자리 코드면: 코드 존재 검증 + name 확정
    if tick_in.isdigit() and len(tick_in) == 6:
        row = stocks[stocks["Code"] == tick_in]
        if row.empty:
            return None
        official_name = str(row.iloc[0]["Name"])
        return official_name, tick_in

    # 2) ticker가 종목명처럼 들어온 경우: 이름으로 code 찾기(정확 매칭)
    if tick_in:
        row = stocks[stocks["Name"] == tick_in]
        if not row.empty:
            code = str(row.iloc[0]["Code"])
            official_name = str(row.iloc[0]["Name"])
            return official_name, code

    # 3) company_name으로도 시도
    if name_in:
        row = stocks[stocks["Name"] == name_in]
        if not row.empty:
            code = str(row.iloc[0]["Code"])
            official_name = str(row.iloc[0]["Name"])
            return official_name, code

    return None


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _no_stock_payload(company_name: str, ticker: str) -> Dict:
    """
    없는 종목일 때 공통 응답 포맷
    """
    return {
        "error": "NO_STOCK",
        "message": "없는 종목입니다.",
        "input_company_name": company_name,
        "input_ticker": ticker,
        "searched_at": _now_str(),
    }


def _verify_failed_payload(company_name: str, ticker: str) -> Dict:
    """
    FDR 미설치/조회 실패 등으로 검증 자체가 불가능할 때
    - 운영에서 원인 파악용
    """
    return {
        "error": "VERIFY_FAILED",
        "message": "종목 검증에 실패했습니다. (KRX 목록 로드 실패)",
        "input_company_name": company_name,
        "input_ticker": ticker,
        "searched_at": _now_str(),
    }


def _verify_and_normalize(company_name: str, ticker: str) -> Tuple[Optional[str], Optional[str], Optional[Dict]]:
    """
    (company_name, ticker) 입력을 검증/정규화하고
    실패 시 바로 반환 payload를 만든다.

    반환:
      - (official_name, official_code, error_payload)
      - 성공이면 error_payload = None
      - 실패면 official_name/code = None
    """
    stocks = _krx_listing_cached()
    if stocks is None:
        return None, None, _verify_failed_payload(company_name, ticker)

    resolved = _resolve_symbol_and_name(company_name, ticker)
    if resolved is None:
        return None, None, _no_stock_payload(company_name, ticker)

    official_name, official_code = resolved
    return official_name, official_code, None


# =========================
# Tavily Client
# =========================

class TavilySearchClient:
    """
    Tavily API를 사용한 웹 검색 클라이언트
    주식 뉴스, 애널리스트 의견, 시장 반응 등을 검색
    """

    def __init__(self):
        self.api_key = os.environ.get("TAVILY_API_KEY")
        if not self.api_key:
            raise ValueError("TAVILY_API_KEY not found in environment")

        try:
            from tavily import TavilyClient
            self.client = TavilyClient(api_key=self.api_key)
            self.available = True
        except ImportError:
            print("Warning: tavily-python not installed. Run: pip install tavily-python")
            self.available = False

    def search_stock_news(self, company_name: str, ticker: str, max_results: int = 5) -> Dict:
        if not self.available:
            return {"error": "Tavily not available", "results": [], "searched_at": _now_str()}

        official_name, official_code, err = _verify_and_normalize(company_name, ticker)
        if err is not None:
            return err

        query = f"{official_name} 주식 뉴스 최신"

        try:
            response = self.client.search(
                query=query,
                search_depth="basic",
                max_results=max_results,
                include_answer=True,
                include_domains=["naver.com", "hankyung.com", "mk.co.kr", "sedaily.com", "edaily.co.kr"],
            )

            return {
                "company_name": official_name,
                "ticker": official_code,
                "query": query,
                "answer": response.get("answer", ""),
                "results": [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "content": (r.get("content", "") or "")[:300],
                        "score": r.get("score", 0),
                    }
                    for r in response.get("results", [])
                ],
                "searched_at": _now_str(),
            }
        except Exception as e:
            return {"error": str(e), "results": [], "searched_at": _now_str()}

    def search_analyst_opinion(self, company_name: str, ticker: str, max_results: int = 3) -> Dict:
        if not self.available:
            return {"error": "Tavily not available", "results": [], "searched_at": _now_str()}

        official_name, official_code, err = _verify_and_normalize(company_name, ticker)
        if err is not None:
            return err

        query = f"{official_name} 목표주가 애널리스트 리포트 2026"

        try:
            response = self.client.search(
                query=query,
                search_depth="basic",
                max_results=max_results,
                include_answer=True,
            )

            return {
                "company_name": official_name,
                "ticker": official_code,
                "query": query,
                "answer": response.get("answer", ""),
                "results": [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "content": (r.get("content", "") or "")[:300],
                    }
                    for r in response.get("results", [])
                ],
                "searched_at": _now_str(),
            }
        except Exception as e:
            return {"error": str(e), "results": [], "searched_at": _now_str()}

    def search_market_sentiment(self, company_name: str, ticker: str, max_results: int = 5) -> Dict:
        """
        ✅ 여기서 ticker 필수
        - stock_news_data.py에서 호출할 때 ticker를 반드시 넘겨야 함
        """
        if not self.available:
            return {"error": "Tavily not available", "results": [], "searched_at": _now_str()}

        official_name, official_code, err = _verify_and_normalize(company_name, ticker)
        if err is not None:
            return err

        query = f"{official_name} 주식 전망 투자 의견"

        try:
            response = self.client.search(
                query=query,
                search_depth="basic",
                max_results=max_results,
                include_answer=True,
            )

            return {
                "company_name": official_name,
                "ticker": official_code,
                "query": query,
                "answer": response.get("answer", ""),
                "results": [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "content": (r.get("content", "") or "")[:300],
                    }
                    for r in response.get("results", [])
                ],
                "searched_at": _now_str(),
            }
        except Exception as e:
            return {"error": str(e), "results": [], "searched_at": _now_str()}

    def get_comprehensive_info(self, company_name: str, ticker: str) -> Dict:
        """
        종합 정보 검색 (뉴스 + 애널리스트 + 시장반응)
        - 여기서 1번만 검증/정규화하고 하위 호출엔 정식 값으로 넘김
        """
        if not self.available:
            return {"error": "Tavily not available", "results": [], "searched_at": _now_str()}

        official_name, official_code, err = _verify_and_normalize(company_name, ticker)
        if err is not None:
            return err

        news = self.search_stock_news(official_name, official_code)
        analyst = self.search_analyst_opinion(official_name, official_code)
        sentiment = self.search_market_sentiment(official_name, official_code)

        # 하위 중 하나라도 NO_STOCK/VERIFY_FAILED면 그대로 반환(일관성)
        for part in (news, analyst, sentiment):
            if isinstance(part, dict) and part.get("error") in ("NO_STOCK", "VERIFY_FAILED"):
                return part

        return {
            "company_name": official_name,
            "ticker": official_code,
            "news": news,
            "analyst": analyst,
            "sentiment": sentiment,
            "searched_at": _now_str(),
        }

    def format_for_llm(self, search_result: Dict) -> str:
        """
        검색 결과를 LLM 프롬프트용 텍스트로 변환
        ✅ 없는 종목/검증 실패면 프롬프트 생성 자체를 막음
        """
        if isinstance(search_result, dict) and search_result.get("error") in ("NO_STOCK", "VERIFY_FAILED"):
            return search_result.get("message", "없는 종목입니다.")

        lines = []

        # 뉴스
        if "news" in search_result:
            news = search_result["news"]
            if isinstance(news, dict) and news.get("error") in ("NO_STOCK", "VERIFY_FAILED"):
                return news.get("message", "없는 종목입니다.")

            lines.append("### 최신 뉴스")
            if news.get("answer"):
                lines.append(f"요약: {news['answer'][:500]}")
            for i, r in enumerate(news.get("results", [])[:3], 1):
                lines.append(f"{i}. [{r.get('title','')}]")
                lines.append(f"   {(r.get('content','') or '')[:150]}...")
            lines.append("")

        # 애널리스트
        if "analyst" in search_result:
            analyst = search_result["analyst"]
            if isinstance(analyst, dict) and analyst.get("error") in ("NO_STOCK", "VERIFY_FAILED"):
                return analyst.get("message", "없는 종목입니다.")

            lines.append("### 애널리스트 의견")
            if analyst.get("answer"):
                lines.append(f"요약: {analyst['answer'][:500]}")
            for i, r in enumerate(analyst.get("results", [])[:2], 1):
                lines.append(f"{i}. [{r.get('title','')}]")
                lines.append(f"   {(r.get('content','') or '')[:150]}...")
            lines.append("")

        # 시장 반응
        if "sentiment" in search_result:
            sentiment = search_result["sentiment"]
            if isinstance(sentiment, dict) and sentiment.get("error") in ("NO_STOCK", "VERIFY_FAILED"):
                return sentiment.get("message", "없는 종목입니다.")

            lines.append("### 시장 반응/커뮤니티")
            if sentiment.get("answer"):
                lines.append(f"요약: {sentiment['answer'][:500]}")
            for i, r in enumerate(sentiment.get("results", [])[:2], 1):
                lines.append(f"{i}. [{r.get('title','')}]")
                lines.append(f"   {(r.get('content','') or '')[:150]}...")
            lines.append("")

        return "\n".join(lines) if lines else "웹 검색 결과 없음"


# ========================================
# 테스트
# ========================================
if __name__ == "__main__":
    print("=" * 60)
    print("Tavily 웹 검색 테스트")
    print("=" * 60)
    print()

    try:
        client = TavilySearchClient()
        print("[OK] Tavily 클라이언트 초기화 성공\n")

        # 테스트 1: 정상 케이스
        company = "삼성전자"
        ticker = "005930"
        print(f"[TEST 1] {company} ({ticker})")
        result = client.get_comprehensive_info(company, ticker)
        print("result.error:", result.get("error"))
        print("company_name:", result.get("company_name"))
        print("ticker:", result.get("ticker"))
        print()

        # 테스트 2: 오타 케이스
        company2 = "삼삼전자"
        ticker2 = "삼삼전자"
        print(f"[TEST 2] {company2} ({ticker2})")
        result2 = client.get_comprehensive_info(company2, ticker2)
        print(result2)
        print("\nLLM 포맷:", client.format_for_llm(result2))

    except Exception as e:
        print(f"[ERROR] {e}")
