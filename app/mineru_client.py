import os
import time
import requests
import logging

logger = logging.getLogger(__name__)

class MinerUClient:
    """Client for MinerU PDF parsing service.
    
    Docs: https://mineru.net/apiManage/docs
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("MINERU_API_KEY")
        self.base_url = "https://mineru.net/api/v4/extract/task"
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

        resp = requests.post(self.base_url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("code") != 0:
            raise RuntimeError(f"MinerU create task failed: {data.get('msg')}")
            
        task_id = data.get("data", {}).get("task_id")
        if not task_id:
            raise RuntimeError("MinerU did not return a task_id")
            
        return task_id

    def query_task(self, task_id: str) -> dict:
        """Query task status.
        
        Returns:
            dict with 'state', 'full_zip_url', etc.
        """
        if not self.api_key:
            raise RuntimeError("MinerU API Key not configured")

        url = f"https://mineru.net/api/v4/extract/task/{task_id}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("code") != 0:
            raise RuntimeError(f"MinerU query task failed: {data.get('msg')}")
            
        return data.get("data", {})

    def poll_task(self, task_id: str, timeout_s: int = 300, interval_s: int = 2) -> dict:
        """Poll task until done or failed."""
        start = time.time()
        while time.time() - start < timeout_s:
            info = self.query_task(task_id)
            state = info.get("state")
            if state == "done":
                return info
            if state == "failed":
                raise RuntimeError(f"MinerU task failed: {info.get('err_msg')}")
            time.sleep(interval_s)
        
        raise TimeoutError("MinerU task timed out")
