# app/utils/ticker_normalizer.py
"""
S3 기반 종목명/종목코드 정규화 유틸.

핵심 정책
- 종목 마스터는 S3의 단일 JSON (`stockpia-kakaotalk-chatbot-apse2-bucket-stockcache.json`)을 사용
- EC2는 S3 JSON이 있으면 그대로 사용하고, 없거나 비어 있을 때만 최초 1회 생성 시도
- 강제 갱신은 배치/운영 명령에서만 `force_refresh=True` 로 수행
- 런타임 메모리 캐시(TTL)로 EC2 프로세스 내 반복 조회 비용을 줄임
- 종목 마스터 원본은 OpenDART corpCode.xml 사용
"""
from __future__ import annotations

import io
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import boto3
    from botocore.exceptions import ClientError
except Exception:  # pragma: no cover
    boto3 = None
    ClientError = Exception

KST = timezone(timedelta(hours=9))

AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-2")
S3_BUCKET = (
    os.getenv("STOCK_UNIVERSE_BUCKET")
    or os.getenv("BUCKET")
    or "stockpia-kakaotalk-chatbot-apse2-bucket"
).strip()
S3_KEY = (
    os.getenv("STOCK_UNIVERSE_KEY")
    or "stockpia-kakaotalk-chatbot-apse2-bucket-stockcache.json"
).strip()
DART_API_KEY = (os.getenv("DART_API_KEY") or "").strip()
DART_CORPCODE_URL = os.getenv(
    "DART_CORPCODE_URL",
    "https://opendart.fss.or.kr/api/corpCode.xml",
).strip()
RUNTIME_CACHE_TTL_SEC = int(os.getenv("STOCK_UNIVERSE_RUNTIME_TTL_SEC", "180"))
REFRESH_ENABLED = (os.getenv("STOCK_UNIVERSE_REFRESH_ENABLED", "true") or "true").strip().lower() not in {
    "0", "false", "no", "off"
}

# 최소 안전망 별칭
MANUAL_NAME_TO_CODE: Dict[str, str] = {
    "삼성전자": "005930",
    "삼성전기": "009150",
    "카카오": "035720",
    "현대차": "005380",
    # DART corpCode에서 누락되기 쉬운 우선주/짧은 별칭 안전망
    "LG": "003550",
    "엘지": "003550",
    "LG우": "003555",
    "엘지우": "003555",
    "삼성전자우": "005935",
    "삼성전기우": "009155",
    "삼성SDI우": "006405",
    "삼성화재우": "000815",
    "현대차우": "005385",
    "현대차2우B": "005387",
    "현대차3우B": "005389",
    "LG전자우": "066575",
    "LG화학우": "051915",
    "LG생활건강우": "051905",
    "한화우": "000885",
    "한화솔루션우": "009835",
    "CJ우": "001045",
    "CJ제일제당우": "097955",
    "유한양행우": "000105",
    "대한항공우": "003495",
    "금호석유우": "011785",
    "SK증권우": "001515",
    "SK네트웍스우": "001745",
    "SK디스커버리우": "006125",
    "SK이노베이션우": "096775",
    "기아": "000270",
    "LG전자": "066570",
    "LG에너지솔루션": "373220",
    "SK하이닉스": "000660",
    "NAVER": "035420",
    "네이버": "035420",
    "삼성SDI": "006400",
    "셀트리온": "068270",
    "포스코": "005490",
    "POSCO홀딩스": "005490",
    "삼성바이오로직스": "207940",
}
MANUAL_CODE_TO_NAME: Dict[str, str] = {v: k for k, v in MANUAL_NAME_TO_CODE.items()}
# 동일 코드에 여러 별칭이 있을 때 사용자에게 보여줄 대표 종목명 보정.
# DART corpCode는 우선주를 누락하는 경우가 있어, 우선주 안전망은 여기서 명시한다.
MANUAL_CODE_TO_NAME.update({
    "003550": "LG",
    "003555": "LG우",
    "005935": "삼성전자우",
    "009155": "삼성전기우",
    "006405": "삼성SDI우",
    "000815": "삼성화재우",
    "005385": "현대차우",
    "005387": "현대차2우B",
    "005389": "현대차3우B",
    "066575": "LG전자우",
    "051915": "LG화학우",
    "051905": "LG생활건강우",
    "000885": "한화우",
    "009835": "한화솔루션우",
    "001045": "CJ우",
    "097955": "CJ제일제당우",
    "000105": "유한양행우",
    "003495": "대한항공우",
    "011785": "금호석유우",
    "001515": "SK증권우",
    "001745": "SK네트웍스우",
    "006125": "SK디스커버리우",
    "096775": "SK이노베이션우",
})

_S3_CLIENT = None
_RUNTIME_CACHE_PAYLOAD: Optional[Dict[str, Any]] = None
_RUNTIME_CACHE_TS: float = 0.0
_RUNTIME_INDEX: Optional[Dict[str, Any]] = None
_RUNTIME_INDEX_SIG: str = ""


def _now_kst() -> datetime:
    return datetime.now(KST)


def _today_kst_str() -> str:
    return _now_kst().strftime("%Y%m%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_valid_code(code: str) -> bool:
    s = (code or "").strip()
    return s.isdigit() and len(s) == 6


def _normalize_name_key(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    s = s.replace("주식회사", "")
    s = s.replace("(주)", "")
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^0-9A-Za-z가-힣]", "", s)
    return s.lower().strip()


def _get_s3_client():
    global _S3_CLIENT
    if boto3 is None:
        return None
    if _S3_CLIENT is None:
        _S3_CLIENT = boto3.client("s3", region_name=AWS_REGION)
    return _S3_CLIENT


def _make_empty_payload() -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "opendart_corpcode",
        "bucket": S3_BUCKET,
        "key": S3_KEY,
        "last_attempt_date": None,
        "last_success_date": None,
        "updated_at": None,
        "item_count": 0,
        "items": [],
        "last_error": None,
    }


def _payload_item_count(payload: Optional[Dict[str, Any]]) -> int:
    if not isinstance(payload, dict):
        return 0
    items = payload.get("items") or []
    return len(items) if isinstance(items, list) else 0


def _has_usable_s3_items(payload: Optional[Dict[str, Any]]) -> bool:
    return _payload_item_count(payload) > 0


def _s3_get_json() -> Optional[Dict[str, Any]]:
    if not S3_BUCKET or not S3_KEY:
        return None

    client = _get_s3_client()
    if client is None:
        return None

    try:
        obj = client.get_object(Bucket=S3_BUCKET, Key=S3_KEY)
        payload = json.loads(obj["Body"].read().decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        payload.setdefault("items", [])
        payload["item_count"] = payload.get("item_count") or _payload_item_count(payload)
        return payload
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404", "NoSuchBucket"):
            return None
        raise
    except Exception as e:
        print(f"[ticker_normalizer] S3 cache read failed: {e} bucket={S3_BUCKET} key={S3_KEY}")
        return None


def _s3_put_json(payload: Dict[str, Any]) -> None:
    if not S3_BUCKET or not S3_KEY:
        raise RuntimeError("S3 bucket/key not configured")

    client = _get_s3_client()
    if client is None:
        raise RuntimeError("boto3 S3 client unavailable")

    client.put_object(
        Bucket=S3_BUCKET,
        Key=S3_KEY,
        Body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )


def _fetch_dart_corpcode_items() -> List[Dict[str, str]]:
    if not DART_API_KEY:
        raise RuntimeError("DART_API_KEY not set")

    url = f"{DART_CORPCODE_URL}?crtfc_key={DART_API_KEY}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "stockpia-backend/1.0",
            "Accept": "application/zip, application/octet-stream, */*",
        },
        method="GET",
    )

    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read()

    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        xml_name = None
        for name in zf.namelist():
            if name.lower().endswith(".xml"):
                xml_name = name
                break
        if not xml_name:
            raise RuntimeError("corpCode.xml not found in DART zip")
        xml_bytes = zf.read(xml_name)

    root = ET.fromstring(xml_bytes)
    items: List[Dict[str, str]] = []
    seen_codes = set()

    for node in root.findall(".//list"):
        corp_name = (node.findtext("corp_name") or "").strip()
        stock_code = (node.findtext("stock_code") or "").strip()
        corp_code = (node.findtext("corp_code") or "").strip()
        if not corp_name or not _is_valid_code(stock_code):
            continue
        if stock_code in seen_codes:
            continue
        seen_codes.add(stock_code)
        items.append({
            "name": corp_name,
            "code": stock_code,
            "corp_code": corp_code,
        })

    items.sort(key=lambda x: (x.get("name") or "", x.get("code") or ""))
    if not items:
        raise RuntimeError("empty stock universe from DART corpCode.xml")
    return items


def _fetch_krx_listing_items() -> List[Dict[str, str]]:
    """KRX 전체 상장 종목/ETF/ETN 보조 종목 마스터.

    OpenDART corpCode는 상장법인 중심이라 ETF/ETN, 합성 상품, 일부 신규 상장 상품이
    빠질 수 있다. 추천 TOP 5에서는 이런 상품을 별도 필터링하지만, 보유 종목/리포트/뉴스
    커뮤니티에서는 사용자가 실제 보유하거나 직접 입력한 공식 상장 상품이면 조회되도록
    KRX listing을 보조로 합친다.
    """
    try:
        import FinanceDataReader as fdr  # type: ignore
    except Exception as e:
        print(f"[ticker_normalizer] KRX listing unavailable: {e}")
        return []

    try:
        df = fdr.StockListing("KRX")
    except Exception as e:
        print(f"[ticker_normalizer] KRX listing fetch failed: {e}")
        return []

    if df is None or getattr(df, "empty", True):
        return []

    def _first_col(*names: str) -> str:
        for name in names:
            if name in df.columns:
                return name
        return ""

    code_col = _first_col("Code", "Symbol", "종목코드", "ShortCode")
    name_col = _first_col("Name", "종목명", "한글 종목약명", "IssueName")
    market_col = _first_col("Market", "시장구분")
    if not code_col or not name_col:
        print(f"[ticker_normalizer] KRX listing columns insufficient: {list(df.columns)}")
        return []

    items: List[Dict[str, str]] = []
    seen = set()
    for _, row in df.iterrows():
        code = str(row.get(code_col, "") or "").strip().upper()
        name = str(row.get(name_col, "") or "").strip()
        market = str(row.get(market_col, "") or "").strip() if market_col else "KRX"
        # KIS 국내상품 코드는 대부분 6자리 숫자이나, 최근 일부 상품은 6자리 영숫자 단축코드가
        # 노출될 수 있어 영숫자 6자도 보존한다. 기존 주식 경로는 숫자 6자리만 사용한다.
        if code.isdigit():
            code = code.zfill(6)
        if not name or not re.fullmatch(r"[0-9A-Z]{6}", code):
            continue
        if code in seen:
            continue
        seen.add(code)
        items.append({"name": name, "code": code, "source": "krx_listing", "market": market})

    return items


def _merge_stock_universe_items(*sources: List[Dict[str, str]]) -> List[Dict[str, str]]:
    merged: Dict[str, Dict[str, str]] = {}
    for source in sources:
        for item in source or []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip().upper()
            name = str(item.get("name") or "").strip()
            if code.isdigit():
                code = code.zfill(6)
            if not name or not re.fullmatch(r"[0-9A-Z]{6}", code):
                continue
            if code not in merged:
                row = dict(item)
                row["code"] = code
                row["name"] = name
                merged[code] = row
            else:
                # DART에 있던 corp_code는 유지하되, KRX 약명이 더 최신이면 alias는 index에서 추가된다.
                if not merged[code].get("source") and item.get("source"):
                    merged[code]["source"] = item.get("source") or ""
    items = list(merged.values())
    items.sort(key=lambda x: (x.get("name") or "", x.get("code") or ""))
    return items


def _mark_attempted(base: Optional[Dict[str, Any]], error_text: Optional[str] = None) -> Dict[str, Any]:
    payload = dict(base or _make_empty_payload())
    payload["schema_version"] = 1
    payload["source"] = "opendart_corpcode"
    payload["bucket"] = S3_BUCKET
    payload["key"] = S3_KEY
    payload["last_attempt_date"] = _today_kst_str()
    payload["last_error"] = error_text
    payload.setdefault("items", [])
    payload["item_count"] = _payload_item_count(payload)
    return payload


def _refresh_stock_universe(base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = _mark_attempted(base)
    try:
        dart_items = _fetch_dart_corpcode_items()
        krx_items = _fetch_krx_listing_items()
        items = _merge_stock_universe_items(dart_items, krx_items)
        payload.update({
            "updated_at": _now_iso(),
            "last_success_date": _today_kst_str(),
            "last_error": None,
            "source": "opendart_corpcode+krx_listing",
            "item_count": len(items),
            "items": items,
        })
        _s3_put_json(payload)
        print(
            f"[ticker_normalizer] stock universe refreshed to S3 "
            f"({len(items)} items) bucket={S3_BUCKET} key={S3_KEY}"
        )
        return payload
    except Exception as e:
        payload["last_error"] = repr(e)
        print(
            f"[ticker_normalizer] stock universe refresh failed: {e} "
            f"bucket={S3_BUCKET} key={S3_KEY}"
        )
        return payload


def _payload_signature(payload: Optional[Dict[str, Any]]) -> str:
    if not payload:
        return ""
    return (
        f"{payload.get('last_success_date')}|"
        f"{payload.get('last_attempt_date')}|"
        f"{payload.get('item_count')}|"
        f"{payload.get('updated_at')}"
    )


def _build_index(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    global _RUNTIME_INDEX, _RUNTIME_INDEX_SIG

    sig = _payload_signature(payload)
    if _RUNTIME_INDEX is not None and _RUNTIME_INDEX_SIG == sig:
        return _RUNTIME_INDEX

    code_to_name: Dict[str, str] = dict(MANUAL_CODE_TO_NAME)
    alias_to_code: Dict[str, str] = {
        _normalize_name_key(name): code for name, code in MANUAL_NAME_TO_CODE.items()
    }

    for item in (payload or {}).get("items", []) or []:
        name = (item.get("name") or "").strip()
        code = (item.get("code") or "").strip().upper()
        if code.isdigit():
            code = code.zfill(6)
        if not name or not re.fullmatch(r"[0-9A-Z]{6}", code):
            continue
        code_to_name.setdefault(code, name)
        alias_to_code[_normalize_name_key(name)] = code
        # KRX/FDR가 제공하는 약명/정식명 필드가 들어오면 모두 별칭으로 등록한다.
        for alias_field in ("short_name", "full_name", "kor_name", "name"):
            alias = str(item.get(alias_field) or "").strip()
            if alias:
                alias_to_code[_normalize_name_key(alias)] = code

    _RUNTIME_INDEX = {
        "code_to_name": code_to_name,
        "alias_to_code": alias_to_code,
    }
    _RUNTIME_INDEX_SIG = sig
    return _RUNTIME_INDEX


def ensure_stock_universe_cache(force_refresh: bool = False) -> Dict[str, Any]:
    """
    우선순위
    1) 런타임 캐시(TTL)
    2) S3 JSON 사용
    3) S3 JSON이 없거나 비어 있을 때만 최초 생성(refresh)
    4) 강제 갱신은 force_refresh=True 일 때만 수행
    """
    global _RUNTIME_CACHE_PAYLOAD, _RUNTIME_CACHE_TS

    now_ts = datetime.now(timezone.utc).timestamp()
    if (
        not force_refresh
        and _RUNTIME_CACHE_PAYLOAD is not None
        and (now_ts - _RUNTIME_CACHE_TS) < RUNTIME_CACHE_TTL_SEC
    ):
        return _RUNTIME_CACHE_PAYLOAD

    payload = _s3_get_json() or _make_empty_payload()

    if force_refresh:
        payload = _refresh_stock_universe(payload)
    elif _has_usable_s3_items(payload):
        print(
            f"[ticker_normalizer] stock universe loaded from S3 "
            f"({_payload_item_count(payload)} items) bucket={S3_BUCKET} key={S3_KEY}"
        )
    elif REFRESH_ENABLED:
        payload = _refresh_stock_universe(payload)
    else:
        print(
            "[ticker_normalizer] S3 stock universe cache unavailable and refresh disabled "
            f"bucket={S3_BUCKET} key={S3_KEY}"
        )

    _RUNTIME_CACHE_PAYLOAD = payload
    _RUNTIME_CACHE_TS = now_ts
    _build_index(payload)
    return payload


def warm_stock_universe_cache() -> Dict[str, Any]:
    return ensure_stock_universe_cache(force_refresh=False)


def get_lookup_status() -> Dict[str, Any]:
    payload = ensure_stock_universe_cache(force_refresh=False)
    return {
        "bucket": S3_BUCKET,
        "key": S3_KEY,
        "refresh_enabled": REFRESH_ENABLED,
        "last_attempt_date": payload.get("last_attempt_date"),
        "last_success_date": payload.get("last_success_date"),
        "updated_at": payload.get("updated_at"),
        "item_count": payload.get("item_count") or _payload_item_count(payload),
        "has_s3_items": _has_usable_s3_items(payload),
        "has_usable_data": bool((payload.get("items") or []) or MANUAL_NAME_TO_CODE),
        "last_error": payload.get("last_error"),
    }


def force_refresh_stock_universe_cache() -> Dict[str, Any]:
    return ensure_stock_universe_cache(force_refresh=True)


def normalize_ticker(ticker: str) -> str:
    raw = ("" if ticker is None else str(ticker)).strip()
    if not raw:
        return ""
    resolved = resolve_symbol_and_name(raw)
    return resolved[0] if resolved else raw


def get_company_name_by_symbol(symbol: str) -> Optional[str]:
    sym = (symbol or "").strip()
    if not _is_valid_code(sym):
        return None
    payload = ensure_stock_universe_cache(force_refresh=False)
    index = _build_index(payload)
    return index["code_to_name"].get(sym)


def _lookup_krx_listing_symbol_once(raw: str) -> Optional[Tuple[str, str]]:
    qkey = _normalize_name_key(raw)
    if not qkey:
        return None
    for item in _fetch_krx_listing_items():
        name = str(item.get("name") or "").strip()
        code = str(item.get("code") or "").strip().upper()
        if code.isdigit():
            code = code.zfill(6)
        if not name or not re.fullmatch(r"[0-9A-Z]{6}", code):
            continue
        if _normalize_name_key(name) == qkey:
            return code, name
    return None


def resolve_symbol_and_name(ticker: str, allow_unresolved: bool = False) -> Optional[Tuple[str, str]]:
    raw = ("" if ticker is None else str(ticker)).strip()
    if not raw:
        return None

    payload = ensure_stock_universe_cache(force_refresh=False)
    index = _build_index(payload)
    code_to_name: Dict[str, str] = index["code_to_name"]
    alias_to_code: Dict[str, str] = index["alias_to_code"]

    if raw.isdigit():
        code = raw.zfill(6) if len(raw) < 6 else raw
        if _is_valid_code(code) and code in code_to_name:
            return code, code_to_name[code]
        return (code, raw) if (allow_unresolved and _is_valid_code(code)) else None

    code = alias_to_code.get(_normalize_name_key(raw))
    if code and code in code_to_name:
        return code, code_to_name[code]

    krx_resolved = _lookup_krx_listing_symbol_once(raw)
    if krx_resolved:
        return krx_resolved

    code = MANUAL_NAME_TO_CODE.get(raw)
    if code:
        return code, MANUAL_CODE_TO_NAME.get(code, raw)

    return (raw, raw) if allow_unresolved else None


if __name__ == "__main__":
    status = get_lookup_status()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    for probe in ("삼성전자", "삼성전기", "카카오", "005930"):
        print(probe, "->", resolve_symbol_and_name(probe))
