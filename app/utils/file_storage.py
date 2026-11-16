"""
Secure file storage and path handling utilities.
"""
import re
import uuid
from io import BytesIO
from pathlib import Path
from typing import Tuple
from PIL import Image, UnidentifiedImageError
from app.config.settings import settings
from app.core.exceptions import IngestionError

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB
MAX_IMAGE_PIXELS = 100_000_000
PDF_MAGIC_BYTES = b"%PDF"
SUPPORTED_IMAGE_FORMATS = {"PNG", "JPEG", "TIFF", "BMP"}
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent directory traversal and remove unsafe chars."""
    basename = Path(filename).name
    # Keep alphanumeric, dashes, dots, underscores
    sanitized = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", basename)
    return sanitized or "document.pdf"


def validate_pdf_bytes(data: bytes, filename: str) -> None:
    """Validate PDF data for size and magic header."""
    if len(data) == 0:
        raise IngestionError(f"Uploaded file '{filename}' is empty.")
    if len(data) > MAX_FILE_SIZE_BYTES:
        raise IngestionError(
            f"File '{filename}' exceeds maximum allowed size of {MAX_FILE_SIZE_BYTES / (1024*1024):.0f}MB."
        )
    if not data.startswith(PDF_MAGIC_BYTES):
        raise IngestionError(f"File '{filename}' is not a valid PDF document (missing %PDF header).")


def validate_document_bytes(data: bytes, filename: str) -> str:
    """Validate a supported PDF or raster invoice image and return its kind."""
    if len(data) == 0:
        raise IngestionError(f"Uploaded file '{filename}' is empty.")
    if len(data) > MAX_FILE_SIZE_BYTES:
        raise IngestionError(
            f"File '{filename}' exceeds maximum allowed size of {MAX_FILE_SIZE_BYTES / (1024*1024):.0f}MB."
        )

    suffix = Path(filename).suffix.lower()
    if data.startswith(PDF_MAGIC_BYTES):
        if suffix != ".pdf":
            raise IngestionError("PDF content must use a .pdf filename extension.")
        return "pdf"
    if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
        raise IngestionError("Upload a PDF or a PNG, JPEG, TIFF, or BMP invoice image.")

    try:
        image = Image.open(BytesIO(data))
        if (image.format or "").upper() not in SUPPORTED_IMAGE_FORMATS:
            raise IngestionError(f"Image format '{image.format}' is not supported.")
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise IngestionError(f"Image '{filename}' exceeds the {MAX_IMAGE_PIXELS:,}-pixel limit.")
        image.verify()
    except IngestionError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise IngestionError(f"File '{filename}' is not a valid supported invoice image: {exc}") from exc
    return "image"


def save_uploaded_file(data: bytes, original_filename: str) -> Tuple[Path, str]:
    """
    Validate and save uploaded PDF or raster image bytes into secure storage.
    Returns (saved_path, sanitized_name).
    """
    validate_document_bytes(data, original_filename)
    safe_name = sanitize_filename(original_filename)
    unique_prefix = uuid.uuid4().hex[:12]
    stored_name = f"{unique_prefix}_{safe_name}"

    target_path = settings.STORAGE_DIR / stored_name
    with open(target_path, "wb") as f:
        f.write(data)

    return target_path, safe_name
