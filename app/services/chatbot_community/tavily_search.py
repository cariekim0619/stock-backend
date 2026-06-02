"""
Tavily 웹 검색 모듈
- 종목 검증/정규화는 S3 기반 stock universe cache 사용
- KRX/FDR StockListing 직접 호출 제거
"""

import os
from typing import Dict, Optional, Tuple
from datetime import datetime
from dotenv import load_dotenv

from app.utils.ticker_normalizer import get_lookup_status, resolve_symbol_and_name

load_dotenv()


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _no_stock_payload(company_name: str, ticker: str) -> Dict:
    return {
        "error": "NO_STOCK",
        "message": "없는 종목입니다.",
        "input_company_name": company_name,
        "input_ticker": ticker,
        "searched_at": _now_str(),
    }


def _verify_failed_payload(company_name: str, ticker: str, detail: Optional[str] = None) -> Dict:
    return {
        "error": "VERIFY_FAILED",
        "message": detail or "종목 검증에 실패했습니다. (S3 stock universe cache unavailable)",
        "input_company_name": company_name,
        "input_ticker": ticker,
        "searched_at": _now_str(),
    }


def _verify_and_normalize(company_name: str, ticker: str) -> Tuple[Optional[str], Optional[str], Optional[Dict]]:
    tick_in = (ticker or "").strip()
    name_in = (company_name or "").strip()

    status = get_lookup_status()
    resolved = None

    if tick_in:
        resolved = resolve_symbol_and_name(tick_in)
    if resolved is None and name_in:
        resolved = resolve_symbol_and_name(name_in)

    if resolved is not None:
        official_code, official_name = resolved
        return official_name, official_code, None

    if not status.get("has_usable_data"):
        detail = status.get("last_error") or "종목 캐시를 읽을 수 없습니다."
        return None, None, _verify_failed_payload(company_name, ticker, detail)

    return None, None, _no_stock_payload(company_name, ticker)


class TavilySearchClient:
    """Tavily API를 사용한 웹 검색 클라이언트"""

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
                include_domains=["news.naver.com", "n.news.naver.com", "hankyung.com", "mk.co.kr", "sedaily.com", "edaily.co.kr", "businesspost.co.kr"],
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
                include_domains=["news.naver.com", "n.news.naver.com", "hankyung.com", "mk.co.kr", "edaily.co.kr", "sedaily.com", "businesspost.co.kr"],
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
        if not self.available:
            return {"error": "Tavily not available", "results": [], "searched_at": _now_str()}

        official_name, official_code, err = _verify_and_normalize(company_name, ticker)
        if err is not None:
            return err

        news = self.search_stock_news(official_name, official_code)
        analyst = self.search_analyst_opinion(official_name, official_code)
        sentiment = self.search_market_sentiment(official_name, official_code)

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
        if isinstance(search_result, dict) and search_result.get("error") in ("NO_STOCK", "VERIFY_FAILED"):
            return search_result.get("message", "없는 종목입니다.")

        lines = []

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
