from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image
from pypdf import PdfReader


@dataclass
class SavedPdfImage:
    filename: str
    mime: str
    width: int
    height: int
    page_number: int


def _detect_image_ext_and_mime(raw: bytes) -> tuple[str, str]:
    if len(raw) >= 2 and raw[0] == 0xFF and raw[1] == 0xD8:
        return "jpg", "image/jpeg"
    if len(raw) >= 8 and raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", "image/png"
    return "png", "image/png"


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _get_pdf_name(value) -> str | None:
    if value is None:
        return None
    try:
        # pypdf NameObject prints like '/DeviceRGB'
        s = str(value)
    except Exception:
        return None
    return s


def _get_filters(xobj) -> list[str]:
    filt = xobj.get("/Filter") if hasattr(xobj, "get") else None
    out: list[str] = []
    for v in _as_list(filt):
        name = _get_pdf_name(v)
        if name:
            out.append(name)
    return out


def _get_colorspace_name(xobj) -> str | None:
    cs = xobj.get("/ColorSpace") if hasattr(xobj, "get") else None
    name = _get_pdf_name(cs)
    if name and name.startswith("/"):
        return name
    # Some PDFs store ColorSpace as an array like ['/ICCBased', <obj>]
    if isinstance(cs, (list, tuple)) and cs:
        head = _get_pdf_name(cs[0])
        if head and head.startswith("/"):
            return head
    return None


def extract_and_save_embedded_images(
    pdf_bytes: bytes,
    *,
    project_id: str,
    images_dir,
    max_images: int = 50,
    max_bytes: int = 2_000_000,
    page_start: int | None = None,
    page_end: int | None = None,
) -> list[SavedPdfImage]:
    reader = PdfReader(io.BytesIO(pdf_bytes))

    saved: list[SavedPdfImage] = []
    total_pages = len(reader.pages)
    start_idx = (page_start - 1) if page_start is not None else 0
    end_idx_exclusive = page_end if page_end is not None else total_pages
    start_idx = max(0, min(start_idx, total_pages))
    end_idx_exclusive = max(0, min(end_idx_exclusive, total_pages))

    for page_index in range(start_idx, end_idx_exclusive):
        if len(saved) >= max_images:
            break

        page = reader.pages[page_index]

        resources = page.get("/Resources")
        if not resources:
            continue

        xobjects = resources.get("/XObject") if hasattr(resources, "get") else None
        if not xobjects:
            continue

        try:
            xobjects = xobjects.get_object()
        except Exception:
            pass

        for name in list(xobjects.keys()):
            if len(saved) >= max_images:
                break

            try:
                obj = xobjects[name]
                xobj = obj.get_object()
            except Exception:
                continue

            subtype = xobj.get("/Subtype")
            if subtype != "/Image":
                continue

            try:
                raw = xobj.get_data()
            except Exception:
                continue

            if not raw:
                continue

            # Convert to a safe PNG under size limit.
            try:
                image = Image.open(io.BytesIO(raw))
                image.load()
            except Exception:
                # PIL can't open it.
                # Many PDFs store images as raw pixel buffers (e.g. /FlateDecode) without a file header.
                # We try a minimal reconstruction for common cases so browsers can display them.
                filters = _get_filters(xobj)
                cs_name = _get_colorspace_name(xobj)
                width = int(xobj.get("/Width") or 0)
                height = int(xobj.get("/Height") or 0)
                bpc = int(xobj.get("/BitsPerComponent") or 8)

                reconstructed: Image.Image | None = None
                if width > 0 and height > 0 and bpc == 8 and "/FlateDecode" in filters:
                    try:
                        if cs_name == "/DeviceRGB":
                            reconstructed = Image.frombytes("RGB", (width, height), raw)
                        elif cs_name == "/DeviceGray":
                            reconstructed = Image.frombytes("L", (width, height), raw)
                        elif cs_name == "/DeviceCMYK":
                            reconstructed = Image.frombytes("CMYK", (width, height), raw)
                    except Exception:
                        reconstructed = None

                if reconstructed is None:
                    # As a last resort, only save when it already looks like a real JPEG/PNG.
                    ext, mime = _detect_image_ext_and_mime(raw)
                    if ext in ("jpg", "png") and (
                        (ext == "jpg" and raw[:2] == b"\xFF\xD8")
                        or (ext == "png" and raw[:8] == b"\x89PNG\r\n\x1a\n")
                    ):
                        filename = f"pdf_p{page_index + 1}_{len(saved)}.{ext}"
                        dest = images_dir / filename
                        dest.write_bytes(raw[:max_bytes])
                        saved.append(
                            SavedPdfImage(
                                filename=filename,
                                mime=mime,
                                width=width,
                                height=height,
                                page_number=page_index + 1,
                            )
                        )
                    # Otherwise skip unsupported/corrupt image bytes.
                    continue

                # Normalize mode and encode reconstructed image as PNG.
                image = reconstructed

            # Normalize mode (P) etc.
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA") if "A" in image.mode else image.convert("RGB")

            buf = io.BytesIO()
            image.save(buf, format="PNG", optimize=True)
            data = buf.getvalue()

            # Simple size control: downscale until under limit.
            while len(data) > max_bytes and image.width > 64 and image.height > 64:
                image = image.resize((max(64, image.width // 2), max(64, image.height // 2)))
                buf = io.BytesIO()
                image.save(buf, format="PNG", optimize=True)
                data = buf.getvalue()

            filename = f"pdf_p{page_index + 1}_{len(saved)}.png"
            dest = images_dir / filename
            dest.write_bytes(data[:max_bytes])

            saved.append(
                SavedPdfImage(
                    filename=filename,
                    mime="image/png",
                    width=image.width,
                    height=image.height,
                    page_number=page_index + 1,
                )
            )

    return saved
