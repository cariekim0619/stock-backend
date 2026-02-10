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
      - (official_name, code) 또는 None(= 없는 종목/검증 실패)

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

    # 3) company_name으로도 시도 (ticker가 비었거나 이상하면)
    if name_in:
        row = stocks[stocks["Name"] == name_in]
        if not row.empty:
            code = str(row.iloc[0]["Code"])
            official_name = str(row.iloc[0]["Name"])
            return official_name, code

    # 못 찾으면 없는 종목
    return None


def _no_stock_payload(company_name: str, ticker: str) -> Dict:
    """
    없는 종목/검증 실패 시 공통 응답 포맷
    - 상위(Chatbot)에서 이 값을 감지해 LLM 호출을 막거나
      사용자에게 '없는 종목'을 안내하도록 사용
    """
    return {
        "error": "NO_STOCK",
        "message": "없는 종목입니다.",
        "input_company_name": company_name,
        "input_ticker": ticker,
        "searched_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


# =========================
# Tavily Client
# =========================

class TavilySearchClient:
    """
    Tavily API를 사용한 웹 검색 클라이언트
    주식 뉴스, 애널리스트 의견, 시장 반응 등을 검색
    """

    def __init__(self):
        """Tavily API 초기화"""
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

    def search_stock_news(
        self,
        company_name: str,
        ticker: str,
        max_results: int = 5
    ) -> Dict:
        """
        종목 관련 최신 뉴스 검색

        Args:
            company_name: 회사명 (예: "삼성전자")
            ticker: 종목코드 또는 종목명 (예: "005930" or "삼성전자")
            max_results: 최대 결과 수

        Returns:
            검색 결과 딕셔너리
        """
        if not self.available:
            return {"error": "Tavily not available", "results": []}

        # ✅ 요구사항: 실존 종목 검증 + 정규화
        resolved = _resolve_symbol_and_name(company_name, ticker)
        if resolved is None:
            return _no_stock_payload(company_name, ticker)

        official_name, official_code = resolved
        company_name = official_name
        ticker = official_code

        query = f"{company_name} 주식 뉴스 최신"

        try:
            response = self.client.search(
                query=query,
                search_depth="basic",
                max_results=max_results,
                include_answer=True,
                include_domains=["naver.com", "hankyung.com", "mk.co.kr", "sedaily.com", "edaily.co.kr"]
            )

            return {
                "company_name": company_name,
                "ticker": ticker,
                "query": query,
                "answer": response.get("answer", ""),
                "results": [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "content": r.get("content", "")[:300],  # 300자로 제한
                        "score": r.get("score", 0)
                    }
                    for r in response.get("results", [])
                ],
                "searched_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        except Exception as e:
            return {"error": str(e), "results": []}

    def search_analyst_opinion(
        self,
        company_name: str,
        ticker: str,
        max_results: int = 3
    ) -> Dict:
        """
        애널리스트 의견/목표주가 검색

        Args:
            company_name: 회사명 (예: "삼성전자")
            ticker: 종목코드 또는 종목명 (예: "005930" or "삼성전자")
            max_results: 최대 결과 수

        Returns:
            검색 결과 딕셔너리
        """
        if not self.available:
            return {"error": "Tavily not available", "results": []}

        # ✅ 요구사항: 실존 종목 검증 + 정규화
        resolved = _resolve_symbol_and_name(company_name, ticker)
        if resolved is None:
            return _no_stock_payload(company_name, ticker)

        official_name, official_code = resolved
        company_name = official_name
        ticker = official_code

        query = f"{company_name} 목표주가 애널리스트 리포트 2026"

        try:
            response = self.client.search(
                query=query,
                search_depth="basic",
                max_results=max_results,
                include_answer=True
            )

            return {
                "company_name": company_name,
                "ticker": ticker,
                "query": query,
                "answer": response.get("answer", ""),
                "results": [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "content": r.get("content", "")[:300]
                    }
                    for r in response.get("results", [])
                ],
                "searched_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        except Exception as e:
            return {"error": str(e), "results": []}

    def search_market_sentiment(
        self,
        company_name: str,
        ticker: str,
        max_results: int = 5
    ) -> Dict:
        """
        시장 반응/커뮤니티 의견 검색

        Args:
            company_name: 회사명
            ticker: 종목코드 또는 종목명
            max_results: 최대 결과 수

        Returns:
            검색 결과 딕셔너리
        """
        if not self.available:
            return {"error": "Tavily not available", "results": []}

        # ✅ 요구사항: 실존 종목 검증 + 정규화
        resolved = _resolve_symbol_and_name(company_name, ticker)
        if resolved is None:
            return _no_stock_payload(company_name, ticker)

        official_name, official_code = resolved
        company_name = official_name
        ticker = official_code

        query = f"{company_name} 주식 전망 투자 의견"

        try:
            response = self.client.search(
                query=query,
                search_depth="basic",  # 비용 절감 (advanced → basic)
                max_results=max_results,
                include_answer=True
            )

            return {
                "company_name": company_name,
                "ticker": ticker,
                "query": query,
                "answer": response.get("answer", ""),
                "results": [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "content": r.get("content", "")[:300]
                    }
                    for r in response.get("results", [])
                ],
                "searched_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        except Exception as e:
            return {"error": str(e), "results": []}

    def get_comprehensive_info(
        self,
        company_name: str,
        ticker: str
    ) -> Dict:
        """
        종합 정보 검색 (뉴스 + 애널리스트 + 시장반응)

        Args:
            company_name: 회사명
            ticker: 종목코드 또는 종목명

        Returns:
            종합 검색 결과
        """
        # ✅ 여기서 1번만 정규화/검증해도 되지만
        #    각 메서드에 이미 방어로직이 있어서 중복 안전
        resolved = _resolve_symbol_and_name(company_name, ticker)
        if resolved is None:
            return _no_stock_payload(company_name, ticker)

        official_name, official_code = resolved
        company_name = official_name
        ticker = official_code

        news = self.search_stock_news(company_name, ticker)
        analyst = self.search_analyst_opinion(company_name, ticker)
        sentiment = self.search_market_sentiment(company_name, ticker)

        return {
            "company_name": company_name,
            "ticker": ticker,
            "news": news,
            "analyst": analyst,
            "sentiment": sentiment,
            "searched_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

    def format_for_llm(self, search_result: Dict) -> str:
        """
        검색 결과를 LLM 프롬프트용 텍스트로 변환

        Args:
            search_result: 검색 결과 딕셔너리

        Returns:
            포맷된 텍스트
        """
        # ✅ 요구사항: 없는 종목이면 LLM 프롬프트 생성 자체를 막기
        if isinstance(search_result, dict) and search_result.get("error") == "NO_STOCK":
            return "없는 종목입니다."

        lines = []

        # 뉴스 섹션
        if "news" in search_result:
            news = search_result["news"]
            # 뉴스 자체가 없는 종목 에러면 바로 반환
            if isinstance(news, dict) and news.get("error") == "NO_STOCK":
                return "없는 종목입니다."

            lines.append("### 최신 뉴스")
            if news.get("answer"):
                lines.append(f"요약: {news['answer'][:500]}")
            for i, r in enumerate(news.get("results", [])[:3], 1):
                title = r.get("title", "")
                content = r.get("content", "")
                lines.append(f"{i}. [{title}]")
                lines.append(f"   {content[:150]}...")
            lines.append("")

        # 애널리스트 의견 섹션
        if "analyst" in search_result:
            analyst = search_result["analyst"]
            if isinstance(analyst, dict) and analyst.get("error") == "NO_STOCK":
                return "없는 종목입니다."

            lines.append("### 애널리스트 의견")
            if analyst.get("answer"):
                lines.append(f"요약: {analyst['answer'][:500]}")
            for i, r in enumerate(analyst.get("results", [])[:2], 1):
                title = r.get("title", "")
                content = r.get("content", "")
                lines.append(f"{i}. [{title}]")
                lines.append(f"   {content[:150]}...")
            lines.append("")

        # 시장 반응 섹션
        if "sentiment" in search_result:
            sentiment = search_result["sentiment"]
            if isinstance(sentiment, dict) and sentiment.get("error") == "NO_STOCK":
                return "없는 종목입니다."

            lines.append("### 시장 반응/커뮤니티")
            if sentiment.get("answer"):
                lines.append(f"요약: {sentiment['answer'][:500]}")
            for i, r in enumerate(sentiment.get("results", [])[:2], 1):
                title = r.get("title", "")
                content = r.get("content", "")
                lines.append(f"{i}. [{title}]")
                lines.append(f"   {content[:150]}...")
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
        print("[OK] Tavily 클라이언트 초기화 성공")
        print()

        # ✅ 테스트 1: 정상 케이스
        company = "삼성전자"
        ticker = "005930"

        print(f"[TEST 1] 검색 중: {company} ({ticker})")
        print("-" * 40)

        result = client.get_comprehensive_info(company, ticker)

        if result.get("error") == "NO_STOCK":
            print("[NO_STOCK] 없는 종목으로 판정됨")
        else:
            # 뉴스 출력
            print("\n[최신 뉴스]")
            news = result.get("news", {})
            if news.get("answer"):
                print(f"AI 요약: {news['answer'][:200]}...")
            for r in news.get("results", [])[:3]:
                print(f"  - {r.get('title', '')}")

            # 애널리스트 출력
            print("\n[애널리스트 의견]")
            analyst = result.get("analyst", {})
            if analyst.get("answer"):
                print(f"AI 요약: {analyst['answer'][:200]}...")
            for r in analyst.get("results", [])[:2]:
                print(f"  - {r.get('title', '')}")

            # LLM용 포맷
            print("\n[LLM 프롬프트용 텍스트]")
            print("-" * 40)
            formatted = client.format_for_llm(result)
            print(formatted[:500] + "..." if len(formatted) > 500 else formatted)

        print("\n[OK] 테스트 완료!")

        # ✅ 테스트 2: 오타 케이스(없는 종목)
        company2 = "삼삼전자"
        ticker2 = "삼삼전자"

        print("\n" + "=" * 60)
        print(f"[TEST 2] 오타 케이스: {company2} ({ticker2})")
        print("-" * 40)

        result2 = client.get_comprehensive_info(company2, ticker2)
        print(result2)  # error: NO_STOCK 확인

        formatted2 = client.format_for_llm(result2)
        print("\n[LLM 포맷 결과]")
        print(formatted2)

    except Exception as e:
        print(f"[ERROR] {e}")
