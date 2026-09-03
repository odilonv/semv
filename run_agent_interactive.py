import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from semv.agent.organizer_agent import run_organizer_agent
from semv.config import load_config

config = load_config()
api_key = config.get("api_key") or os.environ.get("MISTRAL_API_KEY")

if not api_key:
    print("WARNING: MISTRAL_API_KEY is not set in environment or config. Please set it using 'semv scan' setup or export it.")
    sys.exit(1)

os.environ["MISTRAL_API_KEY"] = api_key

target_dir = Path("test_folder").absolute()
files = [str(f) for f in target_dir.rglob("*") if f.is_file()]

print(f"Testing agent on {len(files)} files in {target_dir}...\n")
try:
    proposals = run_organizer_agent(str(target_dir), files)
    print("\n=== Agent Proposals ===")
    for f, action in proposals.items():
        print(f"File: {Path(f).name}")
        print(f"  -> Action: Move to '{action['suggested_category']}' and rename to '{action['suggested_name']}'")
        print(f"  -> Confidence: {action.get('confidence', 85)}%")
        print(f"  -> Junk: {action['is_junk']}")
        print(f"  -> Reason: {action['summary_reason']}\n")
except Exception as e:
    print(f"Failed: {e}")
