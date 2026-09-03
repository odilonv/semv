import shutil
from pathlib import Path
from rich.console import Console
import send2trash
import json
from datetime import datetime

console = Console()
HISTORY_FILE = Path.home() / ".config" / "semv" / "history.json"

def _log_action(original_path: str, new_path: str, action: str):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r") as f:
                history = json.load(f)
        except Exception:
            pass
    
    history.append({
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "original_path": original_path,
        "new_path": new_path
    })
    
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

def trash_file(original_path_str: str) -> bool:
    """Moves a file safely to the operating system's Recycle Bin / Trash."""
    original_path = Path(original_path_str)
    if not original_path.exists():
        console.print(f"[red]Error: File {original_path.name} not found to send to trash.[/red]")
        return False
    try:
        send2trash.send2trash(str(original_path))
        _log_action(original_path_str, "TRASH", "TRASH")
        return True
    except Exception as e:
        console.print(f"[red]Failed to send {original_path.name} to trash: {e}[/red]")
        return False

def apply_file_action(original_path_str: str, root_dir_str: str, suggested_folder: str, suggested_name: str) -> bool:
    """Creates the category folder and physically moves/renames the file."""
    original_path = Path(original_path_str)
    root_dir = Path(root_dir_str)
    
    if not original_path.exists():
        console.print(f"[red]Error: File {original_path.name} disappeared before we could move it.[/red]")
        return False

    # Target directory inside scanned root
    target_dir = root_dir / suggested_folder
    
    try:
        # Create the category folder if it doesn't exist (e.g., "Invoices")
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # Ensure we keep the original file extension (e.g., .pdf)
        original_ext = original_path.suffix
        if original_ext and not suggested_name.lower().endswith(original_ext.lower()):
            suggested_name += original_ext
            
        new_path = target_dir / suggested_name
        
        if original_path.resolve() == new_path.resolve():
            return True
            
        # Physical move
        shutil.move(str(original_path), str(new_path))
        _log_action(original_path_str, str(new_path), "MOVE")
        return True
        
    except Exception as e:
        console.print(f"[red]Failed to move {original_path.name}: {e}[/red]")
        return False