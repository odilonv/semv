"""Mistral Batch API pipeline for processing 10k+ files.

Uses the Mistral SDK (not LangChain) to:
1. Generate JSONL requests from extracted file data
2. Upload JSONL to Mistral Files API
3. Create a batch job
4. Poll for completion with Rich progress bar
5. Parse results into proposal dicts

Batch API benefits:
- 50% cheaper than real-time
- Does NOT count against rate limits (RPS/TPM)
- Up to 100k requests per batch
"""

import json
import tempfile
import time
from pathlib import Path
from typing import Callable

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

from semv.logger import get_logger, console
from semv.config import load_config

logger = get_logger("batch_pipeline")

# Max requests per Mistral batch job
MAX_BATCH_SIZE = 100_000
POLL_INTERVAL_SECONDS = 10
MAX_POLL_DURATION_SECONDS = 3600  # 1 hour timeout


def _build_system_prompt(custom_taxonomy: list[str] | None = None) -> str:
    """Build the system prompt for batch classification."""
    if custom_taxonomy:
        tax_str = ", ".join(f"'{t}'" for t in custom_taxonomy)
        taxonomy_block = f"Use these ROOT folders: {tax_str}"
    else:
        taxonomy_block = (
            "Use these ROOT folders: 'Work', 'Personal', 'Finance', 'Code', 'Media', 'Archives'. "
            "Create subfolders for precision (e.g., 'Code/Python', 'Finance/Invoices')."
        )

    return f"""\
You are a file organizer. For each file, respond with ONLY a JSON object (no markdown, no explanation):
{{"suggested_category": "Root/Sub", "suggested_name": "descriptive_snake_case_name", "summary_reason": "short reason", "is_junk": false, "confidence": 85}}

Rules:
- {taxonomy_block}
- snake_case names, keep original extension.
- is_junk=true for installers, temp files, caches.
- confidence: 95-100 obvious, 70-85 guesses, <60 ambiguous.
"""


def _build_user_message(file_data: dict) -> str:
    """Build the user message for a single file classification request."""
    content = file_data.get("content", "")
    if len(content) > 500:
        content = content[:500] + "..."
    return f"Classify this file:\nPath: {file_data['path']}\nContent:\n---\n{content}\n---"


def generate_batch_jsonl(
    files_data: list[dict],
    output_path: Path,
    custom_taxonomy: list[str] | None = None,
) -> int:
    """Generate a JSONL file for Mistral Batch API.

    Each line: {"custom_id": "<file_path>", "body": {"model": "...", "messages": [...]}}

    Returns the number of requests written.
    """
    system_prompt = _build_system_prompt(custom_taxonomy)
    count = 0

    with open(output_path, "w", encoding="utf-8") as f:
        for file_data in files_data:
            request = {
                "custom_id": file_data["path"],
                "body": {
                    "model": "mistral-small-latest",
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": _build_user_message(file_data)},
                    ],
                },
            }
            f.write(json.dumps(request, ensure_ascii=False) + "\n")
            count += 1

    logger.info("Generated %d batch requests in %s", count, output_path.name)
    return count


def _get_mistral_client():
    """Create a Mistral SDK client."""
    import os

    try:
        from mistralai import Mistral
    except ImportError:
        raise ImportError(
            "The 'mistralai' package is required for batch mode. "
            "Install it with: pip install mistralai"
        )

    config = load_config()
    api_key = config.get("api_key") or os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        raise ValueError("No Mistral API key found. Run 'semv organize' to configure.")

    return Mistral(api_key=api_key)


def upload_and_create_batch(
    jsonl_path: Path,
    on_status: Callable[[str], None] | None = None,
) -> str:
    """Upload JSONL file and create a Mistral batch job.

    Args:
        jsonl_path: Path to the JSONL file to upload.
        on_status: Optional callback for status updates.

    Returns:
        The batch job ID.
    """
    client = _get_mistral_client()

    # Upload file
    if on_status:
        on_status("Uploading batch file to Mistral...")
    logger.info("Uploading %s to Mistral Files API...", jsonl_path.name)

    with open(jsonl_path, "rb") as f:
        uploaded_file = client.files.upload(
            file={
                "file_name": jsonl_path.name,
                "content": f,
            },
            purpose="batch",
        )

    file_id = uploaded_file.id
    logger.info("File uploaded: %s (ID: %s)", jsonl_path.name, file_id)

    # Create batch job
    if on_status:
        on_status("Creating batch job...")

    job = client.batch.jobs.create(
        input_files=[file_id],
        endpoint="/v1/chat/completions",
        model="mistral-small-latest",
    )

    logger.info("Batch job created: %s", job.id)
    return job.id


def poll_batch_job(
    job_id: str,
    on_progress: Callable[[str, float], None] | None = None,
    timeout: int = MAX_POLL_DURATION_SECONDS,
) -> dict:
    """Poll a batch job until completion.

    Args:
        job_id: The batch job ID to poll.
        on_progress: Callback(status, progress_pct) for progress updates.
        timeout: Maximum seconds to wait.

    Returns:
        The completed job object as a dict.

    Raises:
        TimeoutError: If the job doesn't complete within timeout.
        RuntimeError: If the job fails.
    """
    client = _get_mistral_client()
    start_time = time.monotonic()

    while True:
        elapsed = time.monotonic() - start_time
        if elapsed > timeout:
            raise TimeoutError(
                f"Batch job {job_id} did not complete within {timeout}s"
            )

        job = client.batch.jobs.get(job_id=job_id)
        status = job.status

        # Calculate progress percentage
        total = getattr(job, "total_requests", 0) or 0
        completed = getattr(job, "completed_requests", 0) or 0
        failed = getattr(job, "failed_requests", 0) or 0
        progress = (completed + failed) / max(total, 1)

        if on_progress:
            on_progress(status, progress)

        logger.debug(
            "Batch %s: status=%s, progress=%.0f%% (%d/%d, %d failed)",
            job_id[:12],
            status,
            progress * 100,
            completed,
            total,
            failed,
        )

        if status == "SUCCESS":
            logger.info("Batch job %s completed successfully", job_id[:12])
            return job.__dict__ if hasattr(job, "__dict__") else {"status": status, "output_file": getattr(job, "output_file", None)}

        if status in ("FAILED", "CANCELLED", "EXPIRED"):
            error_msg = getattr(job, "error", "Unknown error")
            raise RuntimeError(f"Batch job {status}: {error_msg}")

        time.sleep(POLL_INTERVAL_SECONDS)


def download_and_parse_results(job_id: str) -> dict[str, dict]:
    """Download batch results and parse into proposals dict.

    Returns:
        Dict mapping file_path -> proposal dict.
    """
    client = _get_mistral_client()

    # Get the job to find output file
    job = client.batch.jobs.get(job_id=job_id)
    output_file_id = getattr(job, "output_file", None)

    if not output_file_id:
        logger.error("No output file found for batch job %s", job_id[:12])
        return {}

    # Download the output file
    logger.info("Downloading batch results...")
    result_content = client.files.download(file_id=output_file_id)

    # Parse the JSONL output
    proposals = {}
    errors = 0

    # result_content may be bytes or a response object
    if isinstance(result_content, bytes):
        lines = result_content.decode("utf-8").strip().split("\n")
    else:
        lines = result_content.text.strip().split("\n")

    for line in lines:
        if not line.strip():
            continue
        try:
            result = json.loads(line)
            custom_id = result.get("custom_id", "")
            response = result.get("response", {})

            # Extract the assistant's JSON response
            if response.get("status_code") == 200:
                body = response.get("body", {})
                choices = body.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                    try:
                        proposal = json.loads(content)
                        proposals[custom_id] = {
                            "suggested_category": proposal.get("suggested_category", "Unsorted"),
                            "suggested_name": proposal.get("suggested_name", Path(custom_id).stem),
                            "summary_reason": proposal.get("summary_reason", "Batch processed"),
                            "is_junk": proposal.get("is_junk", False),
                            "confidence": proposal.get("confidence", 75),
                        }
                    except json.JSONDecodeError:
                        logger.warning("Failed to parse JSON response for %s", custom_id)
                        errors += 1
            else:
                logger.warning(
                    "Batch request failed for %s: status=%s",
                    custom_id,
                    response.get("status_code"),
                )
                errors += 1

        except json.JSONDecodeError:
            logger.warning("Failed to parse batch result line")
            errors += 1

    logger.info(
        "Parsed %d proposals from batch results (%d errors)",
        len(proposals),
        errors,
    )
    return proposals


def run_batch_pipeline(
    files_data: list[dict],
    custom_taxonomy: list[str] | None = None,
    on_status: Callable[[str], None] | None = None,
    session_state=None,
) -> dict[str, dict]:
    """Run the complete batch pipeline.

    Args:
        files_data: List of dicts with 'path' and 'content' keys.
        custom_taxonomy: Optional custom root folder names.
        on_status: Callback for status messages.
        session_state: Optional SessionState for save/resume.

    Returns:
        Dict mapping file_path -> proposal dict.
    """
    from semv.state import SessionState

    # Check for resumable batch job
    if session_state and session_state.has_pending_batch:
        logger.info("Resuming batch job %s...", session_state.batch_job_id)
        if on_status:
            on_status(f"Resuming batch job {session_state.batch_job_id[:12]}...")

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Batch processing...", total=100)

            def _on_progress(status, pct):
                progress.update(task, completed=pct * 100, description=f"Batch: {status}")

            poll_batch_job(session_state.batch_job_id, on_progress=_on_progress)

        proposals = download_and_parse_results(session_state.batch_job_id)
        if session_state:
            session_state.batch_status = "SUCCESS"
            session_state.proposals = proposals
            session_state.save()
        return proposals

    # Chunk files if needed (max 100k per batch)
    all_proposals = {}
    chunks = [
        files_data[i : i + MAX_BATCH_SIZE]
        for i in range(0, len(files_data), MAX_BATCH_SIZE)
    ]

    for chunk_idx, chunk in enumerate(chunks):
        chunk_label = f" (chunk {chunk_idx + 1}/{len(chunks)})" if len(chunks) > 1 else ""

        # Generate JSONL
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, dir=None
        ) as tmp:
            jsonl_path = Path(tmp.name)

        generate_batch_jsonl(chunk, jsonl_path, custom_taxonomy)

        # Upload and create job
        if on_status:
            on_status(f"Uploading batch{chunk_label}...")
        job_id = upload_and_create_batch(jsonl_path, on_status)

        if session_state:
            session_state.batch_job_id = job_id
            session_state.batch_status = "QUEUED"
            session_state.save()

        # Cleanup temp file
        jsonl_path.unlink(missing_ok=True)

        # Poll with progress bar
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"Batch processing{chunk_label}...", total=100)

            def _on_progress(status, pct):
                progress.update(task, completed=pct * 100, description=f"Batch: {status}")

            poll_batch_job(job_id, on_progress=_on_progress)

        # Download and parse
        chunk_proposals = download_and_parse_results(job_id)
        all_proposals.update(chunk_proposals)

        if session_state:
            session_state.batch_status = "SUCCESS"
            session_state.proposals.update(chunk_proposals)
            session_state.save()

    return all_proposals
