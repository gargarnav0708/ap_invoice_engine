"""
OCR engine abstraction supporting Tesseract and cloud OCR pluggability.
"""
from abc import ABC, abstractmethod
from typing import List, Optional
from PIL import Image
import io
import shutil

from app.config.settings import settings
from app.utils.logging import get_logger

logger = get_logger("ocr_engine")


class BaseOCREngine(ABC):
    """Abstract interface for OCR engines."""

    @abstractmethod
    def extract_text_from_image(self, image: Image.Image) -> str:
        """Extract text from a PIL Image."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if OCR runtime is available."""
        pass


class TesseractOCREngine(BaseOCREngine):
    """Tesseract OCR implementation via pytesseract."""

    def __init__(self, tesseract_cmd: Optional[str] = None):
        import pytesseract
        self.pytesseract = pytesseract
        self.cmd = tesseract_cmd or settings.TESSERACT_CMD or shutil.which("tesseract")
        if self.cmd:
            self.pytesseract.pytesseract.tesseract_cmd = self.cmd

    def is_available(self) -> bool:
        return bool(self.cmd and shutil.which(self.cmd))

    def extract_text_from_image(self, image: Image.Image) -> str:
        if not self.is_available():
            logger.warning("Tesseract binary not found on system. OCR unavailable.")
            return ""
        try:
            text = self.pytesseract.image_to_string(image)
            return text.strip()
        except Exception as e:
            logger.error(f"Tesseract OCR failed: {e}")
            return ""


class FallbackOCREngine(BaseOCREngine):
    """Fallback OCR engine used when local Tesseract is not installed."""

    def is_available(self) -> bool:
        return True

    def extract_text_from_image(self, image: Image.Image) -> str:
        logger.info("Using Fallback OCR Engine (Tesseract not installed on host).")
        return "[OCR_FALLBACK_TEXT: Scanned document text unreadable without OCR binary]"


def get_ocr_engine() -> BaseOCREngine:
    """Factory to get the configured OCR engine."""
    tesseract = TesseractOCREngine()
    if tesseract.is_available() and settings.OCR_ENABLED:
        logger.info(f"Using Tesseract OCR Engine at {tesseract.cmd}")
        return tesseract
    logger.info("Using Fallback OCR Engine")
    return FallbackOCREngine()
