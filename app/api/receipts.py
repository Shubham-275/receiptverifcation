"""
Receipt upload, extraction, and persistence API endpoints.

POST /api/v1/receipts/classify
    Upload an image; get back is_receipt + confidence.
    Does NOT persist anything — pure classification gate.

POST /api/v1/receipts/extract
    Classify, extract, and normalize. Returns parsed data without persisting.

POST /api/v1/receipts/upload
    Full pipeline: classify → extract → validate → resolve products → embed → persist.

GET  /api/v1/receipts/{receipt_id}
    Retrieve stored receipt with items and supplier details.

GET  /api/v1/receipts
    List receipts with filtering and pagination.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_dep
from app.exceptions import InvalidImageError, NotAReceiptError, NotFoundError
from app.logging_config import get_logger
from app.models.receipt import ReceiptStatus
from app.repositories import receipt_repo
from app.schemas.extraction import ExtractedItem, ExtractedReceipt
from app.schemas.receipt import (
    BatchExtractItemResult,
    BatchExtractResponse,
    BatchReceiptItemResult,
    BatchUploadResponse,
    ExtractResponse,
    ExtractedItemResponse,
    ReceiptDetailResponse,
    ReceiptListResponse,
)
from app.services.classifier import ClassificationResult, classify_receipt_image
from app.services.extractor import extract_receipt
from app.services.image_validator import validate_image
from app.services.ingestion import (
    ingest_receipt_pipeline,
    receipt_to_detail_response,
)
from app.services.normalizer import ValidationResult, validate_and_normalise

log = get_logger(__name__)
router = APIRouter(prefix="/receipts", tags=["receipts"])


class ClassifyResponse(BaseModel):
    is_receipt: bool
    accepted: bool
    confidence: float
    document_type: str
    reason: str
    latency_ms: float
    message: str


async def _process_single_extract(data: bytes, filename: str) -> ExtractResponse:
    """Classify, extract, and normalize a single receipt image without persistence."""
    image_info = validate_image(data, filename=filename)

    clf: ClassificationResult = await classify_receipt_image(
        image_bytes=data, mime_type=image_info.mime_type
    )
    if not clf.accepted:
        raise NotAReceiptError(
            "Image was not accepted as a supplier receipt.",
            detail=f"confidence={clf.confidence:.2f} reason='{clf.reason}'",
        )

    extracted: ExtractedReceipt = await extract_receipt(
        image_bytes=data, mime_type=image_info.mime_type
    )

    validation: ValidationResult = validate_and_normalise(extracted)
    r = validation.receipt

    log.info(
        "extract_complete",
        filename=filename,
        item_count=r.item_count,
        is_valid=validation.is_valid,
    )

    return ExtractResponse(
        classifier_confidence=round(clf.confidence, 4),
        supplier=r.supplier,
        date=validation.receipt_date.isoformat() if validation.receipt_date else r.date,
        invoice_number=r.invoice_number,
        receipt_total=float(r.receipt_total) if r.receipt_total is not None else None,
        currency=r.currency,
        items=[
            ExtractedItemResponse(
                raw_name=item.raw_name,
                quantity=float(item.quantity),
                unit=item.unit,
                unit_price=float(item.unit_price) if item.unit_price is not None else None,
                line_total=float(item.line_total) if item.line_total is not None else None,
            )
            for item in r.items
        ],
        item_count=r.item_count,
        extraction_confidence=round(r.extraction_confidence, 4),
        extraction_notes=r.extraction_notes,
        validation_summary=validation.summary,
        total_match=validation.total_match,
        item_math_failures=validation.item_math_failures,
        is_valid=validation.is_valid,
    )



# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post(
    "/classify",
    response_model=ClassifyResponse,
    summary="Classify whether an image is a supplier receipt",
    description=(
        "Upload a receipt image. Returns classification result and confidence. "
        "Does not persist data — use /upload for the full pipeline."
    ),
)
async def classify_receipt(
    file: UploadFile = File(..., description="Receipt image (JPEG, PNG, WebP, HEIC)"),
) -> ClassifyResponse:
    data = await file.read()
    filename = file.filename or ""

    image_info = validate_image(data, filename=filename)

    result: ClassificationResult = await classify_receipt_image(
        image_bytes=data,
        mime_type=image_info.mime_type,
    )

    if result.accepted:
        message = "Image accepted as a supplier receipt."
    elif result.is_receipt and not result.accepted:
        message = (
            f"Image looks like a receipt but confidence ({result.confidence:.0%}) "
            f"is below threshold ({result.threshold_used:.0%})."
        )
    else:
        message = "Image does not appear to be a supplier receipt."

    log.info(
        "classify_endpoint_complete",
        filename=filename,
        accepted=result.accepted,
        confidence=result.confidence,
    )

    return ClassifyResponse(
        is_receipt=result.is_receipt,
        accepted=result.accepted,
        confidence=round(result.confidence, 4),
        document_type=result.document_type,
        reason=result.reason,
        latency_ms=round(result.latency_ms, 1),
        message=message,
    )


@router.post(
    "/extract",
    response_model=ExtractResponse,
    summary="Classify, extract, and validate a receipt image",
    description=(
        "Pipeline: classify → extract → validate. "
        "Returns structured receipt data without persisting to the database. "
        "Use /upload to persist."
    ),
)
async def extract_receipt_endpoint(
    file: UploadFile = File(..., description="Receipt image (JPEG, PNG, WebP, HEIC)"),
) -> ExtractResponse:
    data = await file.read()
    filename = file.filename or ""
    return await _process_single_extract(data, filename)


@router.post(
    "/batch-extract",
    response_model=BatchExtractResponse,
    summary="Extract and validate multiple receipt images in batch (Preview mode)",
    description=(
        "Batch pipeline without database persistence. Evaluates multiple receipt images sequentially, "
        "returning individual extraction results and batch aggregate statistics."
    ),
)
async def batch_extract_endpoint(
    files: list[UploadFile] = File(..., description="Multiple receipt image files"),
) -> BatchExtractResponse:
    results: list[BatchExtractItemResult] = []
    successful_count = 0
    failed_count = 0
    total_spend = 0.0
    total_items = 0

    for file in files:
        filename = file.filename or "unknown.jpg"
        try:
            data = await file.read()
            extract_res = await _process_single_extract(data, filename)
            results.append(
                BatchExtractItemResult(
                    filename=filename,
                    success=True,
                    status_code=200,
                    extraction=extract_res,
                )
            )
            successful_count += 1
            if extract_res.receipt_total is not None:
                total_spend += extract_res.receipt_total
            total_items += extract_res.item_count
        except NotAReceiptError as exc:
            log.warning("batch_extract_rejected", filename=filename, error=str(exc))
            results.append(
                BatchExtractItemResult(
                    filename=filename,
                    success=False,
                    status_code=422,
                    error=exc.message,
                    detail=exc.detail,
                )
            )
            failed_count += 1
        except InvalidImageError as exc:
            log.warning("batch_extract_invalid_image", filename=filename, error=str(exc))
            results.append(
                BatchExtractItemResult(
                    filename=filename,
                    success=False,
                    status_code=400,
                    error=exc.message,
                    detail=exc.detail,
                )
            )
            failed_count += 1
        except Exception as exc:
            log.exception("batch_extract_item_failed", filename=filename, error=str(exc))
            results.append(
                BatchExtractItemResult(
                    filename=filename,
                    success=False,
                    status_code=500,
                    error=str(exc),
                )
            )
            failed_count += 1

    return BatchExtractResponse(
        total_files=len(files),
        successful_count=successful_count,
        failed_count=failed_count,
        total_spend=round(total_spend, 2),
        total_items=total_items,
        results=results,
    )


@router.post(
    "/upload",
    response_model=ReceiptDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload, extract, resolve, and persist a receipt",
    description=(
        "Full ingestion pipeline: validates image, gates through classifier, extracts items, "
        "validates arithmetic, resolves products, embeds receipt content, and stores everything in PostgreSQL."
    ),
)
async def upload_receipt(
    file: UploadFile = File(..., description="Receipt image file"),
    db: AsyncSession = Depends(get_db_dep),
) -> ReceiptDetailResponse:
    data = await file.read()
    filename = file.filename or ""

    try:
        receipt = await ingest_receipt_pipeline(
            session=db,
            image_bytes=data,
            filename=filename,
            image_path=filename,
        )
    except (OperationalError, OSError) as exc:
        # Genuine connectivity failure
        log.error("upload_db_unavailable", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "PostgreSQL database is currently offline or unreachable. "
                "Switch to 'Preview Only' mode above to classify and extract receipts without database persistence, "
                "or start PostgreSQL with pgvector."
            ),
        )
    except (SQLAlchemyError, DBAPIError) as exc:
        # Database is reachable but the save failed — report the real reason
        log.exception("upload_db_write_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Receipt was extracted but could not be saved: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}",
        )

    return receipt_to_detail_response(receipt)


@router.post(
    "/batch-upload",
    response_model=BatchUploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload, extract, resolve, and persist multiple receipts in batch",
    description=(
        "Full ingestion pipeline for multiple receipts: validates images, classifies, extracts items, "
        "resolves products, embeds content, and commits records to PostgreSQL. "
        "Returns batch metrics and individual receipt details or failure notes."
    ),
)
async def batch_upload_endpoint(
    files: list[UploadFile] = File(..., description="Multiple receipt image files"),
    db: AsyncSession = Depends(get_db_dep),
) -> BatchUploadResponse:
    results: list[BatchReceiptItemResult] = []
    successful_count = 0
    failed_count = 0
    total_spend = 0.0
    total_items = 0

    for file in files:
        filename = file.filename or "unknown.jpg"
        try:
            data = await file.read()
            async with db.begin_nested():
                receipt = await ingest_receipt_pipeline(
                    session=db,
                    image_bytes=data,
                    filename=filename,
                    image_path=filename,
                )
            await db.commit()
            detail = receipt_to_detail_response(receipt)
            results.append(
                BatchReceiptItemResult(
                    filename=filename,
                    success=True,
                    status_code=201,
                    receipt=detail,
                )
            )
            successful_count += 1
            if detail.receipt_total is not None:
                total_spend += detail.receipt_total
            total_items += detail.item_count
        except (OperationalError, OSError) as exc:
            # Genuine connectivity failure — stop batch and warn user
            log.error("batch_upload_db_unavailable", filename=filename, error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "PostgreSQL database is currently offline or unreachable. "
                    "Switch to 'Preview Only' mode above to classify and extract receipts without database persistence, "
                    "or start PostgreSQL with pgvector."
                ),
            )
        except NotAReceiptError as exc:
            log.warning("batch_upload_rejected", filename=filename, error=str(exc))
            results.append(
                BatchReceiptItemResult(
                    filename=filename,
                    success=False,
                    status_code=422,
                    error=exc.message,
                    detail=exc.detail,
                )
            )
            failed_count += 1
        except InvalidImageError as exc:
            log.warning("batch_upload_invalid_image", filename=filename, error=str(exc))
            results.append(
                BatchReceiptItemResult(
                    filename=filename,
                    success=False,
                    status_code=400,
                    error=exc.message,
                    detail=exc.detail,
                )
            )
            failed_count += 1
        except (SQLAlchemyError, DBAPIError) as exc:
            log.exception("batch_upload_db_write_failed", filename=filename, error=str(exc))
            results.append(
                BatchReceiptItemResult(
                    filename=filename,
                    success=False,
                    status_code=500,
                    error=f"Receipt was extracted but could not be saved: {type(exc).__name__}",
                    detail=str(exc).splitlines()[0][:200],
                )
            )
            failed_count += 1
        except Exception as exc:
            log.exception("batch_upload_generic_error", filename=filename, error=str(exc))
            results.append(
                BatchReceiptItemResult(
                    filename=filename,
                    success=False,
                    status_code=500,
                    error=str(exc),
                )
            )
            failed_count += 1

    return BatchUploadResponse(
        total_files=len(files),
        successful_count=successful_count,
        failed_count=failed_count,
        total_spend=round(total_spend, 2),
        total_items=total_items,
        results=results,
    )



@router.get(
    "/{receipt_id}",
    response_model=ReceiptDetailResponse,
    summary="Get receipt details by ID",
    description="Retrieve a stored receipt and all its purchase line items.",
)
async def get_receipt(
    receipt_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_dep),
) -> ReceiptDetailResponse:
    receipt = await receipt_repo.get_receipt_by_id(db, receipt_id)
    if not receipt:
        raise NotFoundError(
            f"Receipt with ID {receipt_id} not found.",
            detail=f"receipt_id={receipt_id}",
        )
    return receipt_to_detail_response(receipt)


@router.get(
    "",
    response_model=ReceiptListResponse,
    summary="List stored receipts",
    description="List receipts with optional filtering by supplier or date range.",
)
async def list_receipts(
    supplier_id: uuid.UUID | None = Query(None, description="Filter by supplier ID"),
    date_from: date | None = Query(None, description="Start date filter"),
    date_to: date | None = Query(None, description="End date filter"),
    status: ReceiptStatus | None = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=100, description="Page limit"),
    offset: int = Query(0, ge=0, description="Page offset"),
    db: AsyncSession = Depends(get_db_dep),
) -> ReceiptListResponse:
    receipts, total = await receipt_repo.list_receipts(
        db,
        supplier_id=supplier_id,
        date_from=date_from,
        date_to=date_to,
        status=status,
        limit=limit,
        offset=offset,
    )
    return ReceiptListResponse(
        total=total,
        receipts=[receipt_to_detail_response(r) for r in receipts],
    )
