from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, timedelta
import os
from app.utils.ticker_normalizer import normalize_ticker

# -------------------------------------------------------------------
# 0) FinanceDataReader (실시간/준실시간 주가용)
# -------------------------------------------------------------------
try:
    import FinanceDataReader as fdr
except Exception:
    fdr = None

# -------------------------------------------------------------------
# 0-1) DART / 재무제표 기반 지표 계산 모듈
#   - app/clients/dart_client.py
#   - app/domain/dart_financial_loader.py
#   - app/domain/metrics_calculator.py
# -------------------------------------------------------------------
try:
    from app.clients.dart_client import DartClient
    from app.domain.dart_financial_loader import DartFinancialLoader
    from app.domain.metrics_calculator import MetricsCalculator
except Exception as e:
    print("[raw_report_service] DART 모듈 import 실패:", e)
    DartClient = None
    DartFinancialLoader = None
    MetricsCalculator = None

DART_LOADER: Optional["DartFinancialLoader"] = None
METRICS_CALCULATOR: Optional["MetricsCalculator"] = None

if DartClient and DartFinancialLoader and MetricsCalculator:
    dart_key = os.environ.get("DART_API_KEY")
    if dart_key:
        try:
            dart_client = DartClient(api_key=dart_key)
            DART_LOADER = DartFinancialLoader(dart_client)
            METRICS_CALCULATOR = MetricsCalculator()
            print("[raw_report_service] ✅  DART 모듈 초기화 완료")
        except Exception as e:
            print("[raw_report_service] ⚠️ DART 초기화 실패:", e)
    else:
        print("[raw_report_service] ⚠️ DART_API_KEY 미설정")

# -------------------------------------------------------------------
# 1) 최소한의 종목명 -> 코드 매핑 (자주 쓰는 것만 하드코딩)
# -------------------------------------------------------------------
NAME_TO_CODE: Dict[str, str] = {
    "삼성전자": "005930",
    "카카오": "035720",
    "LG에너지솔루션": "373220",
}

# -------------------------------------------------------------------
# 2) 공통 유틸: 티커 정규화 / 포맷터
# -------------------------------------------------------------------

def _fmt_pct(v: Optional[float]) -> str:
    if not isinstance(v, (int, float)):
        return "N/A"
    return f"{v:+.2f}%"


def _fmt_won(v: Optional[float]) -> str:
    if not isinstance(v, (int, float)):
        return "N/A"
    return f"{v:,.0f}원"



# -------------------------------------------------------------------
# 2-1) 재무제표(DataFrame) 파싱 유틸 (DART)
#  - account_nm(계정명), thstrm_amount(당기금액) 기반
# -------------------------------------------------------------------
def _to_float_amount(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip()
        if not s:
            return None
        s = s.replace(",", "")
        # DART는 음수 괄호 표기 가능
        if s.startswith("(") and s.endswith(")"):
            s = "-" + s[1:-1]
        return float(s)
    except Exception:
        return None


def _extract_amount_contains(financial_df: Any, keys: List[str]) -> Optional[float]:
    """
    financial_df: pandas.DataFrame expected
    keys: 포함 매칭 키 리스트
    """
    try:
        if financial_df is None or getattr(financial_df, "empty", True):
            return None
        if "account_nm" not in financial_df.columns:
            return None
        col = "thstrm_amount" if "thstrm_amount" in financial_df.columns else None
        if col is None:
            # 일부 응답은 thstrm_amount 대신 다른 컬럼일 수 있으니 fallback
            for candidate in ("thstrm_amount", "thstrm_add_amount", "thstrm"):
                if candidate in financial_df.columns:
                    col = candidate
                    break
        if col is None:
            return None

        # 문자열 포함 매칭(가장 먼저 찾은 값)
        for key in keys:
            rows = financial_df[financial_df["account_nm"].astype(str).str.contains(key, na=False)]
            if not rows.empty:
                val = rows.iloc[0][col]
                return _to_float_amount(val)
        return None
    except Exception:
        return None


def _compute_financial_ratios(financial_df: Any) -> Dict[str, Any]:
    """
    DART 재무제표에서 핵심 비율 산출
    - 부채비율, 유동비율, ROE, 영업이익률(가능하면)
    """
    # 핵심 계정 키(회사별 표기 차이 대응)
    ASSET_KEYS = ["자산총계"]
    LIAB_KEYS = ["부채총계"]
    EQUITY_KEYS = ["자본총계", "지배기업의 소유주에게 귀속되는 자본", "지배기업 소유지분"]
    CUR_ASSET_KEYS = ["유동자산"]
    CUR_LIAB_KEYS = ["유동부채"]
    NET_INCOME_KEYS = ["당기순이익", "당기순이익(손실)", "지배기업의 소유주에게 귀속되는 당기순이익"]
    REVENUE_KEYS = ["매출액", "수익", "영업수익"]
    OP_KEYS = ["영업이익", "영업이익(손실)"]

    assets = _extract_amount_contains(financial_df, ASSET_KEYS)
    liab = _extract_amount_contains(financial_df, LIAB_KEYS)
    equity = _extract_amount_contains(financial_df, EQUITY_KEYS)
    cur_assets = _extract_amount_contains(financial_df, CUR_ASSET_KEYS)
    cur_liab = _extract_amount_contains(financial_df, CUR_LIAB_KEYS)
    net_income = _extract_amount_contains(financial_df, NET_INCOME_KEYS)
    revenue = _extract_amount_contains(financial_df, REVENUE_KEYS)
    op_profit = _extract_amount_contains(financial_df, OP_KEYS)

    debt_ratio = None
    current_ratio = None
    roe = None
    op_margin = None

    try:
        if liab is not None and equity not in (None, 0):
            debt_ratio = (liab / equity) * 100.0
    except Exception:
        debt_ratio = None

    try:
        if cur_assets is not None and cur_liab not in (None, 0):
            current_ratio = (cur_assets / cur_liab) * 100.0
    except Exception:
        current_ratio = None

    try:
        if net_income is not None and equity not in (None, 0):
            roe = (net_income / equity) * 100.0
    except Exception:
        roe = None

    try:
        if op_profit is not None and revenue not in (None, 0):
            op_margin = (op_profit / revenue) * 100.0
    except Exception:
        op_margin = None

    return {
        "assets": assets,
        "liab": liab,
        "equity": equity,
        "cur_assets": cur_assets,
        "cur_liab": cur_liab,
        "net_income": net_income,
        "revenue": revenue,
        "op_profit": op_profit,
        "debt_ratio": round(debt_ratio, 1) if isinstance(debt_ratio, (int, float)) else None,
        "current_ratio": round(current_ratio, 1) if isinstance(current_ratio, (int, float)) else None,
        "roe_calc": round(roe, 1) if isinstance(roe, (int, float)) else None,
        "op_margin": round(op_margin, 1) if isinstance(op_margin, (int, float)) else None,
    }


def _grade_debt_ratio(r: Optional[float]) -> str:
    if not isinstance(r, (int, float)):
        return "N/A"
    if r < 50:
        return "A+"
    if r < 100:
        return "A"
    if r < 150:
        return "B"
    if r < 200:
        return "C"
    return "D"


def _grade_current_ratio(r: Optional[float]) -> str:
    if not isinstance(r, (int, float)):
        return "N/A"
    if r >= 200:
        return "A+"
    if r >= 150:
        return "A"
    if r >= 100:
        return "B"
    if r >= 80:
        return "C"
    return "D"


def _grade_roe(r: Optional[float]) -> str:
    if not isinstance(r, (int, float)):
        return "N/A"
    if r >= 15:
        return "A+"
    if r >= 10:
        return "A"
    if r >= 7:
        return "B"
    if r >= 5:
        return "C"
    return "D"


def _build_financial_analysis_text(fin_ratios: Dict[str, Any]) -> str:
    debt = fin_ratios.get("debt_ratio")
    cur = fin_ratios.get("current_ratio")
    roe = fin_ratios.get("roe_calc")
    opm = fin_ratios.get("op_margin")

    # 코멘트
    debt_comment = "부채 부담이 낮은 편이에요" if isinstance(debt, (int, float)) and debt < 100 else                    "부채 수준이 높아 관리가 필요해요" if isinstance(debt, (int, float)) and debt >= 150 else                    "부채 수준은 무난한 편이에요"
    cur_comment = "단기 유동성은 탄탄해요" if isinstance(cur, (int, float)) and cur >= 150 else                   "단기 유동성은 주의가 필요해요" if isinstance(cur, (int, float)) and cur < 100 else                   "단기 유동성은 보통 수준이에요"
    roe_comment = "수익성이 좋은 편이에요" if isinstance(roe, (int, float)) and roe >= 10 else                   "수익성 개선 여지가 있어요" if isinstance(roe, (int, float)) and roe < 7 else                   "수익성은 무난한 편이에요"

    lines = [
        "⬛️ 재무 분석이에요.",
        "",
        f"• 부채비율: {debt if debt is not None else 'N/A'}% ({_grade_debt_ratio(debt)})",
        f"• 유동비율: {cur if cur is not None else 'N/A'}% ({_grade_current_ratio(cur)})",
        f"• ROE(추정): {roe if roe is not None else 'N/A'}% ({_grade_roe(roe)})",
    ]
    if opm is not None:
        lines.append(f"• 영업이익률(추정): {opm}%")
    lines += [
        "",
        "✔️ 주요 체크 포인트는",
        f"{debt_comment}, {cur_comment}, {roe_comment}."
    ]
    return "\n".join(lines)


def _build_valuation_text(per: Any, pbr: Any, roe: Any) -> str:
    def _num(v):
        return float(v) if isinstance(v, (int, float)) else None

    per_v = _num(per)
    pbr_v = _num(pbr)
    roe_v = _num(roe)

    per_line = f"• PER:{per_v:.1f}" if per_v is not None else "• PER:N/A"
    pbr_line = f"• PBR:{pbr_v:.2f}" if pbr_v is not None else "• PBR:N/A"
    roe_line = f"• ROE:{roe_v:.1f}%" if roe_v is not None else "• ROE:N/A"

    # 간단한 해석 규칙(업종 평균이 없으므로 과도한 단정은 피함)
    comment = "현재주가는 과도하게 고평가되었다고 보기는 어렵고, 업종 평균 수준으로 해석돼요."
    if per_v is not None and per_v >= 30:
        comment = "PER 기준으로는 시장 기대치가 큰 편이라, 실적이 따라오는지 확인이 필요해요."
    if per_v is not None and per_v <= 10:
        comment = "PER 기준으로는 부담이 큰 편은 아니라, 업황/실적의 방향을 함께 확인해보면 좋아요."
    if pbr_v is not None and pbr_v >= 3:
        comment = "PBR이 높은 편이라, 성장 기대가 어느 정도인지 점검이 필요해요."
    if pbr_v is not None and pbr_v <= 1 and (roe_v is not None and roe_v >= 10):
        comment = "PBR 대비 ROE가 양호해, 밸류에이션 부담이 상대적으로 낮아 보일 수 있어요."

    return "\n".join([
        "⬛️ 밸류에이션 관점에서 보면,",
        "",
        per_line,
        pbr_line,
        roe_line,
        "",
        comment
    ])


def _build_investment_opinion_text(ret_3m: Any, ret_1y: Any, rsi_signal: str, fin_ratios: Dict[str, Any]) -> str:
    # 방향성(단정 대신 체크포인트 중심)
    debt = fin_ratios.get("debt_ratio")
    roe = fin_ratios.get("roe_calc")

    long_term = "단기보다는 중장기 관점에 적합한 종목이에요"
    if isinstance(ret_1y, (int, float)) and ret_1y < -20:
        long_term = "변동성이 큰 구간일 수 있어, 접근은 신중한 편이 좋아요"

    key_var = "실적 회복 여부가 핵심 변수예요"
    if isinstance(roe, (int, float)) and roe >= 10:
        key_var = "수익성 유지 여부가 핵심 변수예요"
    if isinstance(debt, (int, float)) and debt >= 150:
        key_var = "부채 관리와 현금흐름이 핵심 변수예요"

    strat = "분할 접근 전략이 비교적 적절해 보여요"
    if rsi_signal and rsi_signal != "N/A" and "과열" in rsi_signal:
        strat = "단기 과열 신호가 있다면, 분할로 천천히 접근하는 편이 좋아요"

    return "\n".join([
        "⬛️ 종합 투자 의견이에요.",
        "",
        f"• {long_term}",
        f"• {key_var}",
        f"• {strat}",
        "",
        "👉 시장 변동성에 따라 리스크 관리는 필요해요."
    ])
# -------------------------------------------------------------------
# 3) 실시간 스냅샷: 가격/수익률/기본 정보 (FDR)
# -------------------------------------------------------------------
def load_stock_snapshot(ticker: str) -> Optional[Dict[str, Any]]:
    code = normalize_ticker(ticker)

    if fdr is None:
        print("[load_stock_snapshot] FinanceDataReader 미설치 → None 반환")
        return None

    # 1) 종목 리스트 (이름, 시가총액, 시총 순위 계산용)
    try:
        stocks = fdr.StockListing("KRX")
    except Exception as e:
        print(f"[load_stock_snapshot] StockListing 조회 오류: {e}")
        return None

    row_df = stocks[stocks["Code"] == code]
    if row_df.empty:
        row_df = stocks[stocks["Name"] == ticker]
        if row_df.empty:
            print(f"[load_stock_snapshot] 종목 리스트에서 {ticker} 를 찾지 못함")
            return None

    row = row_df.iloc[0]

    name = str(row.get("Name", code))

    # 시가총액
    marcap_val = row.get("Marcap")
    try:
        market_cap = int(marcap_val) if marcap_val is not None else None
    except Exception:
        market_cap = None

    # 시총 순위
    try:
        stocks_sorted = stocks.sort_values("Marcap", ascending=False).reset_index(drop=True)
        idx = stocks_sorted[stocks_sorted["Code"] == code].index
        market_cap_rank = int(idx[0] + 1) if len(idx) > 0 else None
    except Exception:
        market_cap_rank = None

    # 2) 가격/수익률 계산용 1년 데이터
    try:
        end = datetime.today()
        start = end - timedelta(days=365)
        df = fdr.DataReader(code, start, end)
    except Exception as e:
        print(f"[load_stock_snapshot] DataReader 조회 오류: {e}")
        return None

    if df is None or df.empty:
        print(f"[load_stock_snapshot] 일봉 데이터 없음 (code={code})")
        return None

    for col in ["Close", "High", "Low"]:
        if col not in df.columns:
            print(f"[load_stock_snapshot] 컬럼 {col} 없음 (code={code})")
            return None

    df = df.dropna(subset=["Close"])
    if df.empty:
        print(f"[load_stock_snapshot] Close 전부 NaN (code={code})")
        return None

    current_price = float(df["Close"].iloc[-1])
    high_52w = float(df["High"].max())
    low_52w = float(df["Low"].min())

    def pct_from_n_days(n: int) -> Optional[float]:
        if len(df) <= n:
            return None
        past_price = float(df["Close"].iloc[-(n + 1)])
        if past_price <= 0:
            return None
        return (current_price / past_price - 1.0) * 100.0

    ret_1m = pct_from_n_days(20)
    ret_3m = pct_from_n_days(60)
    ret_1y = pct_from_n_days(240)

    from_high: Optional[float] = None
    if high_52w > 0:
        from_high = (current_price / high_52w - 1.0) * 100.0

    snapshot: Dict[str, Any] = {
        "ticker": code,
        "name": name,
        "current_price": int(current_price),
        "market_cap": market_cap,
        "market_cap_rank": market_cap_rank,
        "ret_1m": ret_1m,
        "ret_3m": ret_3m,
        "ret_1y": ret_1y,
        "high_52w": int(high_52w),
        "low_52w": int(low_52w),
        "from_high": from_high,
    }

    return snapshot


# -------------------------------------------------------------------
# 4) 1차 밸류에이션 지표: FDR StockSummary (있으면 사용)
# -------------------------------------------------------------------
def load_stock_metrics(ticker: str) -> Dict[str, Any]:
    """
    1차: FDR StockSummary
    - 없거나 에러면 키만 만들고 None으로 채움
    - 이후 DART 보강 단계에서 실제 값 계산
    """
    code = normalize_ticker(ticker)

    if fdr is not None and hasattr(fdr, "StockSummary"):
        try:
            summary = fdr.StockSummary(code)
            if summary is not None:
                return {
                    "per": summary.get("PER"),
                    "pbr": summary.get("PBR"),
                    "roe": summary.get("ROE"),
                    "eps": summary.get("EPS"),
                    "bps": summary.get("BPS"),
                }
        except Exception as e:
            print(f"[load_stock_metrics] FDR StockSummary 조회 오류: {e}")

    # 여기까지 왔으면 FDR에서는 값을 못 얻은 상태
    return {
        "per": None,
        "pbr": None,
        "roe": None,
        "eps": None,
        "bps": None,
    }


# -------------------------------------------------------------------
# 5) FDR metrics 부족 시 DART로 보강
# -------------------------------------------------------------------
def _enhance_metrics_with_dart_if_needed(
    ticker: str,
    metrics: Dict[str, Any],
    current_price: Optional[float],
) -> Tuple[Dict[str, Any], Optional[str], Any]:
    """
    FDR 기반 metrics가 모두 None이면
    → DART 재무제표로 PER·PBR·ROE·EPS·BPS 재계산
    """
    if not (DART_LOADER and METRICS_CALCULATOR and current_price):
        return metrics, None, None

    values = [metrics.get(k) for k in ("per", "pbr", "roe", "eps", "bps")]
    has_any_value = any(v is not None for v in values)

    if has_any_value:
        # FDR에서라도 값이 하나라도 있으면 그걸 우선 사용
        return metrics, None, None

    code = normalize_ticker(ticker)
    print(f"[DART] FDR metrics 없음 → DART 재무제표로 재계산 시도 (ticker={code})")

    # 1) DART 재무제표 로딩
    try:
        financial_text, financial_df = DART_LOADER.load_financials(code)
    except Exception as e:
        print(f"[DART] 재무제표 로딩 실패: {e}")
        return metrics, None, None

    if financial_df is None or financial_df.empty:
        print("[DART] 재무제표 DataFrame 비어 있음")
        return metrics, None, None

    # 2) 재무제표 기반 지표 계산
    calculated = METRICS_CALCULATOR.calculate_from_dataframe(
        financial_df,
        current_price=current_price,
    )

    if not calculated:
        print("[DART] MetricsCalculator 결과 없음")
        return metrics, financial_text, financial_df

    # 3) PER·PBR·ROE·EPS·BPS 덮어쓰기
    for key in ("per", "pbr", "roe", "eps", "bps"):
        if key in calculated and calculated[key] is not None:
            metrics[key] = calculated[key]

    print(
        f"[DART] 밸류 지표 업데이트 → "
        f"PER={metrics.get('per')}, PBR={metrics.get('pbr')}, ROE={metrics.get('roe')}"
    )

    return metrics, financial_text, financial_df


# -------------------------------------------------------------------
# 6) 최종 리포트 조립
# -------------------------------------------------------------------
def generate_raw_report(ticker: str) -> Dict[str, Any]:
    """
    최종: 내부 리포트 JSON 생성
    - 스냅샷(FDR)
    - 밸류에이션(FDR → DART 보강)
    """
    # 타입 가드: 혹시 dict 같은 게 넘어오면 바로 no-data 처리
    if not isinstance(ticker, str):
        print(f"[generate_raw_report] invalid ticker type: {type(ticker)} {repr(ticker)[:200]}")
        return {}

    ticker = ticker.strip()
    if not ticker:
        print("[generate_raw_report] empty ticker")
        return {}

    snapshot = load_stock_snapshot(ticker)
    if not snapshot:
        print(f"[generate_raw_report] snapshot not found for ticker={ticker!r}")
        return {}

    # 1차: FDR
    metrics = load_stock_metrics(ticker) or {}

    name = snapshot.get("name", ticker)
    current_price = snapshot.get("current_price")
    market_cap = snapshot.get("market_cap")
    market_cap_rank = snapshot.get("market_cap_rank")

    ret_1m = snapshot.get("ret_1m")
    ret_3m = snapshot.get("ret_3m")
    ret_1y = snapshot.get("ret_1y")
    high_52w = snapshot.get("high_52w")
    low_52w = snapshot.get("low_52w")
    from_high = snapshot.get("from_high")

    # 2차: DART 보강
    financial_text: Optional[str] = None
    financial_df = None
    try:
        metrics, financial_text, financial_df = _enhance_metrics_with_dart_if_needed(
            ticker=snapshot.get("ticker") or normalize_ticker(ticker),
            metrics=metrics,
            current_price=current_price,
        )
    except Exception as e:
        print(f"[DART] metrics 보강 중 예외 발생: {e}")

    per = metrics.get("per")
    pbr = metrics.get("pbr")
    roe = metrics.get("roe")
    eps = metrics.get("eps")
    bps = metrics.get("bps")

    raw_data: Dict[str, Any] = {
        "basic": {
            "ticker": snapshot.get("ticker") or normalize_ticker(ticker),
            "name": name,
            "current_price": current_price,
            "market_cap": market_cap,
            "market_cap_rank": market_cap_rank,
        },
        "price_trend": {
            "1m": ret_1m,
            "3m": ret_3m,
            "1y": ret_1y,
            "52w_high": high_52w,
            "52w_low": low_52w,
            "from_high": from_high,
        },
        "metrics": {
            "per": per,
            "pbr": pbr,
            "roe": roe,
            "eps": eps,
            "bps": bps,
        },
        "technical": {
            "rsi": None,
            "rsi_signal": "N/A",
        },
    }

    if financial_text:
        raw_data["financial_text"] = financial_text

    summary_text = (
        f"{name}의 현재 주가는 {_fmt_won(current_price)}입니다. "
        f"최근 1년 수익률은 {_fmt_pct(ret_1y)} 수준입니다."
    )

    price_analysis_text = (
        f"최근 1개월 수익률은 {_fmt_pct(ret_1m)}, "
        f"3개월 수익률은 {_fmt_pct(ret_3m)}, "
        f"1년 수익률은 {_fmt_pct(ret_1y)}입니다. "
        f"52주 고점은 {_fmt_won(high_52w)}, "
        f"52주 저점은 {_fmt_won(low_52w)}이며, "
        f"현재가는 52주 고점 대비 {_fmt_pct(from_high)} 위치에 있습니다."
    )

    fin_ratios = _compute_financial_ratios(financial_df) if financial_df is not None else {}

# 수정
    if fin_ratios and isinstance(fin_ratios, dict) and "error" not in fin_ratios:
        financial_analysis_text = _build_financial_analysis_text(fin_ratios)
    else:
        financial_analysis_text = (
            "⬛️ 재무 분석이에요.\n\n"
            "• 최근 공시 기준 재무 데이터를 확인할 수 없어요\n"
            "• 최신 사업보고서 반영 후 다시 확인해 주세요\n\n"
            "✔️ 재무 구조 분석은 다음 공시 반영 시 제공될 예정이에요."
        )

    valuation_text = _build_valuation_text(per, pbr, roe)

    investment_opinion_text = _build_investment_opinion_text(ret_3m, ret_1y, raw_data["technical"].get("rsi_signal","N/A"), fin_ratios if "fin_ratios" in locals() else {})

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report_data: Dict[str, Any] = {
        "ticker": snapshot.get("ticker") or normalize_ticker(ticker),
        "name": name,
        "generated_at": generated_at,
        "report": {
            "title": f"{name} 투자 리포트",
            "full_text": "",
            "sections": {
                "summary": summary_text,
                "price_analysis": price_analysis_text,
                "financial_analysis": financial_analysis_text,
                "valuation": valuation_text,
                "investment_opinion": investment_opinion_text,
            },
            "has_financials": bool(metrics),
        },
        "raw_data": raw_data,
    }

    return report_data
