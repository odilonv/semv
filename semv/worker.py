import asyncio
import fitz 
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from semv.database import get_jobs_by_status, update_job_with_ai, mark_job_as_failed
from semv.llm_provider import get_llm_provider

console = Console()

def extract_text_from_file(file_path: Path) -> str:
    """Reads the first 2000 characters of a file safely."""
    text_content = ""
    
    try:
        if file_path.suffix.lower() == ".pdf":
            with fitz.open(file_path) as doc:
                for page in doc:
                    text_content += page.get_text()
                    if len(text_content) > 2000:
                        break  # Stop early, we only need the beginning
                        
        else:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text_content = f.read(2000)
                
    except Exception as e:
        console.print(f"[red]Error reading {file_path.name}: {e}[/red]")
        return ""

    return text_content.strip()

async def run_worker():
    """Fetches PENDING jobs, extracts text, calls AI, and updates the database."""
    
    pending_jobs = await get_jobs_by_status("PENDING")
    
    if not pending_jobs:
        console.print("[dim]No pending files to process.[/dim]")
        return

    console.print(f"[bold cyan]Found {len(pending_jobs)} files to process. Booting AI...[/bold cyan]")
    
    try:
        llm = get_llm_provider()
    except Exception as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True, 
    ) as progress:
        
        task_id = progress.add_task("Analyzing files...", total=len(pending_jobs))
        
        for job in pending_jobs:
            file_path = Path(job["original_path"])
            job_id = job["id"]
            
            progress.update(task_id, description=f"Analyzing: [bold]{file_path.name}[/bold]")
            
            text_content = extract_text_from_file(file_path)
            
            if not text_content:
                await mark_job_as_failed(job_id)
                progress.advance(task_id)
                continue
                
            root_dir = Path(job["root_scanned_dir"])
            
            existing_folders = []
            if root_dir.exists():
                existing_folders = [
                    d.name for d in root_dir.iterdir() 
                    if d.is_dir() and not d.name.startswith(".")
                ]
            
            try:
                result = llm.analyze_text(
                    text_content=text_content, 
                    existing_folders=existing_folders
                )
                
                await update_job_with_ai(
                    job_id=job_id,
                    suggested_name=result.suggested_name,
                    suggested_folder=result.suggested_category,
                    reasoning=result.summary_reason
                )
                
            except Exception as e:
                console.print(f"\n[red]AI failed for {file_path.name}: {e}[/red]")
                await mark_job_as_failed(job_id)
                
            progress.advance(task_id)

    console.print("[bold green]✨ Processing complete! Type 'semv review' to see the results.[/bold green]")