import os
import time
import requests
import logging

logger = logging.getLogger(__name__)

class MinerUClient:
    """Client for MinerU PDF parsing service.
    
    Docs: https://mineru.net/apiManage/docs
    
    Supports both official API (mineru.net) and 302.ai proxy.
    Set MINERU_API_BASE env var to switch endpoints.
    """

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.getenv("MINERU_API_KEY")
        # Support 302.ai proxy or official API
        default_base = "https://mineru.net/api/v4"
        self.base_url = base_url or os.getenv("MINERU_API_BASE") or default_base
        if not self.api_key:
            logger.warning("MINERU_API_KEY not found in environment")

    def create_task(self, pdf_url: str, is_ocr: bool = True, model_version: str = "v2", page_ranges: str | None = None) -> str:
        """Create a parsing task.
        
        Args:
            pdf_url: Publicly accessible URL of the PDF.
            is_ocr: Enable OCR (default True).
            model_version: 'v1' or 'v2' (default 'v2').
            page_ranges: Optional page range string, e.g., "2,4-6" or "1-10".
            
        Returns:
            task_id (str)
        """
        if not self.api_key:
            raise RuntimeError("MinerU API Key not configured")

        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "url": pdf_url,
            "is_ocr": is_ocr,
            "enable_formula": True,
            "enable_table": True,
            "language": "auto",
            "model_version": model_version
        }
        
        if page_ranges:
            payload["page_ranges"] = page_ranges

        resp = requests.post(f"{self.base_url}/extract/task", json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("code") != 0:
            raise RuntimeError(f"MinerU create task failed: {data.get('msg')}")
            
        task_id = data.get("data", {}).get("task_id")
        if not task_id:
            raise RuntimeError("MinerU did not return a task_id")
            
        return task_id

    def query_task(self, task_id: str, max_retries: int = 3) -> dict:
        """Query task status.
        
        Returns:
            dict with 'state', 'full_zip_url', etc.
        """
        if not self.api_key:
            raise RuntimeError("MinerU API Key not configured")

        url = f"{self.base_url}/extract/task/{task_id}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        
        last_error = None
        for attempt in range(max_retries):
            try:
                resp = requests.get(url, headers=headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                
                if data.get("code") != 0:
                    raise RuntimeError(f"MinerU query task failed: {data.get('msg')}")
                    
                return data.get("data", {})
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                last_error = e
                logger.warning(f"MinerU query_task attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                continue
        
        raise RuntimeError(f"MinerU query_task failed after {max_retries} retries: {last_error}")

    def poll_task(self, task_id: str, timeout_s: int = 300, interval_s: int = 3) -> dict:
        """Poll task until done or failed."""
        start = time.time()
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        while time.time() - start < timeout_s:
            try:
                info = self.query_task(task_id)
                consecutive_errors = 0  # Reset on success
                state = info.get("state")
                if state == "done":
                    return info
                if state == "failed":
                    raise RuntimeError(f"MinerU task failed: {info.get('err_msg')}")
                time.sleep(interval_s)
            except RuntimeError as e:
                if "failed after" in str(e):
                    consecutive_errors += 1
                    logger.warning(f"Poll error ({consecutive_errors}/{max_consecutive_errors}): {e}")
                    if consecutive_errors >= max_consecutive_errors:
                        raise
                    time.sleep(interval_s * 2)
                else:
                    raise
        
        raise TimeoutError("MinerU task timed out")

