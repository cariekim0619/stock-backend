# app/schemas/report_schema.py
from pydantic import BaseModel

class ReportRequest(BaseModel):
    ticker: str
