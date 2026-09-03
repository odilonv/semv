import typer
import asyncio
from rich.console import Console
from rich.progress import track
from rich.table import Table
from pathlib import Path
import questionary
import json
import shutil
from semv.config import load_config, save_config, is_configured, run_setup_wizard
from semv.organizer import apply_file_action, trash_file, HISTORY_FILE, clear_history
from semv.agent.organizer_agent import run_organizer_agent
from semv.text_extraction import analyze_file_async

app = typer.Typer(help="Semv: Semantic File Organizer")
console = Console()


def _print_proposals(proposals: dict, root_dir_str: str):
    table = Table(title="Agent Proposed Organization", show_lines=True)
    table.add_column("Original File", style="dim", overflow="fold")
    table.add_column("New Folder", style="cyan", overflow="fold")
    table.add_column("New Name", style="green", overflow="fold")
    table.add_column("Confidence", justify="center")

    for file_path, action in proposals.items():
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

        if action["is_junk"]:
            table.add_row(
                display_orig,
                "[red][Recycle Bin][/red]",
                action["suggested_name"],
                conf_str
            )
        else:
            root_dir = Path(root_dir_str)
            target_dir = root_dir / action["suggested_category"]
            suggested_name = action["suggested_name"]
            
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
                    action["suggested_category"],
                    action["suggested_name"],
                    conf_str
                )
    console.print(table)
    console.print("\n")

@app.command()
def undo():
    """Undo the last file organization operations."""
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

@app.command()
def organize(
    path: str = typer.Argument(..., help="Directory to organize interactively"),
    multi_agent: bool = typer.Option(False, "--multi-agent", help="Use specialized agents for Code, Finance, etc.")
):
    """Run the Agentic organizer interactively on a directory."""
    if not is_configured():
        run_setup_wizard()
        
    target_dir = Path(path).expanduser()
    if not target_dir.exists() or not target_dir.is_dir():
        console.print(f"[bold red]Error:[/bold red] The directory {target_dir} does not exist.")
        raise typer.Exit(1)
        
    # Get files to process (avoid hidden files and directories)
    files_to_process = [str(f.absolute()) for f in target_dir.rglob("*") if f.is_file() and not f.name.startswith(".")]
    
    if not files_to_process:
        console.print("[dim]No files found to organize.[/dim]")
        return
        
    # Interactive mode selection
    if multi_agent:
        mode = "expert"
    else:
        mode = questionary.select(
            "Choose your organization engine:",
            choices=[
                questionary.Choice("Fast Mode (Single General Agent)", "fast"),
                questionary.Choice("Expert Mode (Multi-Agent Team for Code, Finance, etc.)", "expert")
            ]
        ).ask()
        
        # Handle ctrl+c
        if not mode:
            raise typer.Exit()
            
    is_multi = (mode == "expert")

    console.print(f"\n[bold cyan]Analyzing {len(files_to_process)} files in parallel...[/bold cyan]")
    
    # Run text/EXIF extraction and hashing concurrently
    async def extract_all():
        tasks = [analyze_file_async(Path(f)) for f in files_to_process]
        return await asyncio.gather(*tasks)
        
    file_data = asyncio.run(extract_all())
    
    # Deduplication and prep for agent
    unique_hashes = set()
    files_for_agent = []
    proposals = {}
    
    for data in file_data:
        f_hash = data["hash"]
        path_str = data["path"]
        
        if f_hash and f_hash in unique_hashes:
            proposals[path_str] = {
                "suggested_category": "[Recycle Bin]",
                "suggested_name": Path(path_str).name,
                "confidence": 100,
                "summary_reason": "Exact duplicate (SHA-256 match)",
                "is_junk": True
            }
        else:
            if f_hash:
                unique_hashes.add(f_hash)
            files_for_agent.append(data)
            
    console.print(f"[bold cyan]Agent is deciding organization for {len(files_for_agent)} unique files...[/bold cyan]")
    
    feedback = None
    while True:
        try:
            if is_multi:
                from semv.agent.multi_agent import run_multi_agent
                agent_proposals = run_multi_agent(str(target_dir), files_for_agent, feedback=feedback)
            else:
                agent_proposals = run_organizer_agent(str(target_dir), files_for_agent, feedback=feedback)
            
            # Defense in depth: The LLM sometimes truncates the absolute path to just the basename.
            # We need to map it back to the true absolute path.
            valid_paths = [f["path"] for f in files_for_agent]
            corrected_proposals = {}
            for k, v in agent_proposals.items():
                if k in valid_paths:
                    corrected_proposals[k] = v
                else:
                    # Fallback: search by basename
                    found = False
                    for vp in valid_paths:
                        if Path(vp).name == Path(k).name:
                            corrected_proposals[vp] = v
                            found = True
                            break
                    if not found:
                        corrected_proposals[k] = v
                        
            proposals.update(corrected_proposals)
        except Exception as e:
            console.print(f"[bold red]Agent failed:[/bold red] {e}")
            return
            
        if not proposals:
            console.print("[yellow]Agent returned no proposals.[/yellow]")
            return
            
        _print_proposals(proposals, str(target_dir))
        
        choice = questionary.select(
            "What would you like to do?",
            choices=[
                questionary.Choice("[Approve] Apply changes", value="approve"),
                questionary.Choice("[Feedback] Provide instructions to refine", value="feedback"),
                questionary.Choice("[Cancel] Do not make changes", value="cancel")
            ]
        ).ask()
        
        if choice == "approve":
            clear_history()
            success_count = 0
            for file_path, action in proposals.items():
                if action["is_junk"]:
                    # Safely move to OS Recycle Bin / Trash
                    trashed = trash_file(file_path)
                    if trashed:
                        console.print(f"[magenta]Sent to Recycle Bin:[/magenta] {Path(file_path).name}")
                        success_count += 1
                    continue
                
                success = apply_file_action(
                    original_path_str=file_path,
                    root_dir_str=str(target_dir),
                    suggested_folder=action["suggested_category"],
                    suggested_name=action["suggested_name"]
                )
                if success:
                    success_count += 1
            console.print(f"[bold green]All done! {success_count} file actions successfully applied.[/bold green]")
            break
            
        elif choice == "feedback":
            feedback = questionary.text("Enter your instructions for the agent:").ask()
            console.print("[bold cyan]Agent is re-evaluating with your feedback...[/bold cyan]")
            continue
            
        else:
            console.print("[yellow]Operation cancelled.[/yellow]")
            break

if __name__ == "__main__":
    app()


