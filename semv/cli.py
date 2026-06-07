import typer
from rich.console import Console

app = typer.Typer(help="Semv: Semantic File Organizer")
console = Console()

@app.command()
def scan(path: str = typer.Option(..., help="Directory to scan")):
    """Force a scan of a specific directory."""
    console.print(f"[bold green] Scanning directory: [/bold green]{path}")
    
@app.command()
def daemon():
    """Launch the background process."""
    console.print("[bold blue]Daemon active...[/bold blue]")

@app.command()
def review():
    """Review pending file actions."""
    console.print("[bold yellow]Reviewing pending files...[/bold yellow]")
    
if __name__ == "__main__":
    app()
    

