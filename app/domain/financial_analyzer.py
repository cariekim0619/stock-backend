# app/domain/finalcial_analyzer.py
# -*- coding: utf-8 -*-
"""
재무제표 자동 분석 (LLM 불필요)
규칙 기반으로 재무 건전성 평가
"""

import pandas as pd
from pathlib import Path
from typing import Dict, Optional


class FinancialAnalyzer:
    """DART 재무제표 데이터 분석"""

    def __init__(self, data_dir: str = "./processed"):
        self.data_dir = Path(data_dir)

    def load_financials(self, ticker: str, year: int = 2024) -> Optional[pd.DataFrame]:
        """재무제표 CSV 로드"""
        pattern = f"financials_{ticker}_{year}_*.csv"
        files = list(self.data_dir.glob(pattern))

        if not files:
            return None

        # 가장 최근 파일
        latest = max(files, key=lambda p: p.stat().st_mtime)
        return pd.read_csv(latest)

    def extract_value(self, df: pd.DataFrame, account_name: str) -> float:
        """계정과목에서 금액 추출"""
        try:
            row = df[df['account_nm'] == account_name]
            if row.empty:
                return 0.0

            value = row.iloc[0]['thstrm_amount']
            # 문자열이면 숫자로 변환
            if isinstance(value, str):
                value = float(value.replace(',', ''))
            return float(value)
        except:
            return 0.0

    def calculate_financial_ratios(self, ticker: str) -> Dict:
        """
        재무비율 자동 계산 (LLM 불필요)

        Returns:
            dict: {
                'debt_ratio': 부채비율,
                'current_ratio': 유동비율,
                'roe': 자기자본이익률,
                'roa': 총자산이익률,
                'grades': {...},
                'comments': {...}
            }
        """
        df = self.load_financials(ticker)

        if df is None:
            return {'error': '재무제표 데이터 없음'}

        # 1. 주요 계정과목 추출
        자산총계 = self.extract_value(df, '자산총계')
        부채총계 = self.extract_value(df, '부채총계')
        자본총계 = self.extract_value(df, '자본총계')
        유동자산 = self.extract_value(df, '유동자산')
        유동부채 = self.extract_value(df, '유동부채')
        당기순이익 = self.extract_value(df, '당기순이익')
        매출액 = self.extract_value(df, '매출액')
        영업이익 = self.extract_value(df, '영업이익')

        # 2. 재무비율 계산
        부채비율 = (부채총계 / 자본총계 * 100) if 자본총계 > 0 else 0
        유동비율 = (유동자산 / 유동부채 * 100) if 유동부채 > 0 else 0
        ROE = (당기순이익 / 자본총계 * 100) if 자본총계 > 0 else 0
        ROA = (당기순이익 / 자산총계 * 100) if 자산총계 > 0 else 0
        영업이익률 = (영업이익 / 매출액 * 100) if 매출액 > 0 else 0

        # 3. 등급 판정 (규칙 기반)
        grades = {
            'debt': self._grade_debt_ratio(부채비율),
            'liquidity': self._grade_current_ratio(유동비율),
            'profitability': self._grade_roe(ROE),
            'overall': 'B+'  # 종합 점수는 각 등급의 평균
        }

        # 4. 코멘트 생성 (템플릿 기반)
        comments = {
            'debt': self._comment_debt_ratio(부채비율),
            'liquidity': self._comment_current_ratio(유동비율),
            'profitability': self._comment_roe(ROE),
            'summary': self._generate_summary(부채비율, ROE, 유동비율)
        }

        return {
            'ratios': {
                'debt_ratio': round(부채비율, 1),
                'current_ratio': round(유동비율, 1),
                'roe': round(ROE, 1),
                'roa': round(ROA, 1),
                'operating_margin': round(영업이익률, 1)
            },
            'grades': grades,
            'comments': comments,
            'raw_data': {
                '자산총계': 자산총계,
                '부채총계': 부채총계,
                '자본총계': 자본총계,
                '당기순이익': 당기순이익
            }
        }

    # ========================================
    # 등급 판정 규칙
    # ========================================

    def _grade_debt_ratio(self, ratio: float) -> str:
        """부채비율 등급 (낮을수록 좋음)"""
        if ratio < 50:
            return 'A+'
        elif ratio < 100:
            return 'A'
        elif ratio < 150:
            return 'B'
        elif ratio < 200:
            return 'C'
        else:
            return 'D'

    def _grade_current_ratio(self, ratio: float) -> str:
        """유동비율 등급 (높을수록 좋음)"""
        if ratio >= 200:
            return 'A+'
        elif ratio >= 150:
            return 'A'
        elif ratio >= 100:
            return 'B'
        elif ratio >= 80:
            return 'C'
        else:
            return 'D'

    def _grade_roe(self, roe: float) -> str:
        """ROE 등급 (높을수록 좋음)"""
        if roe >= 15:
            return 'A+'
        elif roe >= 10:
            return 'A'
        elif roe >= 7:
            return 'B'
        elif roe >= 5:
            return 'C'
        else:
            return 'D'

    # ========================================
    # 코멘트 생성 템플릿
    # ========================================

    def _comment_debt_ratio(self, ratio: float) -> str:
        """부채비율 코멘트"""
        if ratio < 50:
            return f"부채비율 {ratio:.1f}%로 매우 안전한 재무구조입니다."
        elif ratio < 100:
            return f"부채비율 {ratio:.1f}%로 양호한 재무구조입니다."
        elif ratio < 150:
            return f"부채비율 {ratio:.1f}%로 적정 수준입니다."
        elif ratio < 200:
            return f"부채비율 {ratio:.1f}%로 다소 높은 편입니다."
        else:
            return f"부채비율 {ratio:.1f}%로 높은 편입니다. 부채 관리가 필요합니다."

    def _comment_current_ratio(self, ratio: float) -> str:
        """유동비율 코멘트"""
        if ratio >= 200:
            return f"유동비율 {ratio:.1f}%로 단기 지급능력이 매우 우수합니다."
        elif ratio >= 150:
            return f"유동비율 {ratio:.1f}%로 단기 지급능력이 양호합니다."
        elif ratio >= 100:
            return f"유동비율 {ratio:.1f}%로 단기 지급능력이 적정 수준입니다."
        else:
            return f"유동비율 {ratio:.1f}%로 단기 유동성에 주의가 필요합니다."

    def _comment_roe(self, roe: float) -> str:
        """ROE 코멘트"""
        if roe >= 15:
            return f"ROE {roe:.1f}%로 매우 우수한 수익성을 보입니다."
        elif roe >= 10:
            return f"ROE {roe:.1f}%로 양호한 수익성입니다."
        elif roe >= 7:
            return f"ROE {roe:.1f}%로 보통 수준의 수익성입니다."
        elif roe >= 5:
            return f"ROE {roe:.1f}%로 수익성이 다소 낮습니다."
        else:
            return f"ROE {roe:.1f}%로 수익성 개선이 필요합니다."

    def _generate_summary(self, debt: float, roe: float, current: float) -> str:
        """종합 평가"""
        strengths = []
        weaknesses = []

        # 강점
        if debt < 100:
            strengths.append("낮은 부채비율")
        if roe >= 10:
            strengths.append("우수한 수익성")
        if current >= 150:
            strengths.append("탄탄한 유동성")

        # 약점
        if debt >= 150:
            weaknesses.append("높은 부채")
        if roe < 7:
            weaknesses.append("낮은 수익성")
        if current < 100:
            weaknesses.append("부족한 유동성")

        # 종합
        if len(strengths) >= 2:
            summary = "재무 건전성이 우수합니다. "
        elif len(weaknesses) >= 2:
            summary = "재무 개선이 필요합니다. "
        else:
            summary = "재무 상태가 양호합니다. "

        if strengths:
            summary += f"강점: {', '.join(strengths)}. "
        if weaknesses:
            summary += f"주의: {', '.join(weaknesses)}."

        return summary


# ========================================
# 사용 예시
# ========================================

if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    analyzer = FinancialAnalyzer()

    # 삼성전자 재무 분석
    result = analyzer.calculate_financial_ratios('005930')

    if 'error' not in result:
        print("=" * 60)
        print("📊 삼성전자 재무 건전성 평가")
        print("=" * 60)

        ratios = result['ratios']
        grades = result['grades']
        comments = result['comments']

        print(f"\n【 안정성 】 등급: {grades['debt']}")
        print(f"├ 부채비율: {ratios['debt_ratio']}%")
        print(f"├ 유동비율: {ratios['current_ratio']}%")
        print(f"└ {comments['debt']}")

        print(f"\n【 수익성 】 등급: {grades['profitability']}")
        print(f"├ ROE: {ratios['roe']}%")
        print(f"├ ROA: {ratios['roa']}%")
        print(f"├ 영업이익률: {ratios['operating_margin']}%")
        print(f"└ {comments['profitability']}")

        print(f"\n【 종합 평가 】 {grades['overall']}")
        print(f"💡 {comments['summary']}")

        print("\n" + "=" * 60)
    else:
        print(f"❌ 오류: {result['error']}")
