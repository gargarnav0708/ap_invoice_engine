"""
OCR engine abstraction supporting Tesseract and cloud OCR pluggability.
"""
from abc import ABC, abstractmethod
from typing import Any, List, Optional
from PIL import Image, ImageOps
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

    def extract_text_with_confidence(self, image: Image.Image) -> tuple[str, Optional[float]]:
        """Return text and word confidence where the OCR engine provides it."""
        return self.extract_text_from_image(image), None

    def extract_text_with_word_confidence(self, image: Image.Image) -> tuple[str, Optional[float], List[dict]]:
        text, confidence = self.extract_text_with_confidence(image)
        return text, confidence, []


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
        text, _ = self.extract_text_with_confidence(image)
        return text

    @staticmethod
    def _prepare_image(image: Image.Image) -> Image.Image:
        """Crop blank page margins, deskew small rotations, and enlarge small text."""
        image = ImageOps.grayscale(ImageOps.exif_transpose(image))
        foreground = image.point(lambda value: 255 if value < 248 else 0)
        bounds = foreground.getbbox()
        if bounds:
            left, top, right, bottom = bounds
            pad = max(4, int(max(image.size) * 0.015))
            image = image.crop((
                max(0, left - pad), max(0, top - pad),
                min(image.width, right + pad), min(image.height, bottom + pad),
            ))

        # Estimate small skew on a downscaled binary copy; keep this CPU bounded.
        estimate = image.copy()
        if estimate.width > 600:
            estimate = estimate.resize((600, max(1, round(estimate.height * 600 / estimate.width))))
        binary = estimate.point(lambda value: 0 if value < 180 else 255)

        def projection_score(candidate: Image.Image) -> float:
            row_black_pixels = [
                candidate.crop((0, row, candidate.width, row + 1)).histogram()[0]
                for row in range(candidate.height)
            ]
            return sum(count * count for count in row_black_pixels) - (
                sum(row_black_pixels) ** 2 / max(1, len(row_black_pixels))
            )

        base_score = projection_score(binary)
        best_score, best_angle = base_score, 0.0
        for step in range(-6, 7):
            angle = step * 0.5
            if angle == 0:
                continue
            rotated = binary.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255)
            score = projection_score(rotated)
            if score > best_score:
                best_score, best_angle = score, angle
        if best_angle and best_score > base_score * 1.01:
            image = image.rotate(best_angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255)

        image = ImageOps.autocontrast(image)
        if image.width < 1200:
            scale = min(4.0, 1200 / image.width)
            image = image.resize(
                (round(image.width * scale), round(image.height * scale)),
                Image.Resampling.LANCZOS,
            )
        return image

    def extract_text_with_confidence(self, image: Image.Image) -> tuple[str, Optional[float]]:
        text, confidence, _ = self.extract_text_with_word_confidence(image)
        return text, confidence

    def extract_text_with_word_confidence(self, image: Image.Image) -> tuple[str, Optional[float], List[dict]]:
        if not self.is_available():
            logger.warning("Tesseract binary not found on system. OCR unavailable.")
            return "", None, []
        try:
            prepared = self._prepare_image(image)
            candidates = []
            for psm in (6, 11):
                data = self.pytesseract.image_to_data(
                    prepared,
                    config=f"--oem 3 --psm {psm}",
                    output_type=self.pytesseract.Output.DICT,
                )
                lines: dict[tuple[Any, ...], list[str]] = {}
                confidences: list[float] = []
                words: list[dict[str, Any]] = []
                for idx, raw_word in enumerate(data.get("text", [])):
                    word = raw_word.strip()
                    if not word:
                        continue
                    try:
                        confidence = float(data["conf"][idx])
                    except (TypeError, ValueError, KeyError):
                        continue
                    if confidence < 0:
                        continue
                    line_key = tuple(data[key][idx] for key in ("block_num", "par_num", "line_num"))
                    lines.setdefault(line_key, []).append(word)
                    confidences.append(confidence)
                    words.append({"text": word, "confidence": round(confidence, 1)})
                text = "\n".join(" ".join(words) for words in lines.values()).strip()
                if text and confidences:
                    candidates.append((sum(confidences) / len(confidences), text, words))

            if not candidates:
                return "", None, []
            confidence, text, words = max(candidates, key=lambda candidate: candidate[0])
            return text, round(confidence, 1), words
        except Exception as e:
            logger.error(f"Tesseract OCR failed: {e}")
            return "", None, []


class FallbackOCREngine(BaseOCREngine):
    """Unavailable OCR implementation used when no OCR runtime is installed."""

    def is_available(self) -> bool:
        return False

    def extract_text_from_image(self, image: Image.Image) -> str:
        logger.warning("OCR is unavailable; scanned page was not transcribed.")
        return ""

    def extract_text_with_confidence(self, image: Image.Image) -> tuple[str, Optional[float]]:
        return self.extract_text_from_image(image), None

    def extract_text_with_word_confidence(self, image: Image.Image) -> tuple[str, Optional[float], List[dict]]:
        return self.extract_text_from_image(image), None, []


def get_ocr_engine() -> BaseOCREngine:
    """Factory to get the configured OCR engine."""
    tesseract = TesseractOCREngine()
    if tesseract.is_available() and settings.OCR_ENABLED:
        logger.info(f"Using Tesseract OCR Engine at {tesseract.cmd}")
        return tesseract
    logger.info("Using Fallback OCR Engine")
    return FallbackOCREngine()
