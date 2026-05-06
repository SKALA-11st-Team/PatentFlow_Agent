from pydantic import BaseModel


class FinalReport(BaseModel):
    summary: dict | None = None
    valuation: dict | None = None
    evidence: list[dict]

