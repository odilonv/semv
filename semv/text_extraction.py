"""Text and metadata extraction pipeline for semv.

Features:
- Bounded concurrency via asyncio.Semaphore (avoids 'too many open files')
- Per-file error isolation (one failure doesn't stop the pipeline)
- Progress callback support for Rich progress bars
- Configurable snippet length for memory optimization at scale
"""

import hashlib
import asyncio
from pathlib import Path
from typing import Callable

import pymupdf as fitz
fitz.TOOLS.mupdf_display_errors(False)
from PIL import Image
from PIL.ExifTags import TAGS

from semv.logger import get_logger

logger = get_logger("extraction")

# Default snippet length. Reduced at scale (10k+) to save memory and tokens.
MAX_SNIPPET_LENGTH = 2000
MAX_SNIPPET_LENGTH_BATCH = 500  # Used in batch mode for token efficiency

# Bounded concurrency to avoid exhausting file descriptors
DEFAULT_MAX_CONCURRENT = 50


def _get_file_hash(file_path: Path) -> str:
    """Returns the SHA-256 hash of the file content."""
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    except Exception as e:
        logger.debug("Hash failed for %s: %s", file_path.name, e)
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


def _extract_pdf_text(file_path: Path, max_length: int = MAX_SNIPPET_LENGTH) -> str:
    text = ""
    try:
        with fitz.open(file_path) as doc:
            for page in doc:
                text += page.get_text()
                if len(text) > max_length:
                    break
    except Exception as e:
        logger.debug("PDF extraction failed for %s: %s", file_path.name, e)
    return text[:max_length].strip()


def _extract_plain_text(file_path: Path, max_length: int = MAX_SNIPPET_LENGTH) -> str:
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(max_length).strip()
    except Exception as e:
        logger.debug("Text extraction failed for %s: %s", file_path.name, e)
        return ""


def extract_text(file_path: Path, max_length: int = MAX_SNIPPET_LENGTH) -> str:
    """Synchronous text extraction. Routes by file extension."""
    ext = file_path.suffix.lower()
    if ext in (".jpg", ".jpeg", ".png", ".heic", ".tiff", ".bmp", ".webp"):
        return _extract_image_exif(file_path)
    elif ext == ".pdf":
        return _extract_pdf_text(file_path, max_length)
    return _extract_plain_text(file_path, max_length)


async def analyze_file_async(
    file_path: Path,
    semaphore: asyncio.Semaphore | None = None,
    max_snippet_length: int = MAX_SNIPPET_LENGTH,
) -> dict:
    """Extract hash and content snippet from a file asynchronously.

    Args:
        file_path: Path to the file to analyze.
        semaphore: Optional semaphore to bound concurrency.
        max_snippet_length: Max chars to extract from content.

    Returns:
        Dict with keys: hash, content, path, size, error (if any).
    """
    async def _do_extract():
        try:
            f_hash = _get_file_hash(file_path)
            content = extract_text(file_path, max_snippet_length)
            size = file_path.stat().st_size if file_path.exists() else 0
            return {
                "hash": f_hash,
                "content": content,
                "path": str(file_path.absolute()),
                "size": size,
                "error": None,
            }
        except Exception as e:
            logger.warning("Extraction failed for %s: %s", file_path.name, e)
            return {
                "hash": "",
                "content": "",
                "path": str(file_path.absolute()),
                "size": 0,
                "error": str(e),
            }

    if semaphore:
        async with semaphore:
            return await asyncio.to_thread(lambda: asyncio.get_event_loop().run_until_complete(_do_extract()) if False else None) or await asyncio.to_thread(
                lambda: {
                    "hash": _get_file_hash(file_path),
                    "content": extract_text(file_path, max_snippet_length),
                    "path": str(file_path.absolute()),
                    "size": file_path.stat().st_size if file_path.exists() else 0,
                    "error": None,
                }
            )
    else:
        return await asyncio.to_thread(
            lambda: {
                "hash": _get_file_hash(file_path),
                "content": extract_text(file_path, max_snippet_length),
                "path": str(file_path.absolute()),
                "size": file_path.stat().st_size if file_path.exists() else 0,
                "error": None,
            }
        )


async def extract_all_files(
    file_paths: list[Path],
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    max_snippet_length: int = MAX_SNIPPET_LENGTH,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """Extract content from all files with bounded concurrency and progress.

    Args:
        file_paths: List of file paths to process.
        max_concurrent: Maximum concurrent file extractions.
        max_snippet_length: Max chars per snippet.
        on_progress: Callback(completed, total) for progress updates.

    Returns:
        List of extraction result dicts.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    results: list[dict] = []
    completed = 0
    total = len(file_paths)

    logger.info(
        "Starting extraction of %d files (concurrency=%d, snippet=%d chars)",
        total,
        max_concurrent,
        max_snippet_length,
    )

    async def _extract_with_progress(fp: Path):
        nonlocal completed
        try:
            result = await analyze_file_async(fp, semaphore, max_snippet_length)
        except Exception as e:
            logger.warning("Extraction error for %s: %s", fp.name, e)
            result = {
                "hash": "",
                "content": "",
                "path": str(fp.absolute()),
                "size": 0,
                "error": str(e),
            }
        completed += 1
        if on_progress:
            on_progress(completed, total)
        return result

    tasks = [_extract_with_progress(fp) for fp in file_paths]
    results = await asyncio.gather(*tasks)

    errors = sum(1 for r in results if r.get("error"))
    if errors:
        logger.warning("%d/%d files had extraction errors", errors, total)

    return list(results)
