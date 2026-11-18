"""
Bounded document preview for Invoice Details.

Only the selected PDF page is rasterised, at a DPI derived from the zoom
level and capped by ``PREVIEW_MAX_DPI`` / ``PREVIEW_MAX_PIXELS``. Supports
page rotation (0°/90°/180°/270°) and optional on-page bounding-box highlights
for extracted field values or user search terms (inspired by InvoiceNet's
PDFViewer). Raster uploads are shown without PDF-only page controls. Missing
files (e.g. after a Render restart wiped temporary storage), invalid documents
and rendering errors produce an explanation instead of an exception.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from app.config import ui as ui_config

PDF_MAGIC = b"%PDF"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}

MISSING_FILE_MESSAGE = (
    "The original document is no longer available on this server. "
    "Uploads are kept in local file storage; on the Render demo that storage is temporary "
    "(/tmp) and is cleared whenever the service restarts or redeploys. "
    "The extracted fields, decision, validation evidence and audit trail below are stored "
    "in the database and remain available."
)


@dataclass(frozen=True)
class DocumentInfo:
    kind: str                # "pdf" | "image" | "missing" | "invalid"
    page_count: int = 0
    message: Optional[str] = None


def clamp_page(page: int, page_count: int) -> int:
    """1-based page clamped into [1, page_count] (1 when the count is unknown)."""
    if page_count <= 0:
        return 1
    return min(max(1, int(page)), page_count)


def normalise_zoom(zoom: int) -> int:
    levels = ui_config.ZOOM_LEVELS
    return zoom if zoom in levels else ui_config.DEFAULT_ZOOM


def normalise_rotation(rotation: int) -> int:
    deg = int(rotation) % 360
    return deg if deg in ui_config.ROTATION_DEGREES else 0


def zoom_to_dpi(zoom: int, page_width_pt: float = 612.0, page_height_pt: float = 792.0) -> int:
    """Rendering DPI for a zoom level, bounded by max DPI and max bitmap size."""
    dpi = ui_config.PREVIEW_BASE_DPI * max(zoom, 50) / 100.0
    dpi = min(dpi, ui_config.PREVIEW_MAX_DPI)
    w_in, h_in = max(page_width_pt, 1.0) / 72.0, max(page_height_pt, 1.0) / 72.0
    max_dpi_for_pixels = (ui_config.PREVIEW_MAX_PIXELS / (w_in * h_in)) ** 0.5
    return max(36, int(min(dpi, max_dpi_for_pixels)))


def inspect_document(path_value: Optional[str]) -> DocumentInfo:
    if not path_value:
        return DocumentInfo("missing", message=MISSING_FILE_MESSAGE)
    path = Path(path_value)
    if not path.is_file():
        return DocumentInfo("missing", message=MISSING_FILE_MESSAGE)
    try:
        with path.open("rb") as handle:
            head = handle.read(8)
    except OSError as exc:
        return DocumentInfo("missing", message=f"{MISSING_FILE_MESSAGE} (Read error: {exc.strerror or exc})")

    if head.startswith(PDF_MAGIC):
        try:
            import pymupdf
            with pymupdf.open(str(path)) as doc:
                if doc.needs_pass:
                    return DocumentInfo("invalid", message="This PDF is encrypted and cannot be previewed.")
                count = len(doc)
        except Exception as exc:  # corrupt / unsupported PDF
            return DocumentInfo("invalid", message=f"This PDF could not be opened for preview: {exc}")
        if count == 0:
            return DocumentInfo("invalid", message="This PDF has no pages to preview.")
        return DocumentInfo("pdf", page_count=count)

    if path.suffix.lower() in IMAGE_SUFFIXES:
        try:
            from PIL import Image
            with Image.open(path) as img:
                img.verify()
        except Exception as exc:
            return DocumentInfo("invalid", message=f"This image could not be opened for preview: {exc}")
        return DocumentInfo("image", page_count=1)

    return DocumentInfo("invalid", message="This file type is not supported for preview.")


def render_pdf_page_annotated(
    path_value: str,
    page: int,
    zoom: int,
    rotation: int = 0,
    highlight_terms: Sequence[str] = (),
) -> tuple[bytes, int]:
    """Rasterise a single PDF page (1-based) with optional rotation and term highlights.

    Returns ``(png_bytes, highlight_box_count)``.
    """
    import pymupdf
    from PIL import Image, ImageDraw

    rot = normalise_rotation(rotation)
    cleaned_terms = [t.strip() for t in highlight_terms if isinstance(t, str) and len(t.strip()) >= 2]

    with pymupdf.open(path_value) as doc:
        index = clamp_page(page, len(doc)) - 1
        pdf_page = doc[index]
        rect = pdf_page.rect
        dpi = zoom_to_dpi(normalise_zoom(zoom), rect.width, rect.height)
        scale = dpi / 72.0

        boxes: list[tuple[float, float, float, float]] = []
        if cleaned_terms:
            for term in cleaned_terms:
                try:
                    for hit in pdf_page.search_for(term):
                        boxes.append(
                            (
                                (hit.x0 - rect.x0) * scale,
                                (hit.y0 - rect.y0) * scale,
                                (hit.x1 - rect.x0) * scale,
                                (hit.y1 - rect.y0) * scale,
                            )
                        )
                except Exception:
                    continue

        pix = pdf_page.get_pixmap(dpi=dpi)
        png_bytes = pix.tobytes("png")

    if not boxes and rot == 0:
        return png_bytes, 0

    with Image.open(io.BytesIO(png_bytes)) as base_img:
        rgba = base_img.convert("RGBA")
        if boxes:
            overlay = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            for x0, y0, x1, y1 in boxes:
                pad = 2.0
                draw.rectangle(
                    [max(0.0, x0 - pad), max(0.0, y0 - pad), x1 + pad, y1 + pad],
                    fill=(255, 213, 79, 85),
                    outline=(217, 119, 6, 230),
                    width=2,
                )
            rgba = Image.alpha_composite(rgba, overlay)
        out_img = rgba.convert("RGB")
        if rot:
            # PIL rotate is counter-clockwise; negative angle rotates clockwise.
            out_img = out_img.rotate(-rot, expand=True)
        buf = io.BytesIO()
        out_img.save(buf, format="PNG")
        return buf.getvalue(), len(boxes)


def render_pdf_page_png(path_value: str, page: int, zoom: int, rotation: int = 0) -> bytes:
    """Rasterise exactly one PDF page (1-based) to PNG bytes."""
    png_bytes, _ = render_pdf_page_annotated(path_value, page, zoom, rotation=rotation)
    return png_bytes


def render_image_png(path_value: str, max_side: int = 2400, rotation: int = 0) -> bytes:
    """Return a display-safe PNG for a raster upload, downscaled if very large."""
    from PIL import Image

    rot = normalise_rotation(rotation)
    with Image.open(path_value) as img:
        img.seek(0)
        frame = img.convert("RGB")
        frame.thumbnail((max_side, max_side))
        if rot:
            frame = frame.rotate(-rot, expand=True)
        buf = io.BytesIO()
        frame.save(buf, format="PNG")
        return buf.getvalue()


def image_html(png: bytes, zoom: int, alt: str) -> str:
    """Zoomable image in a scrollable, keyboard-focusable viewport. Zoom is a
    percentage of the panel width, so >100% scrolls horizontally without clipping."""
    from html import escape

    z = int(normalise_zoom(zoom))
    margin_rule = "margin:0 auto" if z <= 100 else "margin:0"
    data = base64.b64encode(png).decode("ascii")
    return (
        f'<div class="ap-preview" role="region" aria-label="{escape(alt)}" tabindex="0">'
        f'<img src="data:image/png;base64,{data}" alt="{escape(alt)}" '
        f'style="width:{z}%;{margin_rule}"></div>'
    )
