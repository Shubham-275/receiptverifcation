"""
Image validation utilities.

Runs BEFORE any AI call to catch obviously invalid uploads cheaply:
  - File size check
  - MIME type / magic bytes check
  - PIL can actually decode the image
  - Minimum dimensions (avoid 1×1 pixel images)

Raises InvalidImageError on failure so the classifier never sees garbage.
"""
import io
from typing import NamedTuple

from PIL import Image, UnidentifiedImageError

from app.config import get_settings
from app.exceptions import InvalidImageError
from app.logging_config import get_logger

log = get_logger(__name__)

# PIL format string → canonical MIME type
_PIL_FORMAT_TO_MIME: dict[str, str] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "HEIF": "image/heic",
    "HEIC": "image/heic",
}

# Minimum image size we'll attempt to classify
_MIN_WIDTH = 100
_MIN_HEIGHT = 100


class ImageInfo(NamedTuple):
    mime_type: str      # e.g. "image/jpeg"
    width: int
    height: int
    size_bytes: int
    pil_image: Image.Image


def validate_image(data: bytes, filename: str = "") -> ImageInfo:
    """
    Validate raw image bytes and return metadata.

    Args:
        data:     Raw bytes of the uploaded file.
        filename: Original filename (used only for log context).

    Returns:
        ImageInfo with MIME type, dimensions, size, and PIL image.

    Raises:
        InvalidImageError: If the file is too large, wrong type, or corrupt.
    """
    settings = get_settings()

    # 1. Size gate (before decoding — cheap)
    size_bytes = len(data)
    if size_bytes == 0:
        raise InvalidImageError("Uploaded file is empty.")
    if size_bytes > settings.max_upload_bytes:
        mb = settings.max_upload_bytes / (1024 * 1024)
        raise InvalidImageError(
            f"File too large ({size_bytes / 1024 / 1024:.1f} MB). "
            f"Maximum allowed: {mb:.0f} MB."
        )

    # 2. Try to open with PIL — catches corrupt files and wrong formats
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()          # raises on corrupt data
        img = Image.open(io.BytesIO(data))  # re-open after verify (verify closes the file)
    except UnidentifiedImageError:
        raise InvalidImageError(
            "Uploaded file is not a recognisable image. "
            "Accepted formats: JPEG, PNG, WebP, HEIC."
        )
    except Exception as exc:
        raise InvalidImageError(f"Image appears corrupt or unreadable: {exc}")

    # 3. Format / MIME check
    pil_format = (img.format or "").upper()
    mime_type = _PIL_FORMAT_TO_MIME.get(pil_format)
    if mime_type is None or mime_type not in settings.allowed_image_types:
        raise InvalidImageError(
            f"Unsupported image format: {pil_format or 'unknown'}. "
            f"Accepted: {', '.join(settings.allowed_image_types)}."
        )

    # 4. Dimension sanity check
    width, height = img.size
    if width < _MIN_WIDTH or height < _MIN_HEIGHT:
        raise InvalidImageError(
            f"Image is too small ({width}×{height} px). "
            f"Minimum: {_MIN_WIDTH}×{_MIN_HEIGHT} px."
        )

    log.debug(
        "image_validated",
        filename=filename,
        mime=mime_type,
        size_bytes=size_bytes,
        dimensions=f"{width}x{height}",
    )
    return ImageInfo(
        mime_type=mime_type,
        width=width,
        height=height,
        size_bytes=size_bytes,
        pil_image=img,
    )
