from __future__ import annotations

import io
from dataclasses import dataclass

import pypdfium2 as pdfium
from PIL import Image


@dataclass
class RenderedPage:
    page_number: int
    png_bytes: bytes
    width: int
    height: int


@dataclass
class RenderedCrop:
    page_number: int
    png_bytes: bytes
    width: int
    height: int
    # crop bbox in pdfplumber-style coordinates (x0, top, x1, bottom)
    bbox: tuple[float, float, float, float]


def render_pdf_pages_to_png(
    pdf_bytes: bytes,
    *,
    page_start: int | None = None,
    page_end: int | None = None,
    max_pages: int = 3,
    scale: float = 2.0,
) -> list[RenderedPage]:
    """Render selected PDF pages to PNG bytes using pdfium.

    Args:
        page_start/page_end: 1-based inclusive range. None means from first/to last.
        max_pages: maximum number of pages to render after applying range.
        scale: rendering scale (2.0 ~ 200 DPI-ish). Increase for clearer OCR.
    """

    doc = pdfium.PdfDocument(pdf_bytes)
    total = len(doc)

    start_idx = (page_start - 1) if page_start is not None else 0
    end_idx_exclusive = page_end if page_end is not None else total
    start_idx = max(0, min(start_idx, total))
    end_idx_exclusive = max(0, min(end_idx_exclusive, total))

    indices = list(range(start_idx, end_idx_exclusive))
    if max_pages > 0:
        indices = indices[:max_pages]

    rendered: list[RenderedPage] = []
    for idx in indices:
        page = doc.get_page(idx)
        bitmap = page.render(scale=scale)
        pil_image: Image.Image = bitmap.to_pil()

        buf = io.BytesIO()
        pil_image.save(buf, format="PNG", optimize=True)
        rendered.append(
            RenderedPage(
                page_number=idx + 1,
                png_bytes=buf.getvalue(),
                width=pil_image.width,
                height=pil_image.height,
            )
        )

    return rendered


def render_pdf_crop_to_png(
    pdf_bytes: bytes,
    *,
    page_number: int,
    bbox: tuple[float, float, float, float],
    scale: float = 2.0,
    padding_px: int = 8,
) -> RenderedCrop:
    """Render a cropped region of a single PDF page to PNG.

    Notes:
        - `bbox` uses pdfplumber coordinates: (x0, top, x1, bottom)
        - pdfium renders full page; we crop in pixel space.
    """

    if page_number < 1:
        raise ValueError("page_number must be >= 1")

    x0, top, x1, bottom = bbox
    if x1 <= x0 or bottom <= top:
        raise ValueError("Invalid bbox")

    doc = pdfium.PdfDocument(pdf_bytes)
    total = len(doc)
    if page_number > total:
        raise ValueError("page_number out of range")

    page = doc.get_page(page_number - 1)
    bitmap = page.render(scale=scale)
    pil_image: Image.Image = bitmap.to_pil()

    # Convert PDF coordinates to pixel coordinates.
    # pdfplumber uses origin at top-left. pdfium/PIL image is also top-left.
    page_w_pt = float(page.get_width())
    page_h_pt = float(page.get_height())
    img_w = pil_image.width
    img_h = pil_image.height

    def _clamp(v: int, lo: int, hi: int) -> int:
        return max(lo, min(v, hi))

    left = int(round(x0 / page_w_pt * img_w))
    right = int(round(x1 / page_w_pt * img_w))
    upper = int(round(top / page_h_pt * img_h))
    lower = int(round(bottom / page_h_pt * img_h))

    left = _clamp(left - padding_px, 0, img_w)
    right = _clamp(right + padding_px, 0, img_w)
    upper = _clamp(upper - padding_px, 0, img_h)
    lower = _clamp(lower + padding_px, 0, img_h)

    if right <= left or lower <= upper:
        raise ValueError("Computed empty crop")

    cropped = pil_image.crop((left, upper, right, lower))
    buf = io.BytesIO()
    cropped.save(buf, format="PNG", optimize=True)

    return RenderedCrop(
        page_number=page_number,
        png_bytes=buf.getvalue(),
        width=cropped.width,
        height=cropped.height,
        bbox=bbox,
    )
