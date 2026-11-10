"""
Natural language query API endpoint.

POST /api/v1/queries
    Accepts natural language purchasing questions, routes between SQL, RAG, or both,
    and returns a structured aggregation + clear natural language answer.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_dep
from app.schemas.query import QueryRequest, QueryResponse
from app.services.query_engine import process_user_query

router = APIRouter(prefix="/queries", tags=["queries"])


@router.post(
    "",
    response_model=QueryResponse,
    summary="Ask natural language questions about purchases and receipts",
    description=(
        "Hybrid query system: automatically routes questions to PostgreSQL aggregations (SQL), "
        "pgvector semantic search (RAG), or a combination of both."
    ),
)
async def query_endpoint(
    body: QueryRequest,
    db: AsyncSession = Depends(get_db_dep),
) -> QueryResponse:
    return await process_user_query(session=db, query_str=body.query)
