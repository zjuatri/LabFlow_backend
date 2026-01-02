from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _prompts_dir() -> Path:
    """Directory for editable prompts."""
    root = Path(__file__).resolve().parent.parent / "prompts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_prompts() -> dict[str, Any]:
    """Load all editable prompts from text files. Raises error if files are missing."""
    root = _prompts_dir()
    
    out = {
        "updated_at": None,
    }

    # helper to read file safely
    def read_file(name: str) -> str:
        p = root / f"{name}.txt"
        if not p.exists():
            raise FileNotFoundError(f"Prompt file not found: {name}.txt (at {p})")
        return p.read_text(encoding="utf-8")

    # Load from text files (Strict Mode: No Defaults)
    for key in ["ai_prompt", "ai_assistant_prompt", "pdf_page_ocr_prompt", "table_cell_ocr_prompt"]:
        out[key] = read_file(key)

    # Determine "updated_at" from the modification time of ai_prompt.txt
    ai_path = root / "ai_prompt.txt"
    if ai_path.exists():
        from datetime import datetime, timezone
        ts = ai_path.stat().st_mtime
        out["updated_at"] = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        
    return out


def save_prompts(
    *,
    ai_prompt: str | None = None,
    ai_assistant_prompt: str | None = None,
    pdf_page_ocr_prompt: str | None = None,
    table_cell_ocr_prompt: str | None = None,
) -> dict[str, Any]:
    """Save one or more prompts to text files."""

    root = _prompts_dir()
    
    to_save = {
        "ai_prompt": ai_prompt,
        "ai_assistant_prompt": ai_assistant_prompt,
        "pdf_page_ocr_prompt": pdf_page_ocr_prompt,
        "table_cell_ocr_prompt": table_cell_ocr_prompt,
    }

    for key, val in to_save.items():
        if val is not None:
            (root / f"{key}.txt").write_text(val, encoding="utf-8")

    return load_prompts()


def load_prompt() -> dict[str, Any]:
    data = load_prompts()
    return {"ai_prompt": data["ai_prompt"], "updated_at": data.get("updated_at")}


def load_assistant_prompt() -> dict[str, Any]:
    data = load_prompts()
    return {"ai_prompt": data["ai_assistant_prompt"], "updated_at": data.get("updated_at")}


def save_prompt(ai_prompt: str) -> dict[str, Any]:
    return save_prompts(ai_prompt=ai_prompt)
