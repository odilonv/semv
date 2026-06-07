import shutil
from pathlib import Path
from rich.console import Console

console = Console()

def apply_file_action(original_path_str: str, root_dir_str: str, suggested_folder: str, suggested_name: str) -> bool:
    """Creates the category folder and physically moves/renames the file."""
    original_path = Path(original_path_str)
    root_dir = Path(root_dir_str)
    
    if not original_path.exists():
        console.print(f"[red]Error: File {original_path.name} disappeared before we could move it.[/red]")
        return False

    # We organize files in the same parent directory where they were found
    base_dir = original_path.parent
    target_dir = root_dir / suggested_folder
    
    try:
        # Create the category folder if it doesn't exist (e.g., "Invoices")
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # Ensure we keep the original file extension (e.g., .pdf)
        original_ext = original_path.suffix
        if not suggested_name.endswith(original_ext):
            suggested_name += original_ext
            
        new_path = target_dir / suggested_name
        
        # Physical move
        shutil.move(str(original_path), str(new_path))
        return True
        
    except Exception as e:
        console.print(f"[red]Failed to move {original_path.name}: {e}[/red]")
        return False