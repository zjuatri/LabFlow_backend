from pathlib import Path
import os

from dotenv import load_dotenv

# Load environment variables from LabFlow_backend/.env as early as possible.
# Important: modules like db/security read env at import time.
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .db import Base, engine

app = FastAPI(title="LabFlow Backend")


@app.on_event("startup")
def _create_tables() -> None:
    Base.metadata.create_all(bind=engine)

# 添加 CORS 中间件，允许前端访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static storage root for project files (images, etc.)
STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT") or (Path(__file__).resolve().parent.parent / "storage"))
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

# Serve static files under /static
app.mount("/static", StaticFiles(directory=str(STORAGE_ROOT)), name="static")

app.include_router(router, prefix="/api")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
