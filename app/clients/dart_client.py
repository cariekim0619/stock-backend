# app/clients/dart_client.py
# ------------------------------------------------------------
# DART(OpenDART) API client: corp_code lookup and financials fetch
# - get_corp_code(stock_code) -> str
# - get_financials(corp_code, year, reprt_code) -> pandas.DataFrame
# ------------------------------------------------------------
import io
import zipfile
from typing import Optional

import requests
import pandas as pd
from xml.etree import ElementTree as ET


class DartClient:
    def __init__(self, api_key: str, timeout: int = 30):
        self.api_key = api_key
        self.timeout = timeout

    # -----------------------------
    # Public APIs
    # -----------------------------
    def get_corp_code(self, stock_code: str) -> Optional[str]:
        """
        Convert KRX stock code (e.g., '005930') to DART corp_code (e.g., '00126380').
        Returns None if not found.
        """
        root = self._download_corp_code_xml()
        for item in root.findall("list"):
            s_code = (item.findtext("stock_code") or "").strip()
            if s_code == stock_code:
                return (item.findtext("corp_code") or "").strip()
        return None

    def get_financials(self, corp_code: str, year: int, reprt_code: str, fs_div: str = "CFS") -> pd.DataFrame:
        """
        Fetch 'Single Company All Accounts' (fnlttSinglAcntAll) financials.
        reprt_code: 11011(사업), 11012(반기), 11013(1Q), 11014(3Q)
        """
        url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bsns_year": year,
            "reprt_code": reprt_code,
            "fs_div": fs_div,  # ✅ 연결(CFS) 또는 별도(OFS)
        }
        res = requests.get(url, params=params, timeout=self.timeout).json()
        status = res.get("status")
        if status not in ("000", "013"):
            raise RuntimeError(f"DART API error: {res}")
        df = pd.DataFrame(res.get("list") or [])
        return df

    # -----------------------------
    # Internal helpers
    # -----------------------------
    def _download_corp_code_xml(self) -> ET.Element:
        url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={self.api_key}"
        r = requests.get(url, timeout=self.timeout)
        r.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        with zf.open("CORPCODE.xml") as f:
            tree = ET.parse(f)
        return tree.getroot()
