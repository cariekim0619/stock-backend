# app/domain/metrics_calculator.py

from typing import Dict, Optional
import pandas as pd

class MetricsCalculator:
    """재무제표 기반 밸류에이션 지표 계산기"""

    # -----------------------------
    # 계정명 키 매핑(확장 버전)
    # -----------------------------
    NET_INCOME_KEYS = [
        "지배기업의 소유주에게 귀속되는 당기순이익",
        "지배기업의 소유주에게 귀속되는 당기순이익(손실)",
        "지배기업 소유지분",  # 삼성전자형 이름
        "당기순이익(손실)",
        "당기순이익",
    ]

    EQUITY_KEYS = [
        "지배기업의 소유주에게 귀속되는 자본",
        "지배기업 소유지분",   # 삼성전자형 이름
        "자본총계",
    ]

    EPS_KEYS = [
        "기본주당순이익",  # 일반 케이스
        "기본주당이익",    # 삼성전자형 이름
    ]

    @staticmethod
    def _extract_value_by_keys(df, keys):
        for key in keys:
            row = df[df['account_nm'].str.contains(key, na=False)]
            if not row.empty:
                try:
                    return float(row.iloc[0]['thstrm_amount'])
                except:
                    pass
        return 0

    @staticmethod
    def calculate_from_dataframe(df: pd.DataFrame, current_price: float):
        try:
            # 주요 값 추출
            net_income = MetricsCalculator._extract_value_by_keys(df, MetricsCalculator.NET_INCOME_KEYS)
            equity = MetricsCalculator._extract_value_by_keys(df, MetricsCalculator.EQUITY_KEYS)
            eps = MetricsCalculator._extract_value_by_keys(df, MetricsCalculator.EPS_KEYS)

            if eps == 0 or net_income == 0 or equity == 0:
                return None

            # 발행주식수 = 순이익 / EPS
            shares = net_income / eps

            bps = equity / shares if shares > 0 else 0

            metrics = {
                "per": round(current_price / eps, 2),
                "pbr": round(current_price / bps, 2) if bps > 0 else None,
                "roe": round((net_income / equity) * 100, 2),
                "eps": int(eps),
                "bps": int(bps),
            }

            return metrics

        except Exception as e:
            print("❌ MetricsCalculator 실패:", e)
            return None
