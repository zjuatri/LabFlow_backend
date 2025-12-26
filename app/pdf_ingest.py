from __future__ import annotations

import io
from collections import defaultdict
from dataclasses import dataclass

import pdfplumber


@dataclass
class ExtractedTable:
    page_number: int
    rows: int
    cols: int
    cells: list[list[dict[str, object]]]
    csv_preview: str


def _grid_from_table_cells(table) -> tuple[list[float], list[float], dict[tuple[int, int], dict[str, object]]]:
    """Build a grid model with merge info from a pdfplumber Table.

    Returns:
        xs: x boundaries (len cols+1)
        ys: y boundaries (len rows+1)
        anchors: mapping (r,c) -> cell dict {content,rowspan,colspan,is_placeholder}

    Notes:
        pdfplumber's `table.cells` provides cell bboxes that already reflect merged cells.
        We convert that into row/col spans by projecting bboxes onto the grid boundaries.
    """

    # Collect unique boundaries.
    xs_set: set[float] = set()
    ys_set: set[float] = set()
    def _cell_bbox(cell_obj) -> tuple[float, float, float, float] | None:
        # pdfplumber cell could be a dict-like with keys, or a tuple/list.
        # We need (x0, top, x1, bottom).
        try:
            if hasattr(cell_obj, "get"):
                x0 = float(cell_obj.get("x0"))
                x1 = float(cell_obj.get("x1"))
                top = float(cell_obj.get("top"))
                bottom = float(cell_obj.get("bottom"))
                return x0, top, x1, bottom
        except Exception:
            pass

        # Tuple forms we may see:
        # - (x0, top, x1, bottom)
        # - (x0, x1, top, bottom) (less likely)
        try:
            if isinstance(cell_obj, (tuple, list)) and len(cell_obj) == 4:
                a, b, c, d = [float(v) for v in cell_obj]
                # Heuristic: top < bottom in PDF coordinate system used by pdfplumber.
                # Choose the permutation where (top,bottom) are ordered.
                if b < d and a < c:
                    # (x0, top, x1, bottom)
                    return a, b, c, d
                if c < d and a < b:
                    # (x0, x1, top, bottom)
                    return a, c, b, d
                # fallback assume (x0, top, x1, bottom)
                return a, b, c, d
        except Exception:
            pass

        return None

    for cell in getattr(table, "cells", []) or []:
        bbox = _cell_bbox(cell)
        if bbox is None:
            continue
        x0, top, x1, bottom = bbox
        xs_set.add(x0)
        xs_set.add(x1)
        ys_set.add(top)
        ys_set.add(bottom)

    xs = sorted(xs_set)
    ys = sorted(ys_set)
    if len(xs) < 2 or len(ys) < 2:
        return [], [], {}

    def _idx(boundaries: list[float], v: float) -> int:
        # snap by nearest boundary (tolerant to float noise)
        best_i = 0
        best_d = abs(boundaries[0] - v)
        for i, b in enumerate(boundaries):
            d = abs(b - v)
            if d < best_d:
                best_d = d
                best_i = i
        return best_i

    # Map each cell bbox to a grid anchor and span.
    anchors: dict[tuple[int, int], dict[str, object]] = {}
    occupied: set[tuple[int, int]] = set()

    # Extract text per cell bbox using the page's char map.
    for cell in getattr(table, "cells", []) or []:
        bbox = _cell_bbox(cell)
        if bbox is None:
            continue
        x0, top, x1, bottom = bbox

        c0 = _idx(xs, x0)
        c1 = _idx(xs, x1)
        r0 = _idx(ys, top)
        r1 = _idx(ys, bottom)

        colspan = max(1, c1 - c0)
        rowspan = max(1, r1 - r0)

        # Best-effort text inside bbox:
        txt = ""
        try:
            # pdfplumber Table has a reference to page via .page
            page = getattr(table, "page", None)
            if page is not None:
                txt = (page.within_bbox((x0, top, x1, bottom)).extract_text(x_tolerance=2, y_tolerance=2) or "").strip()
        except Exception:
            txt = ""

        anchors[(r0, c0)] = {
            "content": txt,
            "rowspan": rowspan,
            "colspan": colspan,
            "is_placeholder": False,
            "bbox": {"x0": x0, "top": top, "x1": x1, "bottom": bottom},
        }

        # Mark occupied cells for placeholder generation.
        for rr in range(r0, r0 + rowspan):
            for cc in range(c0, c0 + colspan):
                occupied.add((rr, cc))

    # Fill placeholders for cells covered by spans but not anchors.
    rows = len(ys) - 1
    cols = len(xs) - 1
    for r in range(rows):
        for c in range(cols):
            if (r, c) in anchors:
                continue
            if (r, c) in occupied:
                anchors[(r, c)] = {
                    "content": "",
                    "rowspan": 1,
                    "colspan": 1,
                    "is_placeholder": True,
                    "bbox": None,
                }

    return xs, ys, anchors


def _matrix_from_grid(xs: list[float], ys: list[float], anchors: dict[tuple[int, int], dict[str, object]]) -> list[list[dict[str, object]]]:
    rows = max(0, len(ys) - 1)
    cols = max(0, len(xs) - 1)
    out: list[list[dict[str, object]]] = []
    for r in range(rows):
        row: list[dict[str, object]] = []
        for c in range(cols):
            row.append(anchors.get((r, c), {"content": "", "rowspan": 1, "colspan": 1, "is_placeholder": False}))
        out.append(row)
    return out


@dataclass
class PdfIngestResult:
    pages_text: list[str]
    tables: list[ExtractedTable]


def _safe_truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


def extract_pdf_payload(
    pdf_bytes: bytes,
    *,
    max_pages: int = 10,
    max_chars_per_page: int = 20000,
    page_start: int | None = None,
    page_end: int | None = None,
) -> PdfIngestResult:
    pages_text: list[str] = []
    tables: list[ExtractedTable] = []
    # Images are extracted and saved via `app/pdf_images.py` (pypdf), not here.

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        total_pages = len(pdf.pages)
        start_idx = (page_start - 1) if page_start is not None else 0
        end_idx_exclusive = page_end if page_end is not None else total_pages
        start_idx = max(0, min(start_idx, total_pages))
        end_idx_exclusive = max(0, min(end_idx_exclusive, total_pages))

        selected_pages = pdf.pages[start_idx:end_idx_exclusive]
        if max_pages > 0:
            selected_pages = selected_pages[:max_pages]

        for local_idx, page in enumerate(selected_pages):
            page_number = start_idx + local_idx + 1

            text = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
            pages_text.append(_safe_truncate(text, max_chars_per_page))

            # Prefer structured tables (keeps merge information) when possible.
            structured_tables = []
            try:
                structured_tables = page.find_tables() or []
            except Exception:
                structured_tables = []

            if structured_tables:
                for tb in structured_tables:
                    xs, ys, anchors = _grid_from_table_cells(tb)
                    if not xs or not ys:
                        continue

                    cell_matrix = _matrix_from_grid(xs, ys, anchors)
                    rows = len(cell_matrix)
                    cols = len(cell_matrix[0]) if rows else 0

                    # Create a compact csv preview from visible (non-placeholder) cells.
                    csv_lines = []
                    for r in cell_matrix[: min(rows, 20)]:
                        line = []
                        for c in r:
                            if bool(c.get("is_placeholder")):
                                line.append("")
                            else:
                                line.append(str(c.get("content") or "").replace("\n", " ").replace(",", " "))
                        csv_lines.append(",".join(line))
                    csv_preview = "\n".join(csv_lines)

                    tables.append(
                        ExtractedTable(
                            page_number=page_number,
                            rows=rows,
                            cols=cols,
                            cells=cell_matrix,
                            csv_preview=csv_preview,
                        )
                    )
            else:
                # Fallback: plain matrix extraction (no merge info).
                extracted_tables = page.extract_tables() or []
                for t in extracted_tables:
                    norm_rows: list[list[str]] = []
                    for row in t:
                        norm_rows.append([("" if c is None else str(c)) for c in row])

                    if not norm_rows:
                        continue

                    cols = max((len(r) for r in norm_rows), default=0)
                    norm_rows = [r + [""] * (cols - len(r)) for r in norm_rows]

                    cells: list[list[dict[str, object]]] = [
                        [{"content": c, "rowspan": 1, "colspan": 1, "is_placeholder": False} for c in r]
                        for r in norm_rows
                    ]

                    csv_lines = []
                    for r in norm_rows[: min(len(norm_rows), 20)]:
                        csv_lines.append(",".join([c.replace("\n", " ").replace(",", " ") for c in r]))
                    csv_preview = "\n".join(csv_lines)

                    tables.append(
                        ExtractedTable(
                            page_number=page_number,
                            rows=len(norm_rows),
                            cols=cols,
                            cells=cells,
                            csv_preview=csv_preview,
                        )
                    )

    return PdfIngestResult(pages_text=pages_text, tables=tables)
