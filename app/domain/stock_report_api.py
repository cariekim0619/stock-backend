# app/domain/stock_report_api.py
"""
종목 리포트 API
Stock Report API for Backend Integration

This module provides comprehensive stock analysis data for the chatbot backend.
백엔드팀이 호출할 데이터 API입니다.
"""

from typing import Dict, Optional, List
from datetime import datetime, timedelta
from app.domain.stock_analyzer import StockAnalyzer
from app.domain.financial_analyzer import FinancialAnalyzer
import FinanceDataReader as fdr


class StockReportAPI:
    """종목 리포트 데이터 API - 백엔드팀용"""

    def __init__(self):
        self.analyzer = StockAnalyzer(use_realtime=True)
        self.financial = FinancialAnalyzer()

    # ========================================
    # 1. 종합 리포트 (All-in-One)
    # ========================================

    def get_complete_report(self, ticker: str) -> Dict:
        """
        종목 리포트 전체 데이터 반환

        Returns:
            {
                'basic': {...},         # 기본 정보
                'price_trend': {...},   # 가격 흐름 (1m/3m/1y 수익률)
                'chart': {...},         # 차트 데이터 (나중에 이미지 URL)
                'metrics': {...},       # 핵심 지표 (PER/PBR/ROE/배당)
                'technical': {...},     # 기술적 분석
                'financial': {...},     # 재무제표 3년 추세
                'filings': [...]        # 최근 공시
            }
        """
        return {
            'basic': self.get_basic_info(ticker),
            'price_trend': self.get_price_trend(ticker),
            'chart': self.get_chart_data(ticker),
            'metrics': self.get_key_metrics(ticker),
            'technical': self.get_technical_analysis(ticker),
            'financial': self.get_financial_trend(ticker),
            'filings': self.get_recent_filings(ticker)
        }

    # ========================================
    # 2. 기본 정보
    # ========================================

    def get_basic_info(self, ticker: str) -> Dict:
        """
        종목 기본 정보

        Returns:
            {
                'ticker': '005930',
                'name': '삼성전자',
                'current_price': 71300,
                'price_change': 1200,
                'price_change_pct': 1.71,
                'volume': 12345678,
                'market_cap': 420000000000000,  # 시가총액 (원)
                'market_cap_rank': 1             # 시총 순위
            }
        """
        try:
            # 주가 데이터 로드
            df = self.analyzer.load_price_data(ticker, days=5)

            if df.empty:
                return {'error': f'종목 {ticker} 데이터를 찾을 수 없습니다'}

            # 종목명
            name = self.analyzer.get_stock_name(ticker)

            # 현재가 및 변동
            current_price = df.iloc[-1]['close']
            prev_price = df.iloc[-2]['close'] if len(df) > 1 else current_price
            price_change = current_price - prev_price
            price_change_pct = (price_change / prev_price * 100) if prev_price > 0 else 0
            volume = df.iloc[-1]['volume']

            # 시가총액 계산
            market_cap = self._get_market_cap(ticker, current_price)

            return {
                'ticker': ticker,
                'name': name,
                'current_price': int(current_price),
                'price_change': int(price_change),
                'price_change_pct': round(price_change_pct, 2),
                'volume': int(volume),
                'market_cap': market_cap,
                'market_cap_rank': self._get_market_cap_rank(ticker)
            }

        except Exception as e:
            return {'error': f'기본 정보 조회 실패: {str(e)}'}

    # ========================================
    # 3. 가격 흐름 (Price Trend)
    # ========================================

    def get_price_trend(self, ticker: str) -> Dict:
        """
        가격 흐름 데이터 (1개월/3개월/1년 수익률)

        Returns:
            {
                '1m': 4.9,          # 1개월 수익률 (%)
                '3m': 12.4,         # 3개월 수익률 (%)
                '1y': 7.1,          # 1년 수익률 (%)
                'ytd': 15.2,        # 연초 대비 수익률 (%)
                'from_high': -15.3, # 52주 최고가 대비 (%)
                'from_low': 23.1,   # 52주 최저가 대비 (%)
                '52w_high': 85000,  # 52주 최고가
                '52w_low': 58000    # 52주 최저가
            }
        """
        try:
            # 1년치 데이터 로드
            df = self.analyzer.load_price_data(ticker, days=365)

            if df.empty:
                return {'error': '가격 데이터 없음'}

            current_price = df.iloc[-1]['close']

            # 수익률 계산
            returns = {}

            # 1개월 수익률 (약 20거래일)
            if len(df) >= 20:
                price_1m_ago = df.iloc[-20]['close']
                returns['1m'] = round((current_price / price_1m_ago - 1) * 100, 2)
            else:
                returns['1m'] = None

            # 3개월 수익률 (약 60거래일)
            if len(df) >= 60:
                price_3m_ago = df.iloc[-60]['close']
                returns['3m'] = round((current_price / price_3m_ago - 1) * 100, 2)
            else:
                returns['3m'] = None

            # 1년 수익률
            if len(df) >= 240:
                price_1y_ago = df.iloc[-240]['close']
                returns['1y'] = round((current_price / price_1y_ago - 1) * 100, 2)
            else:
                returns['1y'] = None

            # 연초 대비 (YTD) - 약 240거래일 기준
            if len(df) >= 240:
                price_ytd = df.iloc[-240]['close']
                returns['ytd'] = round((current_price / price_ytd - 1) * 100, 2)
            else:
                returns['ytd'] = None

            # 52주 최고가/최저가
            high_52w = df['high'].max()
            low_52w = df['low'].min()
            returns['52w_high'] = int(high_52w)
            returns['52w_low'] = int(low_52w)
            returns['from_high'] = round((current_price / high_52w - 1) * 100, 2)
            returns['from_low'] = round((current_price / low_52w - 1) * 100, 2)

            return returns

        except Exception as e:
            return {'error': f'가격 흐름 계산 실패: {str(e)}'}

    # ========================================
    # 4. 핵심 지표 (PER/PBR/ROE/배당)
    # ========================================

    def get_key_metrics(self, ticker: str) -> Dict:
        """
        핵심 투자 지표

        Returns:
            {
                'per': 15.3,          # 주가수익비율
                'pbr': 1.8,           # 주가순자산비율
                'roe': 12.5,          # 자기자본이익률
                'eps': 5200,          # 주당순이익 (원)
                'bps': 42000,         # 주당순자산 (원)
                'dividend_yield': 2.1,# 배당수익률 (%)
                'dividend_per_share': 1500  # 주당배당금 (원)
            }
        """
        try:
            # 현재가
            basic = self.get_basic_info(ticker)
            if 'error' in basic:
                return basic

            current_price = basic['current_price']

            # 재무 데이터에서 계산
            fin_data = self.financial.load_financials(ticker)

            if fin_data is None or fin_data.empty:
                return {
                    'per': 'N/A',
                    'pbr': 'N/A',
                    'roe': 'N/A',
                    'eps': 'N/A',
                    'bps': 'N/A',
                    'dividend_yield': 'N/A',
                    'dividend_per_share': 'N/A'
                }

            # 최신 데이터
            latest = fin_data.iloc[0] if not fin_data.empty else None

            # EPS 계산 (순이익 / 발행주식수)
            net_income = self.financial.extract_value(fin_data, '당기순이익')
            shares_outstanding = self._get_shares_outstanding(ticker)
            eps = (net_income / shares_outstanding) if shares_outstanding > 0 else 0

            # BPS 계산 (자본총계 / 발행주식수)
            equity = self.financial.extract_value(fin_data, '자본총계')
            bps = (equity / shares_outstanding) if shares_outstanding > 0 else 0

            # PER 계산 (주가 / EPS)
            per = (current_price / eps) if eps > 0 else 0

            # PBR 계산 (주가 / BPS)
            pbr = (current_price / bps) if bps > 0 else 0

            # ROE 계산 (이미 financial_analyzer에 있음)
            fin_ratios = self.financial.calculate_financial_ratios(ticker)
            roe = fin_ratios['ratios']['roe'] if 'error' not in fin_ratios else 0

            # 배당 정보 (임시값 - 나중에 DART API로 가져오기)
            dividend_per_share = 1500  # TODO: DART API에서 실제 배당금 조회
            dividend_yield = (dividend_per_share / current_price * 100) if current_price > 0 else 0

            return {
                'per': round(per, 2),
                'pbr': round(pbr, 2),
                'roe': round(roe, 2),
                'eps': int(eps),
                'bps': int(bps),
                'dividend_yield': round(dividend_yield, 2),
                'dividend_per_share': int(dividend_per_share)
            }

        except Exception as e:
            return {'error': f'지표 계산 실패: {str(e)}'}

    # ========================================
    # 5. 기술적 분석
    # ========================================

    def get_technical_analysis(self, ticker: str) -> Dict:
        """
        기술적 분석 (RSI, 이동평균, 볼린저밴드 등)

        Returns:
            {
                'rsi': 45.2,
                'rsi_signal': '중립',
                'ma5': 71500,
                'ma20': 70800,
                'ma60': 69200,
                'trend': '상승 전환 신호',
                'bollinger_upper': 75000,
                'bollinger_middle': 70000,
                'bollinger_lower': 65000,
                'bollinger_position': '중간'
            }
        """
        try:
            technical = self.analyzer.analyze_stock_technical(
                ticker,
                indicators=['rsi', 'bollinger', 'moving_averages']
            )

            # RSI 해석
            rsi = technical.get('rsi', {}).get('rsi', 50)
            if rsi > 70:
                rsi_signal = "과매수"
            elif rsi < 30:
                rsi_signal = "과매도"
            else:
                rsi_signal = "중립"

            # 이동평균 추세
            ma_data = technical.get('moving_averages', {})
            ma5 = ma_data.get('ma5', 0)
            ma20 = ma_data.get('ma20', 0)
            ma60 = ma_data.get('ma60', 0)

            if ma5 > ma20 > ma60:
                trend = "강한 상승 (정배열)"
            elif ma5 < ma20 < ma60:
                trend = "강한 하락 (역배열)"
            elif ma5 > ma20:
                trend = "상승 전환 신호"
            else:
                trend = "하락 또는 횡보"

            # 볼린저밴드
            bb_data = technical.get('bollinger_bands', {})
            current_price = self.analyzer.load_price_data(ticker, days=5).iloc[-1]['close']

            bb_upper = bb_data.get('upper', 0)
            bb_middle = bb_data.get('middle', 0)
            bb_lower = bb_data.get('lower', 0)

            # 볼린저밴드 위치
            if current_price >= bb_upper:
                bb_position = "상단 (과매수 구간)"
            elif current_price <= bb_lower:
                bb_position = "하단 (과매도 구간)"
            else:
                bb_position = "중간 (정상 구간)"

            return {
                'rsi': round(rsi, 1),
                'rsi_signal': rsi_signal,
                'ma5': int(ma5),
                'ma20': int(ma20),
                'ma60': int(ma60),
                'trend': trend,
                'bollinger_upper': int(bb_upper),
                'bollinger_middle': int(bb_middle),
                'bollinger_lower': int(bb_lower),
                'bollinger_position': bb_position
            }

        except Exception as e:
            return {'error': f'기술적 분석 실패: {str(e)}'}

    # ========================================
    # 6. 재무제표 3년 추세
    # ========================================

    def get_financial_trend(self, ticker: str) -> Dict:
        """
        재무제표 3년 추세

        Returns:
            {
                'years': ['2021', '2022', '2023'],
                'revenue': [105, 112, 121],         # 매출액 (조원)
                'operating_profit': [13, 15, 16],  # 영업이익 (조원)
                'net_profit': [10, 12, 13],        # 당기순이익 (조원)
                'revenue_growth': [6.7, 8.0],      # 매출 성장률 (%)
                'profit_margin': [12.4, 13.4, 13.2]  # 영업이익률 (%)
            }
        """
        try:
            # 최근 3년 재무제표
            fin_data = self.financial.load_financials(ticker)

            if fin_data.empty:
                return {'error': '재무 데이터 없음'}

            # 최근 3개 연도 데이터
            years = []
            revenue = []
            operating_profit = []
            net_profit = []
            profit_margin = []

            for i in range(min(3, len(fin_data))):
                row = fin_data.iloc[i]

                # 연도 추출 (rcept_no에서 또는 별도 컬럼)
                year = str(2024 - i)  # TODO: 실제 연도 추출
                years.append(year)

                # 매출액
                rev = self.financial.extract_value(fin_data.iloc[i:i+1], '매출액') / 1_000_000_000_000  # 조원
                revenue.append(round(rev, 1))

                # 영업이익
                op = self.financial.extract_value(fin_data.iloc[i:i+1], '영업이익') / 1_000_000_000_000
                operating_profit.append(round(op, 1))

                # 당기순이익
                np = self.financial.extract_value(fin_data.iloc[i:i+1], '당기순이익') / 1_000_000_000_000
                net_profit.append(round(np, 1))

                # 영업이익률
                margin = (op / rev * 100) if rev > 0 else 0
                profit_margin.append(round(margin, 1))

            # 매출 성장률 계산
            revenue_growth = []
            for i in range(1, len(revenue)):
                if revenue[i] > 0:
                    growth = (revenue[i-1] / revenue[i] - 1) * 100
                    revenue_growth.append(round(growth, 1))

            return {
                'years': years,
                'revenue': revenue,
                'operating_profit': operating_profit,
                'net_profit': net_profit,
                'revenue_growth': revenue_growth,
                'profit_margin': profit_margin
            }

        except Exception as e:
            return {'error': f'재무 추세 분석 실패: {str(e)}'}

    # ========================================
    # 7. 차트 데이터
    # ========================================

    def get_chart_data(self, ticker: str, days: int = 60) -> Dict:
        """
        차트 데이터 (나중에 이미지 URL로 대체)

        Returns:
            {
                'dates': ['2024-01-01', ...],
                'prices': [70000, 71000, ...],
                'volumes': [12000000, ...],
                'chart_url': 'https://...'  # 나중에 S3 이미지 URL
            }
        """
        try:
            df = self.analyzer.load_price_data(ticker, days=days)

            if df.empty:
                return {'error': '차트 데이터 없음'}

            return {
                'dates': [d.strftime('%Y-%m-%d') for d in df.index],
                'prices': [int(p) for p in df['close'].tolist()],
                'volumes': [int(v) for v in df['volume'].tolist()],
                'chart_url': None  # TODO: 차트 이미지 생성 후 S3 업로드
            }

        except Exception as e:
            return {'error': f'차트 데이터 생성 실패: {str(e)}'}

    # ========================================
    # 8. 최근 공시
    # ========================================

    def get_recent_filings(self, ticker: str, limit: int = 5) -> List[Dict]:
        """
        최근 공시 목록

        Returns:
            [
                {
                    'date': '2024-01-15',
                    'title': '주요사항보고서',
                    'type': '공시',
                    'url': 'https://dart.fss.or.kr/...'
                },
                ...
            ]
        """
        # TODO: dart_client.py에서 공시 데이터 로드
        # 현재는 빈 리스트 반환
        return []

    # ========================================
    # Helper Functions
    # ========================================

    def _get_market_cap(self, ticker: str, current_price: float) -> int:
        """시가총액 계산"""
        shares = self._get_shares_outstanding(ticker)
        return int(current_price * shares)

    def _get_shares_outstanding(self, ticker: str) -> int:
        """발행주식수 조회"""
        try:
            # FinanceDataReader에서 종목 정보 조회
            stocks = fdr.StockListing('KRX')
            stock = stocks[stocks['Code'] == ticker]

            if not stock.empty and 'Stocks' in stock.columns:
                return int(stock.iloc[0]['Stocks'])
            else:
                # 기본값 (삼성전자 기준)
                return 6_000_000_000  # 60억주
        except:
            return 6_000_000_000

    def _get_market_cap_rank(self, ticker: str) -> int:
        """시총 순위 조회"""
        try:
            stocks = fdr.StockListing('KRX')
            stocks = stocks.sort_values('Marcap', ascending=False).reset_index(drop=True)
            rank = stocks[stocks['Code'] == ticker].index
            return int(rank[0] + 1) if len(rank) > 0 else 0
        except:
            return 0


# ========================================
# 사용 예시
# ========================================

if __name__ == "__main__":
    import sys
    import io
    import json

    # Windows console encoding fix
    if sys.platform == 'win32':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    api = StockReportAPI()

    print("=== 종목 리포트 API 테스트 ===\n")

    # 삼성전자 전체 리포트
    ticker = '005930'

    print(f"📊 종목: {ticker}\n")
    print("="*50 + "\n")

    # 1. 기본 정보
    print("1️⃣ 기본 정보")
    basic = api.get_basic_info(ticker)
    print(json.dumps(basic, indent=2, ensure_ascii=False))
    print("\n" + "="*50 + "\n")

    # 2. 가격 흐름
    print("2️⃣ 가격 흐름")
    trend = api.get_price_trend(ticker)
    print(json.dumps(trend, indent=2, ensure_ascii=False))
    print("\n" + "="*50 + "\n")

    # 3. 핵심 지표
    print("3️⃣ 핵심 지표 (PER/PBR/ROE)")
    metrics = api.get_key_metrics(ticker)
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    print("\n" + "="*50 + "\n")

    # 4. 기술적 분석
    print("4️⃣ 기술적 분석")
    technical = api.get_technical_analysis(ticker)
    print(json.dumps(technical, indent=2, ensure_ascii=False))
    print("\n" + "="*50 + "\n")

    # 5. 재무 추세
    print("5️⃣ 재무제표 3년 추세")
    financial = api.get_financial_trend(ticker)
    print(json.dumps(financial, indent=2, ensure_ascii=False))
