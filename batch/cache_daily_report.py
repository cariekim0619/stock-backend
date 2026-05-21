#!/usr/bin/env python
"""
하루에 한 번 실행해서
1) 전체 종목 리스트를 불러오고
2) 각 종목의 리포트를 DART 기반으로 계산한 뒤
3) S3에 JSON으로 저장하는 배치 스크립트 (단일 스레드 + 중간저장).

실행 예:
    cd /home/ec2-user/stock-backend
    venv/bin/python batch/cache_daily_report.py
"""

import sys
import os
import json
import time
import datetime as dt
from pathlib import Path

import boto3
from dotenv import load_dotenv
from pykrx import stock as pystock

# -------------------------------
# 0) 프로젝트 루트 / .env / PYTHONPATH 세팅
# -------------------------------
BASE_DIR = Path(__file__).resolve().parents[1]

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.chdir(BASE_DIR)

env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(env_path)

# -------------------------------
# 1) S3 설정
# -------------------------------
S3_BUCKET = os.getenv("STOCK_REPORT_S3_BUCKET", "stock-report-cache")
S3 = boto3.client("s3")

LATEST_KEY = "reports/latest/all_reports.json"


# -------------------------------
# 2) 전체 종목 리스트 가져오기
# -------------------------------
def load_all_krx_tickers() -> list[str]:
    today_str = dt.datetime.today().strftime("%Y%m%d")
    kospi = pystock.get_market_ticker_list(today_str, market="KOSPI")
    kosdaq = pystock.get_market_ticker_list(today_str, market="KOSDAQ")

    tickers = sorted(set(kospi + kosdaq))
    print(f"[INFO] 전체 종목 수: {len(tickers)}개")
    return tickers


# -------------------------------
# 3) 서비스 리포트 생성 함수 재사용
# -------------------------------
from app.services.report_service import generate_report


def build_report_for_ticker(ticker: str) -> dict:
    """
    주어진 종목코드(ticker)에 대한 최종 리포트를 생성.
    FastAPI /api/stocks/report 에서 사용하는 generate_report()를 그대로 재사용한다.
    (동기 함수이므로 await 사용 X)
    """
    return generate_report(ticker=ticker)


# -------------------------------
# 4) S3 저장 유틸 함수
# -------------------------------
def make_payload(today_str: str, all_results: dict) -> dict:
    return {
        "updated_at": dt.datetime.utcnow().isoformat(),
        "date": today_str,
        "count": len(all_results),
        "data": all_results,
    }


def save_latest(today_str: str, all_results: dict):
    payload = make_payload(today_str, all_results)
    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    S3.put_object(
        Bucket=S3_BUCKET,
        Key=LATEST_KEY,
        Body=body_bytes,
        ContentType="application/json; charset=utf-8",
    )
    print(f"[INFO] latest 저장 완료 (총 {len(all_results)}개)")


def save_dated(today_str: str, all_results: dict):
    payload = make_payload(today_str, all_results)
    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    dated_key = f"reports/{today_str}/all_reports.json"
    S3.put_object(
        Bucket=S3_BUCKET,
        Key=dated_key,
        Body=body_bytes,
        ContentType="application/json; charset=utf-8",
    )
    print(f"[INFO] 날짜별 저장 완료: s3://{S3_BUCKET}/{dated_key}")


# -------------------------------
# 5) 메인 배치 로직 (단일 스레드 + 중간저장)
# -------------------------------
def cache_reports_to_s3():
    today_str = dt.date.today().isoformat()

    # 1) 기존 latest 캐시 있으면 불러오기 (재실행 시 이어서 진행)
    existing_data: dict[str, dict] = {}
    try:
        obj = S3.get_object(Bucket=S3_BUCKET, Key=LATEST_KEY)
        content = obj["Body"].read().decode("utf-8")
        payload = json.loads(content)
        existing_data = payload.get("data", {}) or {}
        print(f"[INFO] 기존 캐시 로드: {len(existing_data)}개")
    except Exception as e:
        print(f"[INFO] 기존 캐시 없음 또는 로드 실패: {e}")

    # 2) 전체 티커 로딩 + 이미 있는 티커는 스킵
    all_tickers = load_all_krx_tickers()
    remaining_tickers = [t for t in all_tickers if t not in existing_data]

    print(
        f"[INFO] 전체 {len(all_tickers)}개 중 "
        f"이미 캐시 {len(existing_data)}개, "
        f"남은 작업 {len(remaining_tickers)}개"
    )

    all_results: dict[str, dict] = dict(existing_data)

    if not remaining_tickers:
        print("[INFO] 남은 작업이 없습니다. 바로 날짜별 저장만 수행합니다.")
        save_dated(today_str, all_results)
        return

    # 3) 순차 처리 + 중간 저장
    BATCH_SAVE_INTERVAL = 10  # 10개마다 latest 중간 저장

    processed = 0
    success = 0
    errors = 0

    for ticker in remaining_tickers:
        processed += 1
        print(f"[{processed}/{len(remaining_tickers)}] {ticker} 처리 중...")

        try:
            report = build_report_for_ticker(ticker)
            if isinstance(report, dict) and "ticker" not in report:
                report["ticker"] = ticker

            all_results[ticker] = report
            success += 1

        except Exception as e:
            errors += 1
            print(f"[ERROR] {ticker}: {e}")
            # 에러 나도 계속 진행
            continue

        # 진행 상황 출력
        print(
            f"[PROGRESS] 처리 {processed}/{len(remaining_tickers)} "
            f"(성공 {success}, 실패 {errors}, 누적 총 {len(all_results)})"
        )

        # BATCH_SAVE_INTERVAL마다 latest 중간 저장
        if processed % BATCH_SAVE_INTERVAL == 0:
            print("[INFO] 중간 latest 저장 실행 중...")
            save_latest(today_str, all_results)
            # DART 서버 / 네트워크 부담 조금 줄여주기
            time.sleep(0.5)

        # 너무 과하게 돌지 않도록 살짝 딜레이
        time.sleep(0.05)

    # 4) 모든 작업 끝난 뒤 최종 저장
    print("[INFO] 모든 티커 처리 완료. latest + 날짜별 저장 수행.")
    save_latest(today_str, all_results)
    save_dated(today_str, all_results)


if __name__ == "__main__":
    cache_reports_to_s3()
