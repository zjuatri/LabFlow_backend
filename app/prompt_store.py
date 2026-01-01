from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _prompts_path() -> Path:
    """Path for editable prompts.

    We keep prompts out of /storage so they can be committed to git, while
    still supporting older deployments that stored them under STORAGE_ROOT.
    """

    # Preferred, repo-friendly location.
    root = Path(__file__).resolve().parent.parent / "prompts"
    root.mkdir(parents=True, exist_ok=True)
    return root / "ai_prompts.json"


def _storage_path() -> Path:
    # Persist under backend storage so it survives restarts and is easy to mount.
    root = Path(os.getenv("STORAGE_ROOT") or (Path(__file__).resolve().parent.parent / "storage"))
    root.mkdir(parents=True, exist_ok=True)
    return root / "ai_prompts.json"


DEFAULT_AI_PROMPT_PREFIX = (
    "你是实验报告写作助手。你的输出会被程序解析为 JSON 并写入可视化编辑器。\n\n"
    "硬性要求：\n"
    "1) 只输出 JSON，不要输出解释、Markdown、代码围栏、注释。\n"
    "2) 输出必须是一个对象：{\"settings\": {...}, \"blocks\": [...]}\n"
    "3) blocks 中每个元素必须包含：id(string, 唯一), type, content，并可选 level/language/width/align/caption 等。\n"
    "4) 图片 block 的 content 必须使用 /static/projects/<project_id>/images/<filename>（不要 http 链接；不要带 ?t=）。\n"
    "5) 表格/图表优先使用 tablePayload/chartPayload（对象）避免转义错误。\n"
    "6) 不要编造用户未提供的数据；缺失部分用“待补充：...”占位。\n\n"
    "生成目标：产出结构完整、可编辑的实验报告 blocks。章节至少包含：摘要、原理、步骤、数据与处理、讨论、结论、参考文献。\n\n"
)


DEFAULT_AI_PROMPT_TEMPLATE = (
    DEFAULT_AI_PROMPT_PREFIX
    + "下面是用户提供的信息（JSON）：\n{{USER_INPUT_JSON}}\n\n"
    + "【必须使用的 project_id】{{PROJECT_ID}}\n"
    + "请将所有图片路径中的 <project_id> 替换为上述 project_id。\n"
)


DEFAULT_PDF_PAGE_OCR_PROMPT = (
    "你是一个严谨的 OCR 助手，擅长从论文/教材 PDF 截图中提取文字与数学公式。\n"
    "我会给你一张 PDF 页面截图。\n"
    "请输出 JSON，格式必须严格为：{\"lines\": [\"...\", \"...\"]}。\n"
    "要求：\n"
    "1) 普通文字按原顺序输出；每一行单独作为 lines 数组的一个元素（不要把整页塞进一个长字符串）。\n"
    "2) 任何数学符号/公式必须转写为 LaTeX。\n"
    "3) 行内公式必须用 \\( ... \\) 包裹；行间公式必须用 \\[ ... \\] 包裹。\n"
    "4) 禁止输出 Unicode 数学形式（例如 η、α、β、₀、上标/下标字符）。必须用 LaTeX（例如 \\\\eta, \\\\alpha_0）。\n"
    "5) JSON 字符串中的反斜杠必须转义为双反斜杠（例如 \\\\beta、\\\\text）。\n"
    "6) 只输出 JSON，不要输出任何额外文字。\n"
)


DEFAULT_TABLE_CELL_OCR_PROMPT = (
    "你是一个严谨的 OCR/公式识别助手。\n"
    "我会给你一张来自 PDF 表格单元格的截图。\n"
    "请你：\n"
    "1) 如果单元格中包含数学公式，尽可能转换为 LaTeX。\n"
    "2) 如果没有公式，latex 返回空字符串。\n"
    "3) 最终输出必须是有效的 JSON: {\"latex\": \"string\"}。\n"
    "4) 注意：LaTeX 中的反斜杠必须转义为双反斜杠（例如 \\\\beta 而不是 \\beta）。\n"
    "不要输出任何额外文字。\n"
)


def load_prompts() -> dict[str, Any]:
    """Load all editable prompts with defaults and backward compatibility."""

    path = _prompts_path()
    legacy_path = _storage_path()
    defaults = {
        "ai_prompt": DEFAULT_AI_PROMPT_TEMPLATE,
        "pdf_page_ocr_prompt": DEFAULT_PDF_PAGE_OCR_PROMPT,
        "table_cell_ocr_prompt": DEFAULT_TABLE_CELL_OCR_PROMPT,
        "updated_at": None,
    }

    if not path.exists() and legacy_path.exists():
        path = legacy_path

    if not path.exists():
        return defaults

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return defaults

        out = {**defaults}

        # Back-compat keys
        stored_ai = None
        if isinstance(data.get("ai_prompt"), str):
            stored_ai = data.get("ai_prompt")
        elif isinstance(data.get("ai_prompt_template"), str):
            stored_ai = data.get("ai_prompt_template")

        if isinstance(stored_ai, str):
            # Back-compat: older deployments stored only a prefix prompt.
            if "{{USER_INPUT_JSON}}" in stored_ai or "{{PROJECT_ID}}" in stored_ai:
                out["ai_prompt"] = stored_ai
            else:
                out["ai_prompt"] = stored_ai.rstrip() + "\n\n" + DEFAULT_AI_PROMPT_TEMPLATE[len(DEFAULT_AI_PROMPT_PREFIX) :]

        if isinstance(data.get("pdf_page_ocr_prompt"), str):
            out["pdf_page_ocr_prompt"] = data["pdf_page_ocr_prompt"]
        if isinstance(data.get("table_cell_ocr_prompt"), str):
            out["table_cell_ocr_prompt"] = data["table_cell_ocr_prompt"]

        out["updated_at"] = data.get("updated_at")
        return out
    except Exception:
        return defaults


def save_prompts(
    *,
    ai_prompt: str | None = None,
    pdf_page_ocr_prompt: str | None = None,
    table_cell_ocr_prompt: str | None = None,
) -> dict[str, Any]:
    """Save one or more prompts; unspecified fields keep current values."""

    from datetime import datetime

    current = load_prompts()
    if ai_prompt is not None:
        current["ai_prompt"] = ai_prompt
    if pdf_page_ocr_prompt is not None:
        current["pdf_page_ocr_prompt"] = pdf_page_ocr_prompt
    if table_cell_ocr_prompt is not None:
        current["table_cell_ocr_prompt"] = table_cell_ocr_prompt

    current["updated_at"] = datetime.utcnow().isoformat() + "Z"

    path = _prompts_path()
    payload = {
        "ai_prompt": current["ai_prompt"],
        "pdf_page_ocr_prompt": current["pdf_page_ocr_prompt"],
        "table_cell_ocr_prompt": current["table_cell_ocr_prompt"],
        "updated_at": current["updated_at"],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load_prompt() -> dict[str, Any]:
    data = load_prompts()
    return {"ai_prompt": data["ai_prompt"], "updated_at": data.get("updated_at")}


def save_prompt(ai_prompt: str) -> dict[str, Any]:
    return save_prompts(ai_prompt=ai_prompt)
