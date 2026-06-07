import typer
import asyncio
from rich.console import Console
from rich.progress import track
from pathlib import Path
from semv.database import init_db, add_pending_file
import questionary
from semv.config import load_config, save_config, is_configured

app = typer.Typer(help="Semv: Semantic File Organizer")
console = Console()

async def async_review():
    """Fetches ready files, displays a beautiful table, and applies actions."""
    ready_jobs = await get_jobs_by_status("REVIEW_READY")
    
    if not ready_jobs:
        console.print("[dim]No files waiting for review. Run 'semv scan' first.[/dim]")
        return

    table = Table(title="AI Organization Proposals", show_lines=True)
    table.add_column("Original File", style="dim", width=25)
    table.add_column("New Folder", style="cyan", width=15)
    table.add_column("New Name", style="green", width=30)
    table.add_column("AI Reasoning", style="yellow")

    for job in ready_jobs:
        original_name = Path(job["original_path"]).name
        table.add_row(
            original_name,
            job["ai_suggested_folder"],
            job["ai_suggested_name"],
            job["ai_reasoning"]
        )

    console.print(table)
    console.print("\n")

    apply = questionary.confirm(
        f"Do you want to apply these {len(ready_jobs)} changes?"
    ).ask()

    if not apply:
        console.print("[yellow]Changes aborted. Files remain in REVIEW_READY status.[/yellow]")
        return

    success_count = 0
    for job in track(ready_jobs, description="Moving and renaming files..."):
        
        success = apply_file_action(
            original_path_str=job["original_path"],
            root_dir_str=job["root_scanned_dir"], 
            suggested_folder=job["ai_suggested_folder"],
            suggested_name=job["ai_suggested_name"]
        )
        
        if success:
            await update_job_status(job["id"], "COMPLETED")
            success_count += 1
        else:
            await update_job_status(job["id"], "FAILED")

    console.print(f"[bold green]✨ All done! {success_count} files successfully organized.[/bold green]")

async def async_scan(path: str):
    """Asynchronous logic to scan a directory."""
    await init_db()
    
    target_dir = Path(path).expanduser()
    if not target_dir.exists() or not target_dir.is_dir():
        console.print(f"[bold red]Error:[/bold red] The directory {target_dir} does not exist.")
        raise typer.Exit(1)
    
    console.print(f"[bold green]Scanning directory:[/bold green] {target_dir}")
    
    # List all files (ignore hidden files/folders like .git)
    files_to_process = [f for f in target_dir.rglob("*") if f.is_file() and not f.name.startswith(".")]
    
    added_count = 0
    
    for file_path in track(files_to_process, description= "Indexing files..."):
        was_added = await add_pending_file(
            file_path=str(file_path.absolute()), 
            root_dir=str(target_dir.absolute())
        )
        
        if was_added:
            added_count += 1

    console.print(f"\n[bold blue]Done![/bold blue] {added_count} new files added to the pending queue (PENDING).")

def run_setup_wizard():
    """Interactive wizard to configure the AI provider."""
    console.print("\n[bold magenta]Welcome to semv![/bold magenta]")
    console.print("Let's configure your AI engine before we start.\n")

    # Interactive menu for choosing the engine
    mode = questionary.select(
        "Choose your inference engine:",
        choices=[
            questionary.Choice("☁️  Cloud (Mistral API - Fast, 0GB disk space)", value="cloud"),
            questionary.Choice("🔒 Local (Mistral 7B - Privacy First, ~4GB disk space)", value="local")
        ]
    ).ask()

    config_data = {"mode": mode}

    if mode == "cloud":
        api_key = questionary.password("Enter your Mistral API Key (sk-...):").ask()
        config_data["api_key"] = api_key
        console.print("[green]Cloud configuration saved![/green]")
    else:
        console.print("\n[bold yellow]Note:[/bold yellow] The Mistral model (~4GB) will be downloaded automatically on the first run.")
        console.print("[green]Local configuration saved![/green]")

    save_config(config_data)
    
    @app.command()
    def daemon():
        """Launch the background process."""
        console.print("[bold blue]Daemon active...[/bold blue]")

    @app.command()
    def review():
        """Review pending file actions and apply them."""
        asyncio.run(async_review())
    
    @app.command()
    def scan(path: str = typer.Option(..., help="Directory to scan")):
        """Force a scan of a specific directory."""
        if not is_configured():
            run_setup_wizard()
        asyncio.run(async_scan(path))
    
if __name__ == "__main__":
    app()


