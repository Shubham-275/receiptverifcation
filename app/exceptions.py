"""
Application-level exceptions and FastAPI exception handlers.

All domain errors inherit from AppError so the handler can produce
a consistent JSON error response without leaking internals.
"""
from fastapi import Request
from fastapi.responses import JSONResponse


# ── Base ───────────────────────────────────────────────────────────────────────

class AppError(Exception):
    """Base class for all application errors."""

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail


# ── 400 Bad Request ────────────────────────────────────────────────────────────

class ValidationError(AppError):
    status_code = 400
    error_code = "VALIDATION_ERROR"


class InvalidImageError(AppError):
    """Uploaded file is not a valid image or exceeds size limit."""
    status_code = 400
    error_code = "INVALID_IMAGE"


# ── 404 Not Found ──────────────────────────────────────────────────────────────

class NotFoundError(AppError):
    status_code = 404
    error_code = "NOT_FOUND"


# ── 422 Unprocessable ──────────────────────────────────────────────────────────

class NotAReceiptError(AppError):
    """Image was classified as NOT a supplier receipt."""
    status_code = 422
    error_code = "NOT_A_RECEIPT"


class ExtractionError(AppError):
    """Receipt image could not be parsed into structured data."""
    status_code = 422
    error_code = "EXTRACTION_FAILED"


# ── 503 Service Unavailable ───────────────────────────────────────────────────

class DatabaseError(AppError):
    status_code = 503
    error_code = "DATABASE_ERROR"


class AIServiceError(AppError):
    """Gemini API or other AI service is unavailable / returned an error."""
    status_code = 503
    error_code = "AI_SERVICE_ERROR"


# ── FastAPI handler ────────────────────────────────────────────────────────────

async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.error_code,
            "message": exc.message,
            "detail": exc.detail,
        },
    )
