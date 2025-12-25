from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.schemas import TypstRenderRequest
from .typst_shared import prepare_typst_compilation

router = APIRouter(tags=["typst"])


@router.post("/render-typst")
async def render_typst(request: TypstRenderRequest):
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            code = prepare_typst_compilation(request.code, temp_path)

            typst_file = temp_path / "temp.typ"
            typst_file.write_text(code, encoding="utf-8")

            # Frontend expects SVG pages JSON: { pages: string[] }
            # Use typst compile to generate SVG output; typst will create multiple pages
            # when the output path contains a page placeholder.
            out_pattern = "page-{n}.svg"
            cmd = ["typst", "compile", "temp.typ", out_pattern]
            # On Windows, text=True may decode using a locale codec (e.g., GBK) and crash.
            # Capture as bytes and decode safely.
            result = subprocess.run(cmd, cwd=temp_path, capture_output=True)

            if result.returncode != 0:
                stderr = (result.stderr or b"").decode("utf-8", errors="replace")
                raise HTTPException(status_code=400, detail=f"Typst compilation error: {stderr}")

            svg_files = sorted(temp_path.glob("page-*.svg"))
            if not svg_files:
                # Fallback: some typst versions output a single file
                single_svg = temp_path / "page.svg"
                if single_svg.exists():
                    svg_files = [single_svg]

            if not svg_files:
                raise HTTPException(status_code=500, detail="Failed to generate SVG")

            pages: list[str] = []
            for svg_path in svg_files:
                pages.append(svg_path.read_text(encoding="utf-8"))

            return {"pages": pages}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/render-typst/pdf")
async def render_typst_pdf(request: TypstRenderRequest):
    """Render typst code to a PDF for download."""
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            code = prepare_typst_compilation(request.code, temp_path)

            typst_file = temp_path / "temp.typ"
            typst_file.write_text(code, encoding="utf-8")

            cmd = ["typst", "compile", "temp.typ", "output.pdf"]
            result = subprocess.run(cmd, cwd=temp_path, capture_output=True)

            if result.returncode != 0:
                stderr = (result.stderr or b"").decode("utf-8", errors="replace")
                raise HTTPException(status_code=400, detail=f"Typst compilation error: {stderr}")

            pdf_path = temp_path / "output.pdf"
            if not pdf_path.exists():
                raise HTTPException(status_code=500, detail="Failed to generate PDF")

            pdf_bytes = pdf_path.read_bytes()
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": "attachment; filename=output.pdf"},
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
