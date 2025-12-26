from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


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


def load_prompt() -> dict[str, Any]:
    path = _storage_path()
    if not path.exists():
        return {"ai_prompt": DEFAULT_AI_PROMPT_TEMPLATE, "updated_at": None}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            if isinstance(data.get("ai_prompt"), str):
                # Back-compat: older deployments stored only a prefix prompt.
                stored = data["ai_prompt"]
                if "{{USER_INPUT_JSON}}" in stored or "{{PROJECT_ID}}" in stored:
                    return {"ai_prompt": stored, "updated_at": data.get("updated_at")}
                return {
                    "ai_prompt": stored.rstrip() + "\n\n" + DEFAULT_AI_PROMPT_TEMPLATE[len(DEFAULT_AI_PROMPT_PREFIX):],
                    "updated_at": data.get("updated_at"),
                }
            if isinstance(data.get("ai_prompt_template"), str):
                return {"ai_prompt": data["ai_prompt_template"], "updated_at": data.get("updated_at")}
    except Exception:
        pass

    return {"ai_prompt": DEFAULT_AI_PROMPT_TEMPLATE, "updated_at": None}


def save_prompt(ai_prompt: str) -> dict[str, Any]:
    from datetime import datetime

    path = _storage_path()
    payload = {"ai_prompt": ai_prompt, "updated_at": datetime.utcnow().isoformat() + "Z"}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
