import json
from pathlib import Path

import questionary
from rich.console import Console

CONFIG_DIR = Path.home() / ".config" / "semv"
CONFIG_FILE = CONFIG_DIR / "config.json"

_console = Console()


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def save_config(config_data: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config_data, f, indent=4)


def is_configured() -> bool:
    return "mode" in load_config()


def run_setup_wizard():
    """Interactive wizard to configure the AI provider."""
    _console.print("\n[bold magenta]Welcome to semv![/bold magenta]")
    _console.print("Let's configure your AI engine before we start.\n")

    mode = questionary.select(
        "Choose your inference engine:",
        choices=[
            questionary.Choice("Cloud (Mistral API - Fast, 0GB disk space)", value="cloud"),
            questionary.Choice("Local (Mistral 7B - Privacy First, ~4GB disk space)", value="local"),
        ],
    ).ask()

    config_data = {"mode": mode}

    if mode == "cloud":
        api_key = questionary.password("Enter your Mistral API Key:").ask()
        config_data["api_key"] = api_key
        _console.print("[green]Cloud configuration saved![/green]")
    else:
        _console.print("\n[bold yellow]Note:[/bold yellow] The Mistral model (~4GB) will be downloaded automatically on the first run.")
        _console.print("[green]Local configuration saved![/green]")

    wants_custom = questionary.confirm("Do you want to define a custom folder taxonomy? (Default: Work, Personal, Finance, Code, Media, Archives)").ask()
    if wants_custom:
        custom_tax = questionary.text("Enter your root folders (comma separated, e.g. Work, School, Hobbies):").ask()
        if custom_tax:
            config_data["taxonomy"] = [t.strip() for t in custom_tax.split(",")]
    
    save_config(config_data)