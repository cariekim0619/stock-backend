from typing import Optional, Tuple
import pandas as pd
from datetime import datetime
from app.clients.dart_client import DartClient   # 경로는 기존 그대로 사용


class DartFinancialLoader:
    """DART API를 통한 재무제표 로딩"""

    def __init__(self, dart_client: DartClient):
        self.dart_client = dart_client

    def load_financials(self, ticker: str) -> Tuple[Optional[str], Optional[pd.DataFrame]]:
        """
        재무제표 조회 및 텍스트 변환

        Args:
            ticker: 종목코드 (예: '005930')

        Returns:
            tuple: (financial_text, financial_df)
        """
        try:
            # 1) 종목코드 → 고유번호 변환
            corp_code = self.dart_client.get_corp_code(ticker)
            if not corp_code:
                print(f"  ❌ 종목코드 {ticker}에 대한 기업 고유번호를 찾을 수 없습니다")
                return None, None

            # 2) 최신 재무제표 조회
            current_year = datetime.now().year
            years_to_try = [current_year, current_year - 1, current_year - 2]

            df = None
            used_year = None

            for year in years_to_try:
                try:
                    tmp = self.dart_client.get_financials(
                        corp_code=corp_code,
                        year=year,
                        reprt_code="11011",  # 사업보고서
                        fs_div="CFS",        # 연결재무제표
                    )
                    if tmp is not None and not tmp.empty:
                        df = tmp
                        used_year = year
                        break
                except Exception as e:
                    print(f"  ❌ {ticker} 재무제표 조회 실패({year}): {e}")

            if df is None or df.empty:
                print(f"  ⚠️  {ticker} 재무제표를 찾을 수 없습니다")
                return None, None

            # 3) DataFrame → 텍스트 변환
            financial_text = self._dataframe_to_text(df, ticker)
            print(f"  ✅ 재무제표 조회 완료 ({used_year}년, {len(df)}개 항목)")

            return financial_text, df

        except Exception as e:
            print(f"  ❌ 재무제표 조회 실패: {e}")
            return None, None

    def _dataframe_to_text(self, df: pd.DataFrame, ticker: str) -> str:
        """
        재무제표 DataFrame을 텍스트로 변환

        Args:
            df: DART API 응답 DataFrame
            ticker: 종목코드

        Returns:
            str: 포맷된 재무제표 텍스트
        """
        lines = [f"# {ticker} 재무제표 (DART API)", ""]

        # 주요 계정과목 추출
        key_accounts = [
            # 포괄손익계산서
            "매출액",
            "영업이익(손실)",
            "당기순이익(손실)",
            "지배기업의 소유주에게 귀속되는 당기순이익(손실)",

            # 재무상태표
            "자산총계",
            "부채총계",
            "자본총계",
            "지배기업의 소유주에게 귀속되는 자본",

            # 현금흐름표
            "영업활동 현금흐름",
            "투자활동 현금흐름",
            "재무활동 현금흐름",

            # 주당정보
            "기본주당순이익(손실)",
        ]

        for account in key_accounts:
            row = df[df["account_nm"] == account]
            if not row.empty:
                try:
                    amount = float(row.iloc[0]["thstrm_amount"])
                    lines.append(f"- {account}: {amount:,.0f}")
                except Exception:
                    pass

        # EPS 특별 처리 (부분 문자열 매칭)
        eps_row = df[df["account_nm"].str.contains("기본주당순이익", na=False)]
        if not eps_row.empty:
            try:
                eps = float(eps_row.iloc[0]["thstrm_amount"])
                lines.append(f"- 주당순이익(EPS): {eps:,.0f}원")
            except Exception:
                pass

        return "\n".join(lines)
