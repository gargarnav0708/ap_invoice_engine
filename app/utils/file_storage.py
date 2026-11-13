"""
Secure file storage and path handling utilities.
"""
import re
import uuid
from pathlib import Path
from typing import Tuple
from app.config.settings import settings
from app.core.exceptions import IngestionError

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB
PDF_MAGIC_BYTES = b"%PDF"


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


def save_uploaded_file(data: bytes, original_filename: str) -> Tuple[Path, str]:
    """
    Validate and save uploaded PDF bytes into the secure storage directory.
    Returns (saved_path, sanitized_name).
    """
    validate_pdf_bytes(data, original_filename)
    safe_name = sanitize_filename(original_filename)
    unique_prefix = uuid.uuid4().hex[:12]
    stored_name = f"{unique_prefix}_{safe_name}"

    target_path = settings.STORAGE_DIR / stored_name
    with open(target_path, "wb") as f:
        f.write(data)

    return target_path, safe_name
