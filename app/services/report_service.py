from typing import Any, Dict, Optional

from app.utils.report_formatter import ReportFormatter
from app.services.raw_report_service import generate_raw_report
from app.services.segment_personalization import apply_personalization_to_kakao, apply_personalization_to_raw_report, normalize_segment


def _simple_text_skill(message: str) -> Dict[str, Any]:
    """
    최후의 방어용 simpleText Kakao 스킬 JSON 생성기.
    (ReportFormatter가 이상한 값을 주더라도 Kakao 포맷은 보장)
    """
    text = (message or "").strip() or "리포트 생성 중 알 수 없는 오류가 발생했습니다."
    if len(text) > 980:
        text = text[:979] + "…"

    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {"simpleText": {"text": text}}
            ]
        }
    }


def _ensure_kakao_format(resp: Any, *, context: str = "") -> Dict[str, Any]:
    """
    ReportFormatter / generate_raw_report 가 무엇을 돌려주든
    최종적으로는 Kakao 스킬 JSON(dict)만 밖으로 나가도록 보장하는 래퍼.
    """
    # 이미 올바른 Kakao 스킬 JSON인 경우 그대로 사용
    if isinstance(resp, dict) and "version" in resp and "template" in resp:
        return resp

    # 문자열이면 내부 로그만 남기고 사용자에게는 simpleText로 보여준다
    if isinstance(resp, str):
        print(f"[generate_report] non-skill string response ({context}): {resp[:200]!r}")
        return _simple_text_skill(resp)

    print(f"[generate_report] unexpected response type ({context}): {type(resp)}")
    return _simple_text_skill("리포트 생성 중 오류가 발생했습니다.\n잠시 후 다시 시도해 주세요.")


def generate_report(ticker: str, segment: str = "risk-neutral", profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    /api/stocks/report 에서 호출할 서비스 함수

    - ticker: '삼성전자' 같은 종목명 or '005930' 같은 코드
    - 반환: Kakao 스킬 JSON (dict)
      {
        "version": "2.0",
        "template": { ... }
      }
    """
    norm_ticker = (ticker or "").strip()
    segment = normalize_segment(segment)

    # 0) 입력 정리
    if not norm_ticker:
        resp = ReportFormatter.build_no_data_response("(미입력)")
        return apply_personalization_to_kakao(_ensure_kakao_format(resp, context="no_ticker"), segment, domain="report")

    # 1) 내부 raw 리포트 생성
    try:
        raw_report = generate_raw_report(norm_ticker)
    except Exception as e:
        # EC2가 이상한 텍스트를 뿜지 못하게 여기서 막음
        print(f"[generate_report] error in generate_raw_report: {e!r}")
        resp = ReportFormatter.build_error_response()
        return apply_personalization_to_kakao(_ensure_kakao_format(resp, context="exception"), segment, domain="report")

    # 2) raw_report 해석

    # (1) raw_report가 이미 Kakao 스킬 JSON인 경우 (과거 구현과의 호환용)
    if isinstance(raw_report, dict) and "version" in raw_report and "template" in raw_report:
        print("[generate_report] raw_report is already kakao skill json (compat mode)")
        return apply_personalization_to_kakao(_ensure_kakao_format(raw_report, context="already_skill"), segment, domain="report")

    # (2) None 이거나 빈 dict이면 "데이터 없음"
    if not raw_report:
        print("[generate_report] raw_report is empty -> no_data")
        resp = ReportFormatter.build_no_data_response(norm_ticker)
        return apply_personalization_to_kakao(_ensure_kakao_format(resp, context="no_data"), segment, domain="report")

    # 여기까지 왔다면: 정상 케이스 — domain raw dict
    raw_report = apply_personalization_to_raw_report(raw_report, segment, profile)
    try:
        # raw_report_service.generate_raw_report 가 만든 구조에 맞춰 사용
        # 예: raw_report["report"]["sections"]["summary"] 등
        skill_json = ReportFormatter.build_from_raw_report(norm_ticker, raw_report)
    except AttributeError:
        # build_from_raw_report 가 아직 없다면, summary 텍스트만 simpleText로 보여주는 fallback
        print("[generate_report] ReportFormatter.build_from_raw_report missing, fallback to simpleText")
        try:
            summary = (
                raw_report.get("report", {})
                .get("sections", {})
                .get("summary", "")
            )
        except Exception:
            summary = ""
        if not summary:
            summary = f"{norm_ticker}에 대한 리포트를 찾을 수 없습니다."
        resp = _simple_text_skill(summary)
        return apply_personalization_to_kakao(_ensure_kakao_format(resp, context="fallback_simple"), segment, domain="report")
    except Exception as e:
        print(f"[generate_report] error in ReportFormatter.build_from_raw_report: {e!r}")
        resp = ReportFormatter.build_error_response()
        return apply_personalization_to_kakao(_ensure_kakao_format(resp, context="formatter_error"), segment, domain="report")

    # 4) 최종 Kakao 포맷 보장
    return apply_personalization_to_kakao(_ensure_kakao_format(skill_json, context="ok"), segment, domain="report")
