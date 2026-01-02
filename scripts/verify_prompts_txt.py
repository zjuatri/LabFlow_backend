import sys
import os
from pathlib import Path

# Add backend directory to sys.path to import app modules
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

from app.prompt_store import load_prompts

def test_load():
    prompts = load_prompts()
    
    print(f"Updated At: {prompts.get('updated_at')}")
    
    ai_prompt = prompts.get("ai_prompt", "")
    print(f"AI Prompt Length: {len(ai_prompt)}")
    if '"width": "80%"' not in ai_prompt and '"align": "center"' not in ai_prompt:
         print("SUCCESS: 'width' and 'align' attributes are removed from AI Prompt image block.")
    else:
         print("WARNING: 'width' and 'align' attributes might still be present in AI Prompt.")

    pdf_prompt = prompts.get("pdf_page_ocr_prompt", "")
    print(f"PDF OCR Prompt Length: {len(pdf_prompt)}")
    
    table_prompt = prompts.get("table_cell_ocr_prompt", "")
    print(f"Table OCR Prompt Length: {len(table_prompt)}")
    
    if len(ai_prompt) > 100 and len(pdf_prompt) > 50:
        print("SUCCESS: Prompts loaded from text files.")
    else:
        print("ERROR: Prompts seem empty or too short.")

if __name__ == "__main__":
    test_load()
