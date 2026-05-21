import sys
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가
BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(BASE_DIR))

from app.services.report_service import generate_report

# 테스트용 티커 10개 (지금 돌려본 애들 그대로 넣어둠)
TEST_TICKERS = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "035420",  # NAVER
    "068270",  # 셀트리온
    "051910",  # LG화학
    "207940",  # 삼성바이오로직스
    "105560",  # KB금융
    "034730",  # SK
    "036570",  # 엔씨소프트
    "005380",  # 현대차
]


def main():
    print("=== 동기 테스트: 10개만 실행합니다 ===")

    for ticker in TEST_TICKERS:
        try:
            print(f"[TEST] {ticker} 계산 시작")
            # 🔥 여기서 더 이상 await 쓰지 않는다
            report = generate_report(ticker=ticker)

            # report가 dict라고 가정하고, 몇 개만 찍어보자
            if isinstance(report, dict):
                title = report.get("title") or report.get("header") or ""
                print(f"[OK] {ticker} 완료, title={title}")
            else:
                print(f"[WARN] {ticker} 완료, but type={type(report)}")

        except Exception as e:
            print(f"[ERROR] {ticker} 처리 중 예외: {e}")


if __name__ == "__main__":
    main()
