from fastapi import APIRouter, File, UploadFile, HTTPException
from markitdown import MarkItDown
import tempfile
import os
import shutil

router = APIRouter()

@router.post("/convert/office-to-markdown")
async def convert_office_to_markdown(file: UploadFile = File(...)):
    """
    Convert uploaded Office file (PPTX, DOCX) to Markdown using MarkItDown.
    """
    
    # Check file extension
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ['.pptx', '.ppt', '.docx', '.doc']:
        raise HTTPException(status_code=400, detail="Unsupported file format. Please upload PPTX or DOCX.")

    # Save to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    converted_path = None
    
    try:
        # User Request: If .doc/.ppt, convert to .docx/.pptx first.
        # We attempt this using 'soffice' (LibreOffice) if available.
        if ext in ['.doc', '.ppt']:
            target_fmt = 'docx' if ext == '.doc' else 'pptx'
            
            # Check if soffice exists
            soffice = shutil.which('soffice') or shutil.which('libreoffice')
            # On Windows it might be full path, but we rely on PATH or standard install
            
            if not soffice:
                # Try standard Windows paths if on Windows
                if os.name == 'nt':
                    potential_paths = [
                        r"C:\Program Files\LibreOffice\program\soffice.exe",
                        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                        r"D:\software\libreoffice\program\soffice.exe"
                    ]
                    for p in potential_paths:
                        if os.path.exists(p):
                            soffice = p
                            break
            
            if soffice:
                import subprocess
                # Run headless conversion
                # soffice --headless --convert-to docx --outdir /tmp /tmp/file.doc
                out_dir = os.path.dirname(tmp_path)
                cmd = [
                    soffice, 
                    '--headless', 
                    '--convert-to', target_fmt, 
                    '--outdir', out_dir,
                    tmp_path
                ]
                
                print(f"Converting legacy file: {' '.join(cmd)}")
                subprocess.run(cmd, check=True, capture_output=True)
                
                # Resulting file should be at same path but with new extension
                new_filename = os.path.splitext(os.path.basename(tmp_path))[0] + '.' + target_fmt
                new_path = os.path.join(out_dir, new_filename)
                
                if os.path.exists(new_path):
                    converted_path = new_path
                    process_path = new_path
                else:
                    print("Conversion failed, output file not found. Trying original file.")
                    process_path = tmp_path
            else:
                 print("LibreOffice (soffice) not found. Attempting to read original file.")
                 process_path = tmp_path
        else:
            process_path = tmp_path

        # Run MarkItDown
        md = MarkItDown()
        result = md.convert(process_path)
        markdown_text = result.text_content
        
        return {"markdown": markdown_text}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Conversion failed: {str(e)}")
        
    finally:
        # Cleanup
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        if converted_path and os.path.exists(converted_path):
             os.remove(converted_path)
