"""
종목 리스트 데이터 조회 모듈
웹 기획서 Web_01 - 종목 탐색 메인 화면용
"""

from datetime import datetime
from typing import Dict, List, Optional

try:
    from app.services.chatbot_report.HantuStock import HantuStock
except ImportError:
    HantuStock = None




def _is_recommendation_excluded_product_name(name: str) -> bool:
    n = (name or "").upper().replace(" ", "")
    tokens = (
        "KODEX", "TIGER", "ACE", "KBSTAR", "SOL", "HANARO", "ARIRANG", "KOSEF",
        "TIMEFOLIO", "RISE", "PLUS", "TREX", "FOCUS", "1Q", "WOORI", "마이티", "히어로즈",
        "ETF", "ETN", "인버스", "레버리지", "커버드콜", "합성", "혼합", "미국채", "국채", "채권",
    )
    return bool(n and any(tok.upper().replace(" ", "") in n for tok in tokens))


class StockListDataProvider:
    """종목 리스트 데이터 제공 클래스"""

    def __init__(self, hantu_stock: Optional[HantuStock] = None):
        """
        Args:
            hantu_stock: HantuStock 인스턴스 (선택). 보유 종목 조회용
        """
        self._hantu = hantu_stock
        if hantu_stock is None and HantuStock is not None:
            try:
                self._hantu = HantuStock()
            except Exception as e:
                print(f"[WARN] HantuStock 초기화 실패: {e}. 보유 종목 조회 제한됨.")

    # ==================== KIS 랭킹 API ====================

    def _get_ranking_stocks(self, category: str, limit: int) -> Dict:
        """
        KIS 랭킹 API로 거래량/등락률 순위 조회

        Args:
            category: "volume" (거래량) | "return" (등락률)
            limit: 조회 개수

        Returns:
            get_market_stocks()와 동일한 스키마
        """
        if not self._hantu:
            return {"error": "HantuStock 초기화 필요"}
        try:
            today = datetime.now().strftime("%Y%m%d")
            rankings = self._hantu.get_market_ranking(category=category, limit=limit)
            if not rankings:
                return {"error": "랭킹 데이터 없음 (장외 시간 또는 API 오류)"}
            stocks = []
            for item in rankings:
                name = item.get("company_name", "")
                # 추천 종목 화면에서는 ETF/ETN/커버드콜/합성 상품을 제외한다.
                # 보유 종목, 리포트, 뉴스 커뮤니티 직접 조회 경로에는 이 필터를 적용하지 않는다.
                if _is_recommendation_excluded_product_name(name):
                    continue
                stocks.append({
                    "ticker": item.get("symbol", ""),
                    "name": name,
                    "current_price": item.get("current_price", 0),
                    "change_rate": item.get("change_rate", 0),
                    "volume": item.get("volume", 0),
                })
            stocks = stocks[:limit]
            return {"date": today, "market": "ALL", "count": len(stocks), "stocks": stocks}
        except Exception as e:
            return {"error": str(e)}

    # ==================== 정렬 기능 ====================

    def sort_stocks(self, stocks: List[Dict], sort_by: str = "price", order: str = "desc") -> List[Dict]:
        """
        종목 리스트 정렬

        Args:
            stocks: 종목 리스트
            sort_by: 정렬 기준 (price, change_rate, volume, name)
            order: 정렬 순서 (asc, desc)

        Returns:
            list: 정렬된 종목 리스트
        """
        sort_keys = {
            "price": "current_price",
            "change_rate": "change_rate",
            "volume": "volume",
            "name": "name"
        }

        key = sort_keys.get(sort_by, "current_price")
        reverse = (order == "desc")

        return sorted(stocks, key=lambda x: x.get(key, 0), reverse=reverse)

    def get_sorted_market_stocks(
        self,
        sort_by: str = "volume",
        order: str = "desc",
        limit: int = 100
    ) -> Dict:
        """
        정렬된 시장 종목 리스트 조회

        Args:
            sort_by: 정렬 기준 (change_rate, volume)
            order: 정렬 순서 (asc, desc)
            limit: 최대 조회 개수

        Returns:
            dict: 정렬된 종목 리스트
        """
        if sort_by == "volume":
            category = "volume"
        elif sort_by == "change_rate":
            category = "return"
        else:
            return {"error": f"지원하지 않는 정렬 기준입니다: {sort_by} (volume, change_rate만 지원)"}

        result = self._get_ranking_stocks(category=category, limit=limit)
        if "error" in result:
            return result

        result["sort_by"] = sort_by
        result["order"] = order
        if order == "asc":
            result["stocks"] = list(reversed(result["stocks"]))
        return result

    # ==================== 보유 종목 리스트 ====================

    def get_holding_stocks(self, sort_by: str = "eval_amount", order: str = "desc") -> Dict:
        """
        보유 종목 리스트 조회 (계좌 연동 필요)

        Args:
            sort_by: 정렬 기준 (eval_amount, profit_rate, quantity, name)
            order: 정렬 순서 (asc, desc)

        Returns:
            dict: 보유 종목 리스트
        """
        if not self._hantu:
            return {"error": "계좌 연동이 필요합니다. HantuStock 초기화 필요."}

        try:
            holdings = self._hantu.get_holding_stock_detail()

            if not holdings:
                return {
                    "count": 0,
                    "stocks": [],
                    "message": "보유 종목이 없습니다"
                }

            # 필드명 변환
            stocks = []
            for h in holdings:
                stocks.append({
                    "ticker": h.get("pdno", ""),
                    "name": h.get("prdt_name", ""),
                    "quantity": h.get("hldg_qty", 0),
                    "avg_price": h.get("pchs_avg_prc", 0),
                    "current_price": h.get("prpr", 0),
                    "eval_amount": h.get("evlu_amt", 0),
                    "profit_amount": h.get("evlu_pfls_amt", 0),
                    "profit_rate": h.get("evlu_pfls_rt", 0)
                })

            # 정렬
            sort_keys = {
                "eval_amount": "eval_amount",
                "profit_rate": "profit_rate",
                "quantity": "quantity",
                "name": "name"
            }
            key = sort_keys.get(sort_by, "eval_amount")
            reverse = (order == "desc")
            stocks = sorted(stocks, key=lambda x: x.get(key, 0), reverse=reverse)

            # 총 평가금액 계산
            total_eval = sum(s["eval_amount"] for s in stocks)
            total_profit = sum(s["profit_amount"] for s in stocks)

            return {
                "count": len(stocks),
                "total_eval_amount": total_eval,
                "total_profit_amount": total_profit,
                "sort_by": sort_by,
                "order": order,
                "stocks": stocks
            }

        except Exception as e:
            return {"error": str(e)}

    # ==================== 관심 종목 (DB 연동 필요) ====================

    def get_watchlist_stocks(self, user_id: str, tickers: List[str]) -> Dict:
        """
        관심 종목 리스트 조회

        Args:
            user_id: 사용자 ID
            tickers: 관심 종목 티커 리스트 (DB에서 조회한 값)

        Returns:
            dict: 관심 종목 리스트 (현재가 정보 포함)
        """
        if not tickers:
            return {
                "count": 0,
                "stocks": [],
                "message": "관심 종목이 없습니다"
            }

        stocks = []
        for ticker in tickers:
            try:
                if self._hantu:
                    data = self._hantu.get_stock_price(ticker)
                    if "error" not in data:
                        stocks.append({
                            "ticker": ticker,
                            "name": data.get("name", ticker),
                            "current_price": data.get("current_price", 0),
                            "change_rate": data.get("change_rate", 0),
                            "volume": data.get("volume", 0)
                        })
            except:
                continue

        return {
            "user_id": user_id,
            "count": len(stocks),
            "stocks": stocks
        }

    # ==================== 종목명 부분 검색 ====================

    def search_stocks_by_name(self, query: str, limit: int = 5) -> Dict:
        """
        종목명 부분 검색 + 거래량 기준 TOP N 반환

        Args:
            query: 검색어 (대소문자 구분 없음, 부분 일치)
            limit: 최대 반환 개수 (기본 5)

        Returns:
            {
                "query": "SK",
                "count": 3,
                "stocks": [
                    {"ticker": "000660", "name": "SK하이닉스", "current_price": 180000, "volume": 1234567},
                    ...
                ]
            }
        """
        result = self._get_ranking_stocks(category="volume", limit=100)
        if "error" in result:
            return result

        query_lower = query.lower()
        matched = [
            s for s in result.get("stocks", [])
            if query_lower in s.get("name", "").lower()
        ]

        return {
            "query": query,
            "count": len(matched[:limit]),
            "stocks": matched[:limit],
        }


# 테스트
if __name__ == "__main__":
    provider = StockListDataProvider()

    print("=" * 50)
    print("종목 리스트 테스트")
    print("=" * 50)

    # 전체 종목 리스트 (상승률순)
    print("\n[1] 상승률 상위 종목:")
    result = provider.get_sorted_market_stocks(
        sort_by="change_rate",
        order="desc",
        limit=10
    )
    if "error" not in result:
        for i, stock in enumerate(result["stocks"][:5], 1):
            print(f"  {i}. {stock['name']} ({stock['ticker']}): {stock['current_price']:,}원 ({stock['change_rate']:+.2f}%)")
    else:
        print(f"  에러: {result['error']}")

    # 거래량 상위
    print("\n[2] 거래량 상위 종목:")
    result = provider.get_sorted_market_stocks(
        sort_by="volume",
        order="desc",
        limit=10
    )
    if "error" not in result:
        for i, stock in enumerate(result["stocks"][:5], 1):
            print(f"  {i}. {stock['name']} ({stock['ticker']}): 거래량 {stock['volume']:,}")
    else:
        print(f"  에러: {result['error']}")

    # 보유 종목
    print("\n[3] 보유 종목:")
    result = provider.get_holding_stocks()
    if "error" not in result:
        print(f"  총 평가금액: {result.get('total_eval_amount', 0):,}원")
        for stock in result["stocks"][:3]:
            print(f"  - {stock['name']}: {stock['quantity']}주, 수익률 {stock['profit_rate']:+.2f}%")
    else:
        print(f"  {result.get('error', result.get('message', ''))}")
