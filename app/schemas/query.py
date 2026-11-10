from __future__ import annotations

import uuid
from typing import Any
from pydantic import BaseModel, Field

from app.schemas.receipt import ReceiptDetailResponse


class QueryRequest(BaseModel):
    query: str = Field(..., description="Natural language question about receipts or purchases")


class QueryResponse(BaseModel):
    query: str
    route: str = Field(..., description="'sql', 'rag', or 'sql_and_rag'")
    answer: str
    structured_data: dict[str, Any] | None = None
    receipts: list[ReceiptDetailResponse] = []
    execution_time_ms: float
