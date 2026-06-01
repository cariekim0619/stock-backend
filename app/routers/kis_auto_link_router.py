# app/routers/kis_auto_link_router.py
"""로컬 Selenium KIS OpenAPI 자동 마법사 결과 수신 라우터.

운영 흐름
1. Lambda가 S3에 kis_auto_jobs/<job_id>.json을 만들고 job token hash만 저장한다.
2. 사용자는 로컬에서 SSH 터널(-L 18000:127.0.0.1:8000)을 연다.
3. 로컬 kis_openapi_selenium_latest.py가 BACKEND_RESULT_URL로 결과를 POST한다.
4. 이 라우터가 token을 검증한 뒤 S3 job에 App Key/App Secret을 저장한다.
5. Lambda의 [자동 연결 결과 확인] 버튼이 결과를 읽고 KIS token을 발급한 뒤 job 결과를 scrub한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-2")
BUCKET = (os.getenv("KIS_AUTO_JOB_BUCKET") or os.getenv("BUCKET") or "stockpia-kakaotalk-chatbot-apse2-bucket").strip()
KIS_AUTO_JOB_PREFIX = (os.getenv("KIS_AUTO_JOB_PREFIX") or "kis_auto_jobs").strip().strip("/") or "kis_auto_jobs"

s3 = boto3.client("s3", region_name=AWS_REGION)
router = APIRouter(prefix="/api/kis/auto-link", tags=["KIS Auto Link"])


class KisAutoResult(BaseModel):
    job_id: str
    user_uuid: str
    env: str = "real"
    account_no: Optional[str] = ""
    appkey: str
    appsecret: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_job_id(job_id: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_\-]", "", (job_id or "").strip())
    if not safe:
        raise HTTPException(status_code=400, detail="job_id is required")
    return safe


def _job_key(job_id: str) -> str:
    return f"{KIS_AUTO_JOB_PREFIX}/{_safe_job_id(job_id)}.json"


def _sha256(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _get_job(job_id: str) -> Optional[Dict[str, Any]]:
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=_job_key(job_id))
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404", "NoSuchBucket"}:
            return None
        raise


def _put_job(job: Dict[str, Any]) -> None:
    s3.put_object(
        Bucket=BUCKET,
        Key=_job_key(str(job.get("job_id") or "")),
        Body=json.dumps(job, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )


def _bearer_token(authorization: Optional[str]) -> str:
    raw = (authorization or "").strip()
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


def _validate_env(env: str) -> str:
    v = (env or "real").strip().lower()
    if v not in {"real", "paper"}:
        raise HTTPException(status_code=400, detail="env must be real or paper")
    return v


@router.post("/result")
def receive_auto_link_result(req: KisAutoResult, authorization: Optional[str] = Header(None)):
    job = _get_job(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="auto link job not found")

    if str(job.get("user_uuid") or "") != str(req.user_uuid or ""):
        raise HTTPException(status_code=403, detail="user_uuid mismatch")

    token = _bearer_token(authorization)
    expected_hash = str(job.get("token_hash") or "")
    if not token or not expected_hash or _sha256(token) != expected_hash:
        raise HTTPException(status_code=403, detail="invalid job token")

    status = str(job.get("status") or "pending").lower()
    if status not in {"pending", "done"}:
        raise HTTPException(status_code=409, detail=f"job is not writable: {status}")

    appkey = (req.appkey or "").strip()
    appsecret = (req.appsecret or "").strip()
    if not appkey or not appsecret:
        raise HTTPException(status_code=400, detail="appkey and appsecret are required")

    job["status"] = "done"
    job["updated_at"] = _utc_now_iso()
    job["result"] = {
        "env": _validate_env(req.env),
        "account_no": (req.account_no or "").strip(),
        "appkey": appkey,
        "appsecret": appsecret,
        "received_at": _utc_now_iso(),
    }
    _put_job(job)

    return {"ok": True, "status": "done", "job_id": job.get("job_id")}


@router.get("/status/{job_id}")
def get_auto_link_status(job_id: str, authorization: Optional[str] = Header(None)):
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="auto link job not found")

    token = _bearer_token(authorization)
    expected_hash = str(job.get("token_hash") or "")
    if expected_hash and token and _sha256(token) != expected_hash:
        raise HTTPException(status_code=403, detail="invalid job token")

    return {
        "ok": True,
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "target_env": job.get("target_env"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "expires_at": job.get("expires_at"),
        "has_result": isinstance(job.get("result"), dict),
    }
