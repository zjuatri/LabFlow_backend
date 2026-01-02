import json
import re

def extract_json_object(text: str) -> dict:
    """Best-effort extraction of a JSON object from model text.

    Important:
      - Models sometimes emit LaTeX with single backslashes (e.g. "\beta").
        In JSON, sequences like \b and \t are valid escapes (backspace/tab),
        which corrupts LaTeX when decoded. We defensively double backslashes
        inside JSON string literals before parsing.
    """

    text = (text or "").strip()
    if not text:
        raise ValueError("empty content")

    # Strip common markdown code fences
    if text.startswith("```"):
        # Remove leading ```lang and trailing ``` if present
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text).strip()

    def _fix_backslashes_inside_json_strings(s: str) -> str:
        # Replace backslashes inside JSON string literals by doubling them.
        # This is conservative: it only touches content within quotes.
        def repl(m: re.Match[str]) -> str:
            inner = m.group(1)
            return '"' + inner.replace("\\", "\\\\") + '"'

        return re.sub(r'"((?:[^"\\]|\\.)*)"', repl, s)

    # Direct parse attempt
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list) and obj and isinstance(obj[0], dict):
            return obj[0]
    except Exception:
        pass

    # Try to decode starting from the first '{' (allows trailing junk text)
    start = text.find("{")
    if start != -1:
        decoder = json.JSONDecoder()
        for candidate in (_fix_backslashes_inside_json_strings(text[start:]), text[start:]):
            try:
                obj, _idx = decoder.raw_decode(candidate)
                if isinstance(obj, dict):
                    return obj
                if isinstance(obj, list) and obj and isinstance(obj[0], dict):
                    return obj[0]
            except Exception:
                continue

    # Fallback: try last {...} span
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = text[start : end + 1]
        for candidate in (_fix_backslashes_inside_json_strings(snippet), snippet):
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    return obj
                if isinstance(obj, list) and obj and isinstance(obj[0], dict):
                    return obj[0]
            except Exception:
                continue

    raise ValueError("unable to parse JSON object")
