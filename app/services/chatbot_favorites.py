import os
import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

# 숫자 이모지 (1~10)
NUM_EMOJIS = ["➊", "➋", "➌", "➍", "➎", "➏", "➐", "➑", "➒", "➓"]

# FDR 종목 목록 캐시 (프로세스 내 1회만 로드)
_stock_list_cache: Optional[Dict[str, str]] = None  # name -> symbol


def _load_stock_list() -> Dict[str, str]:
    """FDR 종목 목록 로드 (KOSPI + KOSDAQ, 이름 -> 티커 매핑)"""
    global _stock_list_cache

    if _stock_list_cache is not None:
        return _stock_list_cache

    try:
        import FinanceDataReader as fdr
        import pandas as pd

        kospi = fdr.StockListing("KOSPI")[["Symbol", "Name"]]
        kosdaq = fdr.StockListing("KOSDAQ")[["Symbol", "Name"]]

        all_stocks = pd.concat([kospi, kosdaq], ignore_index=True)

        _stock_list_cache = {
            row["Name"]: row["Symbol"]
            for _, row in all_stocks.iterrows()
            if row["Name"] and row["Symbol"]
        }

    except Exception as e:
        print(f"[WARN] 종목 목록 로드 실패: {e}")
        _stock_list_cache = {}

    return _stock_list_cache


class ChatbotFavorites:
    """
    Chatbot_06 관심 종목 데이터 프로바이더

    기능:
    - get_favorites() / add_favorite() / remove_favorite_by_name(): 관심 종목 CRUD
    - search_stock(): 종목명 검색 (FDR 기반)
    - get_top_stocks(): 추천 종목 (거래량/상승률 TOP5, pykrx 기반)
    - load_holdings_to_favorites(): 보유 종목 일괄 불러오기
    - get_summary_card_data(): 요약 정보 (리포트 + 뉴스 + 거래내역)
    - format_*_for_kakao(): 카카오톡 API 2.0 형식 변환
    """

    FAVORITES_DIR = "./favorites"
    MAX_FAVORITES = 10

    def __init__(self):
        os.makedirs(self.FAVORITES_DIR, exist_ok=True)

        # --------------------------------------------------
        # HantuStock / StockChartDataProvider 초기화
        # _hantu_shared import 제거
        # --------------------------------------------------
        try:
            from app.services.chatbot_report.stock_chart_data import StockChartDataProvider

            self._provider = StockChartDataProvider()
            self._hantu = getattr(self._provider, "_hantu", None)

        except Exception as e:
            print(f"[WARN] StockChartDataProvider 초기화 실패: {e}")
            self._provider = None
            self._hantu = None

        # --------------------------------------------------
        # Chatbot_02 종목 리포트
        # --------------------------------------------------
        try:
            from app.services.chatbot_report.chatbot_stock_report import ChatbotStockReport
            self._report = ChatbotStockReport()
        except Exception as e:
            print(f"[WARN] ChatbotStockReport 초기화 실패: {e}")
            self._report = None

        # --------------------------------------------------
        # Chatbot_05 뉴스/커뮤니티
        # --------------------------------------------------
        try:
            from app.services.chatbot_community.chatbot_news_community import ChatbotNewsCommunity
            self._news = ChatbotNewsCommunity()
        except Exception as e:
            print(f"[WARN] ChatbotNewsCommunity 초기화 실패: {e}")
            self._news = None

    # ========================================
    # 관심 종목 저장소 (./favorites/{user_id}.json)
    # ========================================

    def _get_path(self, user_id: str) -> str:
        return os.path.join(self.FAVORITES_DIR, f"{user_id}.json")

    def _load(self, user_id: str) -> List[Dict]:
        """저장된 관심 종목 로드"""
        path = self._get_path(user_id)

        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if isinstance(data, list):
                    return data

            except Exception as e:
                print(f"[WARN] 관심 종목 로드 실패 ({user_id}): {e}")

        return []

    def _save(self, user_id: str, favorites: List[Dict]) -> None:
        """관심 종목 저장"""
        try:
            with open(self._get_path(user_id), "w", encoding="utf-8") as f:
                json.dump(favorites, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WARN] 관심 종목 저장 실패 ({user_id}): {e}")

    def get_favorites(self, user_id: str) -> List[Dict]:
        """
        관심 종목 목록 조회 (등록 순서 유지)

        Returns:
            [{"symbol": "005930", "company_name": "삼성전자", "added_at": "..."}, ...]
        """
        return self._load(user_id)

    def add_favorite(self, user_id: str, symbol: str, company_name: str) -> Dict:
        """
        관심 종목 추가

        Returns:
            {"success": True/False, "reason": "ok"|"full"|"duplicate", "count": N}
        """
        favorites = self._load(user_id)

        if len(favorites) >= self.MAX_FAVORITES:
            return {"success": False, "reason": "full", "count": len(favorites)}

        if any(f["symbol"] == symbol for f in favorites):
            return {"success": False, "reason": "duplicate", "count": len(favorites)}

        favorites.append({
            "symbol": symbol,
            "company_name": company_name,
            "added_at": datetime.now().isoformat(),
        })
        self._save(user_id, favorites)
        return {"success": True, "reason": "ok", "count": len(favorites)}

    def remove_favorite_by_name(self, user_id: str, company_name: str) -> Dict:
        """
        종목명으로 관심 종목 삭제

        Returns:
            {"success": True/False, "symbol": "...", "company_name": "...", "count": N}
        """
        favorites = self._load(user_id)
        target = next(
            (f for f in favorites if f["company_name"] == company_name), None
        )

        if not target:
            return {
                "success": False,
                "company_name": company_name,
                "count": len(favorites),
            }

        favorites = [f for f in favorites if f["symbol"] != target["symbol"]]
        self._save(user_id, favorites)

        return {
            "success": True,
            "symbol": target["symbol"],
            "company_name": company_name,
            "count": len(favorites),
        }

    # ========================================
    # 종목 검색 (FDR 종목 목록 기반)
    # ========================================

    def search_stock(self, query: str) -> Dict:
        """
        종목명으로 티커 검색

        Args:
            query: 종목명 (예: "삼성전자", "SK하이닉스")

        Returns:
            {
                "matched": True/False,
                "symbol": "005930",
                "company_name": "삼성전자",
                "candidates": [...]  # 유사 종목 목록 (복수 매칭 시)
            }
        """
        stock_list = _load_stock_list()
        query = query.strip()

        # 1단계: 정확 일치
        if query in stock_list:
            return {
                "matched": True,
                "symbol": stock_list[query],
                "company_name": query,
                "candidates": [],
            }

        # 2단계: 부분 일치
        candidates = [
            {"company_name": name, "symbol": sym}
            for name, sym in stock_list.items()
            if query in name
        ]

        if len(candidates) == 1:
            return {
                "matched": True,
                "symbol": candidates[0]["symbol"],
                "company_name": candidates[0]["company_name"],
                "candidates": [],
            }

        if len(candidates) > 1:
            candidates.sort(key=lambda x: len(x["company_name"]))
            return {
                "matched": True,
                "symbol": candidates[0]["symbol"],
                "company_name": candidates[0]["company_name"],
                "candidates": candidates[:5],
            }

        return {
            "matched": False,
            "symbol": "",
            "company_name": query,
            "candidates": [],
        }

    # ========================================
    # 추천 종목 (pykrx 기반 거래량/상승률 TOP5)
    # ========================================

def _get_recent_market_dataframe(self):
    """
    최근 영업일의 KOSPI + KOSDAQ 데이터를 반환
    휴장일/주말/pykrx 예외 대응을 위해 최근 7일 안에서 탐색
    """
    from datetime import datetime, timedelta

    try:
        from pykrx import stock as pystock
        import pandas as pd
    except Exception as e:
        print(f"[WARN] pykrx import 실패: {e}")
        return None, None

    today = datetime.now()

    for i in range(7):
        target_date = (today - timedelta(days=i)).strftime("%Y%m%d")

        try:
            df_kospi = pystock.get_market_ohlcv_by_ticker(target_date, market="KOSPI")
            df_kosdaq = pystock.get_market_ohlcv_by_ticker(target_date, market="KOSDAQ")

            # 둘 다 비어 있으면 다음 날짜 시도
            if (df_kospi is None or df_kospi.empty) and (df_kosdaq is None or df_kosdaq.empty):
                print(f"[INFO] 최근 시장 데이터 없음: {target_date}")
                continue

            frames = []
            if df_kospi is not None and not df_kospi.empty:
                frames.append(df_kospi)
            if df_kosdaq is not None and not df_kosdaq.empty:
                frames.append(df_kosdaq)

            if not frames:
                continue

            df = pd.concat(frames)

            if df.empty:
                continue

            return df, target_date

        except Exception as e:
            print(f"[WARN] 최근 시장 데이터 조회 실패 ({target_date}): {e}")
            continue

    return None, None


def _get_previous_market_dataframe(self, base_date: str):
    """
    base_date 이전 최근 영업일 데이터 조회
    """
    from datetime import datetime, timedelta

    try:
        from pykrx import stock as pystock
        import pandas as pd
    except Exception as e:
        print(f"[WARN] pykrx import 실패: {e}")
        return None, None

    base_dt = datetime.strptime(base_date, "%Y%m%d")

    for i in range(1, 8):
        target_date = (base_dt - timedelta(days=i)).strftime("%Y%m%d")

        try:
            df_kospi = pystock.get_market_ohlcv_by_ticker(target_date, market="KOSPI")
            df_kosdaq = pystock.get_market_ohlcv_by_ticker(target_date, market="KOSDAQ")

            if (df_kospi is None or df_kospi.empty) and (df_kosdaq is None or df_kosdaq.empty):
                print(f"[INFO] 이전 시장 데이터 없음: {target_date}")
                continue

            frames = []
            if df_kospi is not None and not df_kospi.empty:
                frames.append(df_kospi)
            if df_kosdaq is not None and not df_kosdaq.empty:
                frames.append(df_kosdaq)

            if not frames:
                continue

            df = pd.concat(frames)

            if df.empty:
                continue

            return df, target_date

        except Exception as e:
            print(f"[WARN] 이전 시장 데이터 조회 실패 ({target_date}): {e}")
            continue

    return None, None

def get_top_stocks(self, category: str = "volume") -> List[Dict]:
    """
    추천 종목 조회

    Args:
        category:
            - "volume": 거래량 TOP5
            - "return": 상승률 TOP5
    """
    try:
        from pykrx import stock as pystock
        import pandas as pd

        df, target_date = self._get_recent_market_dataframe()

        if df is None or df.empty:
            print("[WARN] get_top_stocks: 최근 영업일 데이터를 찾지 못했습니다.")
            return []

        # 컬럼명 체크
        vol_col = "거래량" if "거래량" in df.columns else None
        close_col = "종가" if "종가" in df.columns else None
        chg_col = "등락률" if "등락률" in df.columns else None

        if vol_col is None:
            print(f"[WARN] get_top_stocks: 거래량 컬럼 없음. columns={list(df.columns)}")
            return []

        if close_col is None:
            print(f"[WARN] get_top_stocks: 종가 컬럼 없음. columns={list(df.columns)}")
            return []

        # 등락률 없으면 직접 계산
        if chg_col is None:
            prev_df, prev_date = self._get_previous_market_dataframe(target_date)

            if prev_df is None or prev_df.empty:
                print("[WARN] get_top_stocks: 등락률 계산용 전일 데이터가 없습니다.")
                return []

            prev_close_col = "종가" if "종가" in prev_df.columns else None
            if prev_close_col is None:
                print(f"[WARN] get_top_stocks: 전일 종가 컬럼 없음. columns={list(prev_df.columns)}")
                return []

            merged = df[[close_col, vol_col]].join(
                prev_df[[prev_close_col]].rename(columns={prev_close_col: "_전일종가"}),
                how="inner"
            )

            merged = merged[merged["_전일종가"] > 0]

            if merged.empty:
                print("[WARN] get_top_stocks: 전일 데이터 조인 후 결과가 비었습니다.")
                return []

            merged["_등락률계산값"] = (
                (merged[close_col] - merged["_전일종가"]) / merged["_전일종가"]
            ) * 100

            df = merged
            chg_col = "_등락률계산값"

        # 거래량 0 제거
        df = df[df[vol_col] > 0]

        if df.empty:
            print("[WARN] get_top_stocks: 거래량 0 제거 후 데이터 없음")
            return []

        # 카테고리 분기
        if category == "volume":
            sort_col = vol_col

        elif category == "return":
            sort_col = chg_col
            df = df[df[chg_col] > 0]   # 상승 종목만

        else:
            print(f"[WARN] get_top_stocks: 잘못된 category={category}")
            return []

        if df.empty:
            print(f"[WARN] get_top_stocks: category={category} 조건에 맞는 데이터 없음")
            return []

        top_df = df.nlargest(5, sort_col)

        result = []
        for ticker, row in top_df.iterrows():
            try:
                name = pystock.get_ticker_name(ticker)
            except Exception:
                name = ticker

            change_rate = float(row[chg_col]) if pd.notna(row[chg_col]) else 0.0
            current_price = int(row[close_col]) if pd.notna(row[close_col]) else 0

            result.append({
                "symbol": ticker,
                "company_name": name,
                "current_price": current_price,
                "change_rate": round(change_rate, 2),
            })

        return result

    except Exception as e:
        print(f"[WARN] get_top_stocks 실패: {e}")
        return []

    # ========================================
    # 현재가 조회 및 표시 형식
    # ========================================

    def _get_price_display(self, symbol: str) -> Tuple[str, str, str]:
        """
        현재가 표시 정보 반환

        Returns:
            (price_str, emoji, change_str)
            예: ("72,500원", "🔺", "1.2%")
        """
        if not self._hantu:
            return ("N/A", "➖", "0.0%")

        try:
            data = self._hantu.get_stock_price(symbol)

            if not isinstance(data, dict) or "error" in data:
                return ("N/A", "➖", "0.0%")

            price = data.get("current_price", 0)
            rate = data.get("change_rate", 0.0)

            price_str = f"{price:,}원"

            if rate > 0:
                emoji = "🔺"
                change_str = f"{rate:.1f}%"
            elif rate < 0:
                emoji = "🔻"
                change_str = f"{abs(rate):.1f}%"
            else:
                emoji = "➖"
                change_str = "0.0%"

            return (price_str, emoji, change_str)

        except Exception as e:
            print(f"[WARN] 현재가 조회 실패 ({symbol}): {e}")
            return ("N/A", "➖", "0.0%")

    def _get_favorites_with_price(self, user_id: str) -> List[Dict]:
        """현재가 포함 관심 종목 목록"""
        favorites = self._load(user_id)
        result = []

        for f in favorites:
            price_str, emoji, change_str = self._get_price_display(f["symbol"])
            result.append({
                **f,
                "price_str": price_str,
                "emoji": emoji,
                "change_str": change_str,
            })

        return result

    def _format_favorites_list(self, favorites_with_price: List[Dict]) -> str:
        """관심 종목 리스트 텍스트 생성"""
        lines = []

        for i, f in enumerate(favorites_with_price[:10]):
            emoji = NUM_EMOJIS[i]
            price_str = f.get("price_str", "")
            chg_emoji = f.get("emoji", "")
            change_str = f.get("change_str", "")

            if price_str and price_str != "N/A":
                lines.append(
                    f"{emoji} {f['company_name']} {price_str} ({chg_emoji} {change_str})"
                )
            else:
                lines.append(f"{emoji} {f['company_name']}")

        return "\n".join(lines)

    # ========================================
    # 보유 종목 불러오기
    # ========================================

    def load_holdings_to_favorites(self, user_id: str) -> Dict:
        """
        보유 종목을 관심 종목에 일괄 등록 (빈 공간만큼)
        """
        if not self._hantu:
            return {"success": False, "reason": "no_account"}

        try:
            holdings = self._hantu.get_holding_stock_detail()
        except Exception as e:
            print(f"[WARN] 보유 종목 조회 실패: {e}")
            return {"success": False, "reason": "no_account"}

        if not holdings:
            return {"success": False, "reason": "no_holdings"}

        favorites = self._load(user_id)
        existing_symbols = {f["symbol"] for f in favorites}
        available_slots = self.MAX_FAVORITES - len(favorites)

        added = []
        skipped = []
        not_added = []

        for h in holdings:
            symbol = h["pdno"]
            name = h["prdt_name"]

            if symbol in existing_symbols:
                skipped.append({"symbol": symbol, "company_name": name})
                continue

            if available_slots <= 0:
                not_added.append({"symbol": symbol, "company_name": name})
                continue

            favorites.append({
                "symbol": symbol,
                "company_name": name,
                "added_at": datetime.now().isoformat(),
            })
            added.append({"symbol": symbol, "company_name": name})
            existing_symbols.add(symbol)
            available_slots -= 1

        if added:
            self._save(user_id, favorites)

        return {
            "success": True,
            "added": added,
            "skipped": skipped,
            "not_added": not_added,
            "total_count": len(favorites),
        }

    # ========================================
    # 요약 정보 데이터 수집 (캐로셀 카드 1+2)
    # ========================================

    def get_summary_card_data(self, symbol: str, company_name: str) -> Dict:
        """
        관심 종목 요약 정보 수집 (캐로셀 2장 데이터)
        """
        card1 = self._collect_report_news_card(symbol, company_name)
        card2 = self._collect_transaction_card(symbol)
        return {"card1": card1, "card2": card2}

    def _collect_report_news_card(self, symbol: str, company_name: str) -> Dict:
        """카드 1: 종목 리포트 요약 + 커뮤니티/뉴스"""
        result = {
            "symbol": symbol,
            "company_name": company_name,
            "current_price": 0,
            "price_change": 0,
            "return_1y": None,
            "per": None,
            "pbr": None,
            "roe": None,
            "rsi": "N/A",
            "community_text": None,
            "news_headlines": [],
            "web_url": f"https://jutopia.com/stock/{symbol}",
        }

        # 종목 기본 지표
        try:
            if self._provider:
                info = self._provider.get_stock_info(symbol)
                fund = self._provider.get_fundamental_metrics(symbol)
                tech = self._provider.get_technical_indicators(symbol)

                result["current_price"] = info.get("current_price", 0)
                result["price_change"] = info.get("price_change", 0)
                result["per"] = fund.get("per")
                result["pbr"] = fund.get("pbr")
                result["roe"] = fund.get("roe")

                rsi_info = tech.get("rsi", {})
                rsi_signal = rsi_info.get("signal", {})
                result["rsi"] = (
                    rsi_signal.get("description", "N/A")
                    if isinstance(rsi_signal, dict)
                    else str(rsi_signal)
                )

                # 1년 수익률
                try:
                    df = self._provider.get_historical_data(symbol)
                    if not df.empty and len(df) > 252:
                        current = float(df["close"].iloc[-1])
                        past = float(df["close"].iloc[-252])
                        result["return_1y"] = round((current - past) / past * 100, 1)
                except Exception:
                    pass

        except Exception as e:
            print(f"[WARN] 리포트 데이터 수집 실패 ({symbol}): {e}")

        # 커뮤니티/뉴스 헤드라인
        try:
            if self._news:
                community = self._news.get_community_summary(symbol, company_name)
                result["community_text"] = community.get("summary_text", "")

                news = self._news.get_news_summary(symbol, company_name)
                result["news_headlines"] = [
                    issue["title"]
                    for issue in news.get("key_issues", [])[:2]
                ]

        except Exception as e:
            print(f"[WARN] 뉴스/커뮤니티 데이터 수집 실패 ({symbol}): {e}")

        return result

    def _collect_transaction_card(self, symbol: str) -> Dict:
        """카드 2: 최근 일주일 거래내역 요약"""
        result = {
            "symbol": symbol,
            "no_transaction": True,
            "period_start": "",
            "period_end": "",
            "total_trades": 0,
            "buy_trades": 0,
            "sell_trades": 0,
            "buy_amount": 0,
            "sell_amount": 0,
            "realized_profit": 0,
        }

        if not self._hantu:
            return result

        try:
            today = datetime.now()
            week_ago = today - timedelta(days=7)
            start_date = week_ago.strftime("%Y%m%d")
            end_date = today.strftime("%Y%m%d")

            all_tx = self._hantu.get_transaction_history(
                start_date=start_date,
                end_date=end_date
            )
            tx = [t for t in all_tx if t["pdno"] == symbol]

            if not tx:
                result["period_start"] = week_ago.strftime("%Y.%m.%d")
                result["period_end"] = today.strftime("%Y.%m.%d")
                return result

            dates = sorted(t["ord_dt"] for t in tx)
            result["period_start"] = f"{dates[0][:4]}.{dates[0][4:6]}.{dates[0][6:]}"
            result["period_end"] = f"{dates[-1][:4]}.{dates[-1][4:6]}.{dates[-1][6:]}"
            result["no_transaction"] = False

            for t in tx:
                amt = t["tot_ccld_amt"]

                if t["sll_buy_dvsn_cd"] == "02":
                    result["buy_trades"] += 1
                    result["buy_amount"] += amt
                else:
                    result["sell_trades"] += 1
                    result["sell_amount"] += amt

            result["total_trades"] = len(tx)
            result["realized_profit"] = result["sell_amount"] - result["buy_amount"]

        except Exception as e:
            print(f"[WARN] 거래내역 수집 실패 ({symbol}): {e}")

        return result

    # ========================================
    # 카카오톡 포맷
    # ========================================

    def format_entry_for_kakao(self, user_id: str, user_name: str) -> Dict:
        """
        기능 진입 화면 (Section 1 — Case 1/2/3 자동 분기)
        """
        favorites = self._get_favorites_with_price(user_id)
        n = len(favorites)

        if n == 0:
            text = (
                f"{user_name}님, 아직 등록된 관심 종목이 없어요. 😵\n\n"
                "관심 있는 종목을 등록하면,\n"
                "실시간 시세와 리포트를 더 편하게 확인할 수 있어요.\n\n"
                "종목을 직접 검색할 수 있고,\n"
                "아직 종목을 잘 모른다면,\n"
                "거래량과 상승률이 높은 종목을 추천해 드릴 수 있어요 !\n\n"
                "⚠️관심 종목은 최대 10개까지 등록 가능해요."
            )
            quick_replies = [
                {"action": "block", "label": "종목 검색 후 추가", "messageText": "종목 검색 추가", "blockId": "favorite_search_block"},
                {"action": "block", "label": "추천 종목 보기", "messageText": "추천 종목", "blockId": "favorite_recommend_block"},
                {"action": "block", "label": "보유 종목 불러오기", "messageText": "보유 종목 불러오기", "blockId": "favorite_load_holdings_block"},
                {"action": "block", "label": "메인으로", "messageText": "메인으로", "blockId": "main_block"},
            ]

        elif n < self.MAX_FAVORITES:
            list_text = self._format_favorites_list(favorites)
            text = (
                f"▪️{user_name}님의 관심 종목 리스트예요 !\n\n"
                f"📁 내 관심 종목\n{list_text}\n\n"
                "요약 정보를 보고 싶은 종목을 입력하여\n"
                "새로운 종목을 관심 종목으로 추가해보세요 !\n\n"
                "거래량이나 상승률이 높은 종목을 추천해 드릴 수도 있어요 :)\n"
                "해당 종목이 궁금하면 \"추천 종목 확인\"을 눌러주세요 !\n\n"
                "⚠️ 관심 종목은 최대 10개까지 등록 가능해요."
            )
            quick_replies = [
                {"action": "block", "label": "관심 종목 요약 정보", "messageText": "관심 종목 요약", "blockId": "favorite_summary_block"},
                {"action": "block", "label": "종목 검색 추가", "messageText": "종목 검색 추가", "blockId": "favorite_search_block"},
                {"action": "block", "label": "추천 종목 확인", "messageText": "추천 종목", "blockId": "favorite_recommend_block"},
                {"action": "block", "label": "관심 종목 삭제", "messageText": "관심 종목 삭제", "blockId": "favorite_delete_block"},
                {"action": "block", "label": "보유 종목 불러오기", "messageText": "보유 종목 불러오기", "blockId": "favorite_load_holdings_block"},
                {"action": "block", "label": "메인으로", "messageText": "메인으로", "blockId": "main_block"},
            ]

        else:
            list_text = self._format_favorites_list(favorites)
            text = (
                f"▪️{user_name}님의 관심 종목 리스트예요 !\n\n"
                f"📁 내 관심 종목\n{list_text}\n\n"
                "현재 10개의 종목이 관심 종목으로 등록되어 있어요.\n\n"
                "관심 종목은 최대 10개까지 등록 가능하므로\n"
                "\"관심 종목 삭제\"를 눌러 종목을 삭제 후 다시 이용해주세요 :)\n\n"
                "관심 종목으로 등록되어 있는 종목은\n"
                "요약 정보를 제공하여 간편하게 이용 가능해요 !"
            )
            quick_replies = [
                {"action": "block", "label": "관심 종목 요약 정보", "messageText": "관심 종목 요약", "blockId": "favorite_summary_block"},
                {"action": "block", "label": "관심 종목 삭제", "messageText": "관심 종목 삭제", "blockId": "favorite_delete_block"},
                {"action": "block", "label": "보유 종목 불러오기", "messageText": "보유 종목 불러오기", "blockId": "favorite_load_holdings_block"},
                {"action": "block", "label": "메인으로", "messageText": "메인으로", "blockId": "main_block"},
            ]

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": text}}],
                "quickReplies": quick_replies,
            },
        }

    def format_search_result_for_kakao(
        self,
        symbol: str,
        company_name: str,
        user_name: str,
        card1: Dict,
    ) -> Dict:
        """
        종목 검색 결과 + 요약 정보
        """
        price = card1.get("current_price", 0)
        change = card1.get("price_change", 0)
        change_sign = "+" if change >= 0 else ""
        return_1y = card1.get("return_1y")
        per = card1.get("per")
        pbr = card1.get("pbr")
        roe = card1.get("roe")
        rsi = card1.get("rsi", "N/A")

        metrics_parts = []
        if per is not None:
            metrics_parts.append(f"PER {per}")
        if pbr is not None:
            metrics_parts.append(f"PBR {pbr}")
        if roe is not None:
            metrics_parts.append(f"ROE {roe}")

        metrics_str = " / ".join(metrics_parts) if metrics_parts else "N/A"

        text = (
            f"▪️{user_name}님이 검색한 {company_name}을 찾았어요 !\n\n"
            "해당 종목에 대해 간단히 설명드릴게요.\n\n"
            "[요약 정보]\n"
            f"▪️ {company_name}({symbol}) 종목 리포트 요약이에요!\n\n"
            f"• 현재가 : {price:,}원 ({change_sign}{change:,}원)\n"
        )

        if return_1y is not None:
            sign = "+" if return_1y >= 0 else ""
            text += f"• 1년 수익률 : {sign}{return_1y}%\n"

        text += f"• 주요 지표 : {metrics_str}\n"
        text += f"• 기술적 지표(RSI) : {rsi}\n"

        community_text = card1.get("community_text")
        if community_text:
            text += "\n-------------------------------------\n\n"
            text += f"📍 커뮤니티 분위기\n\n{community_text}\n"

        news_headlines = card1.get("news_headlines", [])
        if news_headlines:
            text += f"\n📍 {company_name} 관련 주요 뉴스\n\n"
            for headline in news_headlines:
                text += f"• {headline}\n"

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": text.rstrip()}}],
                "quickReplies": [
                    {"action": "block", "label": "관심 종목 등록", "messageText": f"{company_name} 관심 등록", "blockId": "favorite_add_block"},
                    {"action": "block", "label": "다른 종목 검색", "messageText": "종목 검색 추가", "blockId": "favorite_search_block"},
                    {"action": "block", "label": "추천 종목 확인", "messageText": "추천 종목", "blockId": "favorite_recommend_block"},
                    {"action": "block", "label": "메인으로", "messageText": "메인으로", "blockId": "main_block"},
                ],
            },
        }

    def format_add_complete_for_kakao(
        self, user_id: str, user_name: str, company_name: str
    ) -> Dict:
        """
        관심 종목 등록 완료
        """
        favorites = self._get_favorites_with_price(user_id)
        n = len(favorites)
        list_text = self._format_favorites_list(favorites)

        if n >= self.MAX_FAVORITES:
            text = (
                f"▪️{user_name}님의 관심 종목에 {company_name}을 추가했어요 !\n\n"
                f"📁 내 관심 종목\n{list_text}\n\n"
                "현재 10개의 종목이 관심 종목으로 등록되어 있어요 :)\n\n"
                "관심 종목은 최대 10개까지 등록 가능하기 때문에\n"
                "다른 종목을 추가하려면 기존에 등록되어 있는 관심 종목을 삭제한 후 추가해주세요 !"
            )
            quick_replies = [
                {"action": "block", "label": "관심 종목 삭제", "messageText": "관심 종목 삭제", "blockId": "favorite_delete_block"},
                {"action": "block", "label": "메인으로", "messageText": "메인으로", "blockId": "main_block"},
            ]
        else:
            text = (
                f"▪️{user_name}님의 관심 종목에 {company_name}을 추가했어요 !\n\n"
                f"📁 내 관심 종목\n{list_text}\n\n"
                f"현재 {n}개의 종목이 관심 종목으로 등록되어 있어요 :)\n\n"
                "관심 종목은 최대 10개까지 등록 가능하며,\n"
                "종목을 추가하거나 삭제하고 싶으면 하단의 버튼을 이용해주세요 !"
            )
            quick_replies = [
                {"action": "block", "label": "관심 종목 등록", "messageText": "종목 검색 추가", "blockId": "favorite_search_block"},
                {"action": "block", "label": "관심 종목 삭제", "messageText": "관심 종목 삭제", "blockId": "favorite_delete_block"},
                {"action": "block", "label": "메인으로", "messageText": "메인으로", "blockId": "main_block"},
            ]

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": text}}],
                "quickReplies": quick_replies,
            },
        }

    def format_top_stocks_for_kakao(self, stocks: List[Dict], category: str) -> Dict:
        """
        추천 종목 리스트 출력
        """
        category_label = "거래량" if category == "volume" else "상승률"
        opposite_label = "상승률 TOP5" if category == "volume" else "거래량 TOP5"
        opposite_action = "return" if category == "volume" else "volume"

        lines = []
        for i, s in enumerate(stocks[:5]):
            emoji = NUM_EMOJIS[i]
            chg = s["change_rate"]
            chg_emoji = "🔺" if chg > 0 else ("🔻" if chg < 0 else "➖")
            chg_str = f"{abs(chg):.1f}%"
            price_str = f"{s['current_price']:,}원"
            lines.append(f"{emoji} {s['company_name']} ({price_str} / {chg_emoji} {chg_str})")

        list_text = "\n".join(lines)
        text = (
            f"{category_label}을 기준으로 상위 5개 종목이에요!\n\n"
            f"📁[{category_label} TOP 5]\n\n"
            f"{list_text}\n\n"
            "💡 마음에 드는 종목이 있다면 관심 종목으로 등록해보세요!"
        )

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": text}}],
                "quickReplies": [
                    {"action": "block", "label": "관심 종목 추가", "messageText": "종목 검색 추가", "blockId": "favorite_search_block"},
                    {"action": "block", "label": opposite_label, "messageText": f"추천 {opposite_action}", "blockId": "favorite_recommend_block"},
                    {"action": "block", "label": "메인으로", "messageText": "메인으로", "blockId": "main_block"},
                ],
            },
        }

    def format_delete_complete_for_kakao(
        self, user_id: str, user_name: str, company_name: str
    ) -> Dict:
        """관심 종목 삭제 완료"""
        favorites = self._get_favorites_with_price(user_id)
        n = len(favorites)
        list_text = self._format_favorites_list(favorites)

        list_section = f"📁 내 관심 종목\n{list_text}\n\n" if favorites else ""

        text = (
            f"▪️{user_name}님의 관심 종목인 {company_name}을 삭제했어요.\n\n"
            "현재 등록되어 있는 관심 종목은 아래와 같아요.\n\n"
            f"{list_section}"
            f"현재 {n}개의 종목이 관심 종목으로 등록되어 있어요 :)\n\n"
            "관심 종목은 최대 10개까지 등록 가능하며,\n"
            "종목을 추가하거나 삭제하고 싶으면 하단의 버튼을 이용해주세요 !"
        )

        quick_replies = [
            {"action": "block", "label": "종목 검색 후 추가", "messageText": "종목 검색 추가", "blockId": "favorite_search_block"},
            {"action": "block", "label": "추천 종목 보기", "messageText": "추천 종목", "blockId": "favorite_recommend_block"},
        ]

        if n > 0:
            quick_replies.append(
                {"action": "block", "label": "관심 종목 삭제", "messageText": "관심 종목 삭제", "blockId": "favorite_delete_block"}
            )

        quick_replies.append(
            {"action": "block", "label": "메인으로", "messageText": "메인으로", "blockId": "main_block"}
        )

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": text}}],
                "quickReplies": quick_replies,
            },
        }

    def format_summary_carousel_for_kakao(
        self,
        symbol: str,
        company_name: str,
        card1: Dict,
        card2: Dict,
    ) -> Dict:
        """
        관심 종목 요약 정보 캐로셀
        """
        price = card1.get("current_price", 0)
        change = card1.get("price_change", 0)
        change_sign = "+" if change >= 0 else ""
        return_1y = card1.get("return_1y")
        per = card1.get("per")
        pbr = card1.get("pbr")
        roe = card1.get("roe")
        rsi = card1.get("rsi", "N/A")

        metrics_parts = []
        if per is not None:
            metrics_parts.append(f"PER {per}")
        if pbr is not None:
            metrics_parts.append(f"PBR {pbr}")
        if roe is not None:
            metrics_parts.append(f"ROE {roe}")

        metrics_str = " / ".join(metrics_parts) if metrics_parts else "N/A"

        card1_desc = (
            f"▪️ {company_name}({symbol}) 종목 리포트 요약이에요!\n\n"
            f"• 현재가 : {price:,}원 ({change_sign}{change:,}원)\n"
        )

        if return_1y is not None:
            sign = "+" if return_1y >= 0 else ""
            card1_desc += f"• 1년 수익률 : {sign}{return_1y}%\n"

        card1_desc += f"• 주요 지표 : {metrics_str}\n"
        card1_desc += f"• 기술적 지표(RSI) : {rsi}"

        community_text = card1.get("community_text")
        if community_text:
            card1_desc += f"\n\n-------------------------------------\n\n📍 커뮤니티 분위기\n\n{community_text}"

        news_headlines = card1.get("news_headlines", [])
        if news_headlines:
            card1_desc += f"\n\n📍 {company_name} 관련 주요 뉴스\n\n"
            card1_desc += "\n".join(f"• {h}" for h in news_headlines)

        web_url = card1.get("web_url", f"https://jutopia.com/stock/{symbol}")

        if card2.get("no_transaction"):
            card2_desc = (
                f"📑 {company_name} 거래내역 요약이에요!\n\n"
                "• 최근 거래 내역이 없어요"
            )
        else:
            buy_amt = card2.get("buy_amount", 0)
            sell_amt = card2.get("sell_amount", 0)
            profit = card2.get("realized_profit", 0)
            profit_sign = "+" if profit >= 0 else ""

            card2_desc = (
                f"📑 {company_name} 거래내역 요약이에요!\n\n"
                f"• 거래 기간 : 최근 일주일\n"
                f"• 거래 횟수 : 총 {card2['total_trades']}회 "
                f"(매수 {card2['buy_trades']}회/ 매도 {card2['sell_trades']}회)\n"
                f"• 총 매수금액 : {buy_amt:,.0f}원\n"
                f"• 총 매도금액 : {sell_amt:,.0f}원\n"
                f"• 실현손익(매도 기준) : {profit_sign}{profit:,.0f}원"
            )

        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "carousel": {
                            "type": "basicCard",
                            "items": [
                                {
                                    "title": f"{company_name} 종목 리포트 & 뉴스",
                                    "description": card1_desc,
                                    "buttons": [
                                        {
                                            "action": "webLink",
                                            "label": "종목 상세 페이지로 이동",
                                            "webLinkUrl": web_url,
                                        }
                                    ],
                                },
                                {
                                    "title": f"{company_name} 거래내역 요약",
                                    "description": card2_desc,
                                    "buttons": [
                                        {
                                            "action": "webLink",
                                            "label": "거래내역 자세히 보기",
                                            "webLinkUrl": f"https://jutopia.com/report/{symbol}",
                                        }
                                    ],
                                },
                            ],
                        }
                    }
                ],
                "quickReplies": [
                    {"action": "block", "label": "다른 종목 요약 보기", "messageText": "관심 종목 요약", "blockId": "favorite_summary_block"},
                    {"action": "block", "label": "메인으로", "messageText": "메인으로", "blockId": "main_block"},
                ],
            },
        }

    def format_holdings_loaded_for_kakao(
        self, user_id: str, user_name: str, result: Dict
    ) -> Dict:
        """보유 종목 불러오기 결과"""
        favorites = self._get_favorites_with_price(user_id)
        n = len(favorites)
        list_text = self._format_favorites_list(favorites)
        not_added = result.get("not_added", [])

        if not not_added:
            text = (
                f"▪️{user_name}님의 보유 종목을 관심 종목 리스트로 모두 불러왔어요!\n\n"
                f"📁 내 관심 종목\n{list_text}\n\n"
                "관심 종목의 요약 정보를 확인하시려면 하단의 버튼을 이용해주세요 :)"
            )
            quick_replies = [
                {"action": "block", "label": "관심 종목 요약 정보", "messageText": "관심 종목 요약", "blockId": "favorite_summary_block"},
                {"action": "block", "label": "관심 종목 삭제", "messageText": "관심 종목 삭제", "blockId": "favorite_delete_block"},
                {"action": "block", "label": "메인으로", "messageText": "메인으로", "blockId": "main_block"},
            ]
        else:
            text = (
                f"▪️{user_name}님의 보유 종목과 관심 종목은 총 {n}개에요.\n\n"
                f"📁 내 관심 종목 & 보유 종목\n\n{list_text}\n\n"
                "모든 보유 종목을 관심 종목에 추가할 수 없어요.\n\n"
                "보유 종목 중 추가하고 싶은 종목이 있으면,\n"
                "하단의 [종목 직접 입력]을 눌러 관심 종목으로 등록해주세요 !"
            )
            quick_replies = [
                {"action": "block", "label": "종목 직접 입력", "messageText": "종목 검색 추가", "blockId": "favorite_search_block"},
                {"action": "block", "label": "관심 종목 삭제", "messageText": "관심 종목 삭제", "blockId": "favorite_delete_block"},
                {"action": "block", "label": "관심 종목 요약 정보", "messageText": "관심 종목 요약", "blockId": "favorite_summary_block"},
                {"action": "block", "label": "추천 종목 확인", "messageText": "추천 종목", "blockId": "favorite_recommend_block"},
                {"action": "block", "label": "메인으로", "messageText": "메인으로", "blockId": "main_block"},
            ]

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": text}}],
                "quickReplies": quick_replies,
            },
        }

    def _kakao_error(self, message: str) -> Dict:
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": f"❌ {message}\n잠시 후 다시 시도해주세요."
                        }
                    }
                ]
            },
        }

