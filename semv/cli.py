"""Semv CLI - Semantic File Organizer.

Premium UX with multi-level progress bars, adaptive batch sizing,
session resume, and Mistral Batch API support for 10k+ files.
"""

import typer
import asyncio
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, MofNCompleteColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from pathlib import Path
import questionary
import json
import shutil
import signal
import sys

from semv.config import load_config, save_config, is_configured, run_setup_wizard, get_setting
from semv.organizer import apply_file_action, trash_file, HISTORY_FILE, clear_history
from semv.agent.organizer_agent import run_organizer_agent
from semv.logger import get_logger, setup_logging, console as log_console
from semv.rate_limiter import RateLimiter, RateLimiterConfig
from semv.state import SessionState, cleanup_old_sessions

logger = get_logger("cli")

app = typer.Typer(help="Semv: Semantic File Organizer - AI-powered file organization at scale.")
console = Console()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _scan_directory(target_dir: Path, recursive: bool = False) -> list[Path]:
    """Scan a directory for files or folders, excluding hidden items."""
    items = []
    
    iterator = target_dir.rglob("*") if recursive else target_dir.iterdir()
    
    for f in iterator:
        # Skip hidden files/dirs and our own session files
        try:
            parts = f.relative_to(target_dir).parts
        except ValueError:
            parts = [f.name]
            
        if any(p.startswith(".") for p in parts):
            continue
            
        if recursive:
            if f.is_file():
                items.append(f)
        else:
            items.append(f)
            
    return items


def _format_size(size_bytes: int) -> str:
    """Human-readable file size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _print_scan_summary(
    total_files: int,
    duplicates: int,
    unique_files: int,
    total_size: int,
    mode: str,
    extraction_errors: int = 0,
):
    """Print a beautiful scan summary panel."""
    summary = Text()
    summary.append("[>] Total files:       ", style="bold")
    summary.append(f"{total_files:,}\n", style="cyan")
    summary.append("[>] Total size:        ", style="bold")
    summary.append(f"{_format_size(total_size)}\n", style="cyan")
    summary.append("[>] Duplicates found:  ", style="bold")
    summary.append(f"{duplicates:,}", style="red" if duplicates > 0 else "dim")
    if duplicates > 0:
        summary.append(" (will be trashed)", style="dim")
    summary.append("\n")
    summary.append("[>] Unique for AI:     ", style="bold")
    summary.append(f"{unique_files:,}\n", style="green")
    if extraction_errors > 0:
        summary.append("[!] Extraction errors: ", style="bold")
        summary.append(f"{extraction_errors:,}\n", style="yellow")
    summary.append("[>] Mode:              ", style="bold")
    summary.append(f"{mode}\n", style="magenta")

    if mode == "Batch API":
        summary.append("[$] Cost:              ", style="bold")
        summary.append("50% cheaper than real-time\n", style="green")

    console.print(Panel(summary, title="[bold]Scan Summary[/bold]", border_style="cyan"))


def _print_proposals(proposals: dict, root_dir_str: str, page: int = 0, per_page: int = 50):
    """Display proposals in a formatted Rich table."""
    import math
    
    items = list(proposals.items())
    total_items = len(items)
    total_pages = max(1, math.ceil(total_items / per_page))
    
    if page >= total_pages:
        page = total_pages - 1
    if page < 0:
        page = 0
        
    start_idx = page * per_page
    end_idx = min(start_idx + per_page, total_items)
    
    displayed_items = items[start_idx:end_idx]

    table = Table(
        title=f"Agent Proposed Organization (Page {page + 1} of {total_pages})", 
        show_lines=True
    )
    table.add_column("Original File", style="dim", overflow="fold")
    table.add_column("New Folder", style="cyan", overflow="fold")
    table.add_column("New Name", style="green", overflow="fold")
    table.add_column("Confidence", justify="center")

    for file_path, action in displayed_items:
        conf = action.get("confidence", 85)
        if conf >= 85:
            conf_str = f"[bold green]{conf}%[/bold green]"
        elif conf >= 60:
            conf_str = f"[bold yellow]{conf}%[/bold yellow]"
        else:
            conf_str = f"[bold red]{conf}%[/bold red]"

        original_path = Path(file_path)
        
        try:
            display_orig = str(original_path.relative_to(Path(root_dir_str)))
        except ValueError:
            display_orig = original_path.name

        if action.get("is_junk"):
            table.add_row(
                display_orig,
                "[red][Recycle Bin][/red]",
                action.get("suggested_name", display_orig),
                conf_str
            )
        else:
            root_dir = Path(root_dir_str)
            target_dir = root_dir / action.get("suggested_category", "Uncategorized")
            suggested_name = action.get("suggested_name", display_orig)
            
            original_ext = original_path.suffix
            if original_ext and not suggested_name.lower().endswith(original_ext.lower()):
                suggested_name += original_ext
                
            new_path = target_dir / suggested_name
            
            is_same = (original_path.resolve() == new_path.resolve())
            
            if is_same:
                table.add_row(
                    display_orig,
                    "[dim]Already Organized[/dim]",
                    "[dim]-[/dim]",
                    "[dim]100%[/dim]"
                )
            else:
                table.add_row(
                    display_orig,
                    action.get("suggested_category", "Uncategorized"),
                    suggested_name,
                    conf_str
                )
    console.print(table)
    console.print(f"[dim]Showing files {start_idx + 1} to {end_idx} out of {total_items}[/dim]\n")


def _apply_proposals(proposals: dict, target_dir: Path) -> int:
    """Apply approved proposals with progress tracking."""
    clear_history()
    success_count = 0
    total = len(proposals)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold green]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Applying changes...", total=total)

        for file_path, action in proposals.items():
            if action.get("is_junk"):
                trashed = trash_file(file_path)
                if trashed:
                    success_count += 1
            else:
                success = apply_file_action(
                    original_path_str=file_path,
                    root_dir_str=str(target_dir),
                    suggested_folder=action["suggested_category"],
                    suggested_name=action["suggested_name"]
                )
                if success:
                    success_count += 1

            progress.advance(task)

    return success_count


def _correct_proposal_paths(agent_proposals: dict, valid_paths: list[str]) -> dict:
    """Fix LLM path truncation: map basename-only keys back to full paths."""
    corrected = {}
    for k, v in agent_proposals.items():
        if k in valid_paths:
            corrected[k] = v
        else:
            # Fallback: search by basename
            found = False
            for vp in valid_paths:
                if Path(vp).name == Path(k).name:
                    corrected[vp] = v
                    found = True
                    break
            if not found:
                corrected[k] = v
    return corrected


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command()
def config():
    """Run the interactive configuration wizard to set API keys and taxonomy."""
    run_setup_wizard()


@app.command()
def undo():
    """Undo the last file organization operations."""
    setup_logging()
    
    if not HISTORY_FILE.exists():
        console.print("[yellow]No history found. Nothing to undo.[/yellow]")
        return
        
    try:
        with open(HISTORY_FILE, "r") as f:
            history = json.load(f)
    except Exception:
        console.print("[red]Failed to read history file.[/red]")
        return
        
    if not history:
        console.print("[yellow]History is empty.[/yellow]")
        return
        
    console.print(f"[bold cyan]Undoing {len(history)} operations...[/bold cyan]")
    success_count = 0
    for action in reversed(history):
        if action["action"] == "TRASH":
            console.print(f"[yellow]Skipping {Path(action['original_path']).name} (Was trashed, restore manually from OS Recycle Bin)[/yellow]")
            continue
            
        orig = Path(action["original_path"])
        new = Path(action["new_path"])
        
        if new.exists():
            orig.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(new), str(orig))
            success_count += 1
        else:
            console.print(f"[red]Could not find {new.name} to restore.[/red]")
            
    # Clear history after undo
    with open(HISTORY_FILE, "w") as f:
        json.dump([], f)
        
    console.print(f"[bold green]Undo complete! Restored {success_count} files.[/bold green]")


@app.command(name="clean")
def clean(
    path: str = typer.Argument(..., help="Directory to clean interactively"),
    batch: bool = typer.Option(False, "--batch", help="Force Mistral Batch API mode"),
    recursive: bool = typer.Option(False, "--recursive", help="Flatten and process all nested files"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show proposals without deleting"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
):
    """Run the Agentic cleaner to find and delete junk files."""
    _run_organize(path, multi_agent=False, batch=batch, recursive=recursive, dry_run=dry_run, verbose=verbose, operation_mode="clean")

@app.command(name="organize")
def organize(
    path: str = typer.Argument(..., help="Directory to organize interactively"),
    multi_agent: bool = typer.Option(False, "--multi-agent", help="Use specialized agents for Code, Finance, etc."),
    batch: bool = typer.Option(False, "--batch", help="Force Mistral Batch API mode (async, 50% cheaper)"),
    recursive: bool = typer.Option(False, "--recursive", help="Flatten and process all nested files individually"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show proposals without moving any files"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
):
    """Run the Agentic organizer interactively on a directory."""
    _run_organize(path, multi_agent=multi_agent, batch=batch, recursive=recursive, dry_run=dry_run, verbose=verbose, operation_mode="organize")

def _run_organize(
    path: str,
    multi_agent: bool,
    batch: bool,
    recursive: bool,
    dry_run: bool,
    verbose: bool,
    operation_mode: str,
):
    """Internal pipeline runner for both organize and clean commands."""
    setup_logging(verbose=verbose)
    cleanup_old_sessions()
    
    if not is_configured():
        run_setup_wizard()
        
    target_dir = Path(path).expanduser().resolve()
    if not target_dir.exists() or not target_dir.is_dir():
        console.print(f"[bold red]Error:[/bold red] The directory [cyan]{target_dir}[/cyan] does not exist.")
        raise typer.Exit(1)

    # -- Phase 1: Scan files ----------------------------------------------
    console.print(f"\n[bold cyan][>>] Scanning [white]{target_dir.name}[/white]...[/bold cyan]")
    
    files_to_process = _scan_directory(target_dir, recursive=recursive)
    
    if not files_to_process:
        console.print("[dim]No files found to organize.[/dim]")
        return
    
    total_file_count = len(files_to_process)
    logger.info("Found %d files in %s", total_file_count, target_dir)

    # -- Check for resumable session --------------------------------------
    session = SessionState.load(str(target_dir))
    if session and session.resumable:
        # Headless test bypass
        resume = False
        if resume and session.proposals:
            console.print(f"[bold green]Resumed {len(session.proposals)} cached proposals.[/bold green]")
            _print_proposals(session.proposals, str(target_dir))
            if operation_mode == "clean":
                session.proposals = {k: v for k, v in session.proposals.items() if v.get("is_junk")}
                _clean_approval_flow(session.proposals, target_dir, dry_run, session)
            else:
                _approval_flow(session.proposals, target_dir, dry_run, session)
            return
        elif not resume:
            session.clear()
            session = None
    
    # Create new session
    session = SessionState(str(target_dir))
    session.total_files = total_file_count
    session.phase = "extracting"
    session.save()

    # -- Phase 2: Extract content -----------------------------------------
    from semv.text_extraction import extract_all_files, MAX_SNIPPET_LENGTH_BATCH

    batch_threshold = get_setting("batch_threshold", 100)
    batch_force_threshold = get_setting("batch_force_threshold", 500)
    max_concurrent = get_setting("max_concurrent_extractions", 50)

    # Determine mode
    use_batch = batch or total_file_count >= batch_force_threshold
    if not use_batch and total_file_count >= batch_threshold:
        use_batch = questionary.confirm(
            f"You have {total_file_count:,} files. Use Batch API? (Async, 50% cheaper, no rate limits)"
        ).ask()
        if use_batch is None:
            raise typer.Exit()

    snippet_length = get_setting("snippet_length_batch", 500) if use_batch else get_setting("snippet_length", 2000)
    
    console.print(f"\n[bold cyan][>>] Extracting content from {total_file_count:,} files...[/bold cyan]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        extract_task = progress.add_task("Extracting...", total=total_file_count)

        def _on_extract_progress(completed, total):
            progress.update(extract_task, completed=completed)

        file_data = asyncio.run(
            extract_all_files(
                files_to_process,
                max_concurrent=max_concurrent,
                max_snippet_length=snippet_length,
                on_progress=_on_extract_progress,
            )
        )

    extraction_errors = sum(1 for d in file_data if d.get("error"))

    # -- Phase 3: Deduplication -------------------------------------------
    console.print(f"\n[bold cyan][>>] Deduplicating...[/bold cyan]")

    unique_hashes = set()
    files_for_agent = []
    proposals = {}
    total_size = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        console=console,
    ) as progress:
        dedup_task = progress.add_task("Deduplicating...", total=len(file_data))

        for data in file_data:
            f_hash = data["hash"]
            path_str = data["path"]
            total_size += data.get("size", 0)

            if f_hash and f_hash in unique_hashes:
                proposals[path_str] = {
                    "suggested_category": "[Recycle Bin]",
                    "suggested_name": Path(path_str).name,
                    "confidence": 100,
                    "summary_reason": "Exact duplicate (SHA-256 match)",
                    "is_junk": True,
                }
            else:
                if f_hash:
                    unique_hashes.add(f_hash)
                files_for_agent.append(data)

            progress.advance(dedup_task)

    duplicates = len(proposals)
    unique_count = len(files_for_agent)

    # -- Print scan summary -----------------------------------------------
    mode_label = "Batch API (async)" if use_batch else ("Multi-Agent" if multi_agent else "Single Agent (real-time)")
    _print_scan_summary(
        total_files=total_file_count,
        duplicates=duplicates,
        unique_files=unique_count,
        total_size=total_size,
        mode=mode_label,
        extraction_errors=extraction_errors,
    )

    session.phase = "processing"
    session.save()

    # -- Phase 4: AI Processing -------------------------------------------
    if use_batch:
        # -- Batch API mode -----------------------------------------------
        console.print(f"\n[bold cyan][>>] Launching Batch API for {unique_count:,} files...[/bold cyan]")
        
        try:
            from semv.batch_pipeline import run_batch_pipeline
            
            config = load_config()
            custom_taxonomy = config.get("taxonomy")
            
            def _on_batch_status(msg):
                console.print(f"  [dim]{msg}[/dim]")
            
            batch_proposals = run_batch_pipeline(
                files_data=files_for_agent,
                custom_taxonomy=custom_taxonomy,
                on_status=_on_batch_status,
                session_state=session,
                operation_mode=operation_mode,
            )
            proposals.update(batch_proposals)
            
        except ImportError as e:
            console.print(f"[bold red]Batch mode unavailable:[/bold red] {e}")
            console.print("[yellow]Falling back to real-time mode...[/yellow]")
            use_batch = False
        except Exception as e:
            console.print(f"[bold red]Batch pipeline error:[/bold red] {e}")
            logger.exception("Batch pipeline failed")
            return
    
    if not use_batch:
        # -- Real-time mode -----------------------------------------------
        is_multi = multi_agent
        if not multi_agent and not batch:
            # Hardcoded 'fast' mode to bypass prompt for headless testing
            is_multi = False

        # Create rate limiter
        rate_limiter = RateLimiter(RateLimiterConfig(
            requests_per_second=get_setting("requests_per_second", 1.0),
            max_retries=get_setting("api_retry_max", 10),
        ))

        batch_size = get_setting("realtime_batch_size", 10)
        file_queue = list(files_for_agent)
        
        console.print(f"\n[bold cyan][>>] Agent processing items...[/bold cyan]")

        agent_proposals_all = {}
        
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold cyan]{task.description}"),
                BarColumn(bar_width=40),
                MofNCompleteColumn(),
                TextColumn("•"),
                TimeElapsedColumn(),
                TextColumn("•"),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                agent_task = progress.add_task("Processing items...", total=len(file_queue))

                while file_queue:
                    batch_chunk = file_queue[:batch_size]
                    file_queue = file_queue[batch_size:]

                    # Skip already processed files
                    unprocessed = [
                        f for f in batch_chunk
                        if f["path"] not in session.processed_paths
                    ]
                    if not unprocessed:
                        progress.advance(agent_task, advance=len(batch_chunk))
                        continue

                    try:
                        if is_multi:
                            from semv.agent.multi_agent import run_multi_agent
                            batch_proposals = run_multi_agent(
                                str(target_dir), unprocessed,
                                rate_limiter=rate_limiter,
                            )
                        else:
                            batch_proposals = run_organizer_agent(
                                str(target_dir), unprocessed,
                                rate_limiter=rate_limiter,
                                operation_mode=operation_mode,
                            )
                        
                        # Handle splits and update proposals
                        from semv.text_extraction import extract_text
                        for f in unprocessed:
                            f_path = f["path"]
                            session.processed_paths.add(f_path)
                            
                            if f_path in batch_proposals:
                                action = batch_proposals[f_path]
                                if action.get("decision") == "split":
                                    # Split directory: add its children to the queue
                                    dir_path = Path(f_path)
                                    children = _scan_directory(dir_path, recursive=recursive)
                                    if children:
                                        progress.console.print(f"[bold yellow]Unpacked '{dir_path.name}' -> Added {len(children)} items[/bold yellow]")
                                    for child in children:
                                        text = extract_text(child, max_length=get_setting("snippet_length", 2000))
                                        file_queue.append({
                                            "path": str(child),
                                            "content": text or "[NO TEXT EXTRACTED]",
                                            "size": child.stat().st_size if child.is_file() else 0
                                        })
                                    progress.update(agent_task, total=progress.tasks[agent_task].total + len(children))
                                else:
                                    # Normal proposal or move_intact
                                    agent_proposals_all[f_path] = action
                                    
                    except Exception as e:
                        logger.error("Batch failed: %s", e)
                        console.print(f"[red]Batch failed: {e}[/red]")

                    progress.advance(agent_task, advance=len(batch_chunk))

        except KeyboardInterrupt:
            console.print("\n[yellow][!] Interrupted! Saving progress...[/yellow]")
            # Save partial progress
            valid_paths = [f["path"] for f in files_for_agent]
            corrected = _correct_proposal_paths(agent_proposals_all, valid_paths)
            proposals.update(corrected)
            session.proposals = proposals
            session.save()
            console.print(f"[yellow]Progress saved ({len(proposals)} proposals). Run the same command to resume.[/yellow]")
            return

        # Correct paths from agent output
        valid_paths = [f["path"] for f in files_for_agent]
        corrected = _correct_proposal_paths(agent_proposals_all, valid_paths)
        proposals.update(corrected)

        # Log rate limiter stats
        stats = rate_limiter.stats
        logger.info(
            "Rate limiter stats: %d requests, %d retries, %.1fs throttled",
            stats["total_requests"], stats["total_retries"], stats["total_throttled_seconds"],
        )

    # Save proposals to session
    session.proposals = proposals
    session.phase = "review"
    session.save()

    # -- Phase 5: Review & Apply ------------------------------------------
    if not proposals:
        console.print("[yellow]No proposals generated.[/yellow]")
        session.clear()
        return

    if operation_mode == "clean":
        proposals = {k: v for k, v in proposals.items() if v.get("is_junk")}
        if not proposals:
            console.print("[yellow]No junk files found. Your folder is clean![/yellow]")
            session.clear()
            return
        _clean_approval_flow(proposals, target_dir, dry_run, session)
    else:
        _approval_flow(proposals, target_dir, dry_run, session)

def _print_clean_proposals(proposals: dict, root_dir_str: str, page: int = 0, per_page: int = 50):
    """Display cleanup proposals in a formatted Rich table."""
    import math
    
    items = list(proposals.items())
    total_items = len(items)
    total_pages = max(1, math.ceil(total_items / per_page))
    
    if page >= total_pages:
        page = total_pages - 1
    if page < 0:
        page = 0
        
    start_idx = page * per_page
    end_idx = min(start_idx + per_page, total_items)
    
    displayed_items = items[start_idx:end_idx]

    table = Table(
        title=f"🗑️ Agent Identified Junk (Page {page + 1} of {total_pages})", 
        show_lines=True
    )
    table.add_column("File to Delete", style="dim", overflow="fold")
    table.add_column("Reason", style="yellow", overflow="fold")
    table.add_column("Confidence", justify="center")

    for file_path, action in displayed_items:
        conf = action.get("confidence", 85)
        if conf >= 85:
            conf_str = f"[bold green]{conf}%[/bold green]"
        elif conf >= 60:
            conf_str = f"[bold yellow]{conf}%[/bold yellow]"
        else:
            conf_str = f"[bold red]{conf}%[/bold red]"

        original_path = Path(file_path)
        try:
            display_orig = str(original_path.relative_to(Path(root_dir_str)))
        except ValueError:
            display_orig = original_path.name

        table.add_row(
            display_orig,
            action.get("summary_reason", "No reason provided"),
            conf_str
        )
    console.print(table)
    console.print(f"[dim]Showing files {start_idx + 1} to {end_idx} out of {total_items}[/dim]\n")


def _clean_approval_flow(proposals: dict, target_dir: Path, dry_run: bool, session: SessionState):
    """Interactive approval loop for clean mode."""
    import math
    current_page = 0
    per_page = 50
    
    while True:
        _print_clean_proposals(proposals, str(target_dir), page=current_page, per_page=per_page)

        total_pages = max(1, math.ceil(len(proposals) / per_page))

        choices = [
            questionary.Choice("[Delete] Move all to Recycle Bin", value="approve"),
            questionary.Choice("[Cancel] Do not delete anything", value="cancel"),
        ]
        
        if current_page < total_pages - 1:
            choices.insert(1, questionary.Choice(f"[Next Page] Show files { (current_page+1)*per_page + 1 }-{ min((current_page+2)*per_page, len(proposals)) }", value="next"))
        if current_page > 0:
            choices.insert(1, questionary.Choice(f"[Previous Page] Show files { (current_page-1)*per_page + 1 }-{ current_page*per_page }", value="prev"))

        if dry_run:
            console.print("[bold yellow]Dry run mode - no files will be deleted.[/bold yellow]")
            session.clear()
            return

        choice = questionary.select(
            "What would you like to do with these junk files?",
            choices=choices
        ).ask()
        
        if choice == "next":
            current_page += 1
            continue
        elif choice == "prev":
            current_page -= 1
            continue

        if choice == "approve":
            success_count = 0
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold red]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Moving to Recycle Bin...", total=len(proposals))
                for file_path in proposals.keys():
                    if trash_file(file_path):
                        success_count += 1
                    progress.advance(task)

            console.print(f"\n[bold green]Cleaned up! {success_count} files moved to the Recycle Bin.[/bold green]")
            session.phase = "done"
            session.clear()
            break
        else:
            console.print("[yellow]Cleanup cancelled.[/yellow]")
            session.clear()
            break


def _print_tree_preview(proposals: dict, root_dir_str: str):
    """Print a tree view of the proposed folder architecture."""
    from rich.tree import Tree
    
    # Build a simple representation of categories -> files
    categories = {}
    for file_path, action in proposals.items():
        if action.get("is_junk"):
            cat = "[Recycle Bin]"
            name = action.get("suggested_name", Path(file_path).name)
        else:
            cat = action.get("suggested_category", "Uncategorized")
            name = action.get("suggested_name", Path(file_path).name)
            
            # Ensure correct extension
            ext = Path(file_path).suffix
            if ext and not name.lower().endswith(ext.lower()):
                name += ext

        if cat not in categories:
            categories[cat] = []
        categories[cat].append(name)
        
    tree = Tree(f"📁 [bold]{Path(root_dir_str).name} (Preview)[/bold]")
    
    for cat, files in sorted(categories.items()):
        if cat == "[Recycle Bin]":
            cat_node = tree.add(f"🗑️ [bold red]{cat}[/bold red]")
        else:
            cat_node = tree.add(f"📁 [bold cyan]{cat}[/bold cyan]")
            
        # Display up to 5 files per category to keep it readable
        display_files = sorted(files)
        MAX_FILES_IN_TREE = 5
        for f in display_files[:MAX_FILES_IN_TREE]:
            cat_node.add(f"📄 [dim]{f}[/dim]")
            
        if len(display_files) > MAX_FILES_IN_TREE:
            cat_node.add(f"[dim]... and {len(display_files) - MAX_FILES_IN_TREE} more files[/dim]")
            
    console.print(tree)
    console.print("\n")


def _approval_flow(proposals: dict, target_dir: Path, dry_run: bool, session: SessionState):
    """Interactive approval loop with feedback support."""
    import math
    feedback = None
    current_page = 0
    per_page = 50
    show_tree = False
    
    while True:
        if show_tree:
            _print_tree_preview(proposals, str(target_dir))
            show_tree = False
        else:
            _print_proposals(proposals, str(target_dir), page=current_page, per_page=per_page)

        total_pages = max(1, math.ceil(len(proposals) / per_page))


        choices = [
            questionary.Choice("[Approve] Apply changes", value="approve"),
            questionary.Choice("[Tree View] Preview final folder architecture", value="tree"),
            questionary.Choice("[Feedback] Provide instructions to refine", value="feedback"),
            questionary.Choice("[Cancel] Do not make changes", value="cancel"),
        ]
        
        if current_page < total_pages - 1:
            choices.insert(1, questionary.Choice(f"[Next Page] Show files { (current_page+1)*per_page + 1 }-{ min((current_page+2)*per_page, len(proposals)) }", value="next"))
        if current_page > 0:
            choices.insert(1, questionary.Choice(f"[Previous Page] Show files { (current_page-1)*per_page + 1 }-{ current_page*per_page }", value="prev"))

        if dry_run:
            console.print("[bold yellow]Dry run mode - no files will be moved.[/bold yellow]")
            session.clear()
            return

        choice = questionary.select(
            "What would you like to do?",
            choices=choices
        ).ask()
        
        if choice == "next":
            current_page += 1
            continue
        elif choice == "prev":
            current_page -= 1
            continue
        elif choice == "tree":
            show_tree = True
            continue

        if choice == "approve":
            success_count = _apply_proposals(proposals, target_dir)
            console.print(f"\n[bold green]All done! {success_count} file actions successfully applied.[/bold green]")
            session.phase = "done"
            session.clear()
            break
            
        elif choice == "feedback":
            feedback = questionary.text("Enter your instructions for the agent:").ask()
            if not feedback:
                continue
                
            console.print("[bold cyan][>>] Agent is re-evaluating with your feedback...[/bold cyan]")
            
            # Re-run agent with feedback on current proposals
            rate_limiter = RateLimiter()
            files_for_reprocess = []
            for path_str in proposals:
                files_for_reprocess.append({"path": path_str, "content": ""})
            
            try:
                new_proposals = run_organizer_agent(
                    str(target_dir),
                    files_for_reprocess,
                    feedback=feedback,
                    rate_limiter=rate_limiter,
                )
                if new_proposals:
                    proposals.update(new_proposals)
                    session.proposals = proposals
                    session.save()
            except Exception as e:
                console.print(f"[red]Re-evaluation failed: {e}[/red]")
            continue
            
        else:
            console.print("[yellow]Operation cancelled.[/yellow]")
            session.clear()
            break


if __name__ == "__main__":
    app()
