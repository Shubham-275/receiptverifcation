from fastapi import APIRouter, Depends

from app.api import analytics, auth, health, queries, receipts
from app.api.auth import require_user

router = APIRouter(prefix="/api/v1")

# Public: health probes and the auth surface itself.
router.include_router(health.router)
router.include_router(auth.router)

# Everything that touches the purchasing ledger requires a signed session.
router.include_router(receipts.router, dependencies=[Depends(require_user)])
router.include_router(queries.router, dependencies=[Depends(require_user)])
router.include_router(analytics.router, dependencies=[Depends(require_user)])
