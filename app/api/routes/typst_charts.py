from __future__ import annotations

from datetime import datetime
from io import BytesIO

from fastapi import APIRouter, HTTPException
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from app.schemas import ChartRenderRequest
from .typst_shared import project_charts_dir

router = APIRouter(tags=["typst"])


@router.post("/charts/render")
async def render_chart(request: ChartRenderRequest):
    try:
        charts_dir = project_charts_dir(request.project_id)
        charts_dir.mkdir(parents=True, exist_ok=True)

        fig, ax = plt.subplots(figsize=(6, 4))

        if request.chart_type == "bar":
            ax.bar(request.labels, request.values)
        elif request.chart_type == "line":
            ax.plot(request.labels, request.values)
        elif request.chart_type == "pie":
            ax.pie(request.values, labels=request.labels, autopct="%1.1f%%")
        else:
            raise HTTPException(status_code=400, detail="Unsupported chart type")

        ax.set_title(request.title or "")

        buf = BytesIO()
        fig.tight_layout()
        fig.savefig(buf, format="png", dpi=200)
        plt.close(fig)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"chart_{timestamp}.png"
        dest_path = charts_dir / filename
        dest_path.write_bytes(buf.getvalue())

        public_path = f"/static/projects/{request.project_id}/charts/{filename}"
        return {"path": public_path}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
