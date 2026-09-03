import hashlib
import asyncio
from pathlib import Path

import pymupdf as fitz
fitz.TOOLS.mupdf_display_errors(False)
from PIL import Image
from PIL.ExifTags import TAGS

MAX_SNIPPET_LENGTH = 2000

def _get_file_hash(file_path: Path) -> str:
    """Returns the SHA-256 hash of the file content."""
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    except Exception:
        return ""

def _extract_image_exif(file_path: Path) -> str:
    try:
        with Image.open(file_path) as img:
            exif = img.getexif()
            if not exif:
                return f"[IMAGE FILE] Format: {img.format}, Size: {img.size}. No EXIF data."
            
            exif_data = {}
            for k, v in exif.items():
                tag = TAGS.get(k, k)
                if tag in ("DateTime", "Model", "Make", "Software"):
                    exif_data[tag] = str(v)
            
            info = f"[IMAGE FILE] Format: {img.format}, Size: {img.size}."
            if exif_data:
                info += f" Metadata: {exif_data}"
            return info
    except Exception:
        return "[IMAGE FILE] Unable to read metadata."

def _extract_pdf_text(file_path: Path) -> str:
    text = ""
    try:
        with fitz.open(file_path) as doc:
            for page in doc:
                text += page.get_text()
                if len(text) > MAX_SNIPPET_LENGTH:
                    break
    except Exception:
        pass
    return text[:MAX_SNIPPET_LENGTH].strip()

def _extract_plain_text(file_path: Path) -> str:
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(MAX_SNIPPET_LENGTH).strip()
    except Exception:
        return ""

def extract_text(file_path: Path) -> str:
    """Legacy sync fallback for worker.py"""
    ext = file_path.suffix.lower()
    if ext in (".jpg", ".jpeg", ".png", ".heic"):
        return _extract_image_exif(file_path)
    elif ext == ".pdf":
        return _extract_pdf_text(file_path)
    return _extract_plain_text(file_path)

async def analyze_file_async(file_path: Path) -> dict:
    """Reads a file asynchronously and returns its hash and content snippet."""
    def _read():
        f_hash = _get_file_hash(file_path)
        content = extract_text(file_path)
        return {"hash": f_hash, "content": content, "path": str(file_path.absolute())}

    return await asyncio.to_thread(_read)
