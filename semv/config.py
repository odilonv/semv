import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "semv"
CONFIG_FILE = CONFIG_DIR / "config.json"

def load_config() -> dict:
    """Loads the configuration file if it exists, otherwise returns an empty dict."""
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(config_data: dict):
    """Saves the configuration dictionary to the JSON file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config_data, f, indent=4)

def is_configured() -> bool:
    """Checks if the user has already completed the setup."""
    config = load_config()
    return "mode" in config