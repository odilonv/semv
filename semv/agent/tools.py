from pathlib import Path

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from semv.text_extraction import extract_text


@tool
def list_directory_tool(path: str) -> str:
    """Lists the contents of a directory.
    Useful to understand what folders already exist to reuse them for categorization.
    """
    target = Path(path).expanduser()
    if not target.exists() or not target.is_dir():
        return f"Error: Directory {path} does not exist."

    contents = []
    for item in target.iterdir():
        if item.name.startswith("."):
            continue
        prefix = "DIR" if item.is_dir() else "FILE"
        contents.append(f"[{prefix}] {item.name}")

    return "\n".join(contents) if contents else f"Directory {path} is empty."


@tool
def read_file_snippet_tool(file_path: str) -> str:
    """Reads a text snippet (up to 2000 chars) from a file (text or PDF).
    Use this to understand the content of a file before categorizing it.
    """
    path = Path(file_path).expanduser()
    if not path.exists() or not path.is_file():
        return f"Error: File {file_path} does not exist."
    text = extract_text(path)
    return text if text else f"Could not extract text from {path.name}."


class FileActionInput(BaseModel):
    file_path: str = Field(description="The original absolute path of the file.")
    suggested_name: str = Field(description="A clear, descriptive name in snake_case, including the date if found. Keep original extension.")
    suggested_category: str = Field(description="The main logical destination folder (e.g., 'Administrative', 'Code', 'Images', 'Invoices'). Reuse existing folders if appropriate.")
    summary_reason: str = Field(description="Short explanation of this choice (max 15 words).")
    is_junk: bool = Field(description="True if the file is an installer, temp file, cache, or useless draft that should be sent to the OS recycle bin.")
    confidence: int = Field(default=85, description="Confidence score (0-100). Use 95-100 for obvious files (clear dates, obvious context). Use 70-85 for files with vague names or partial context. Use <60 for ambiguous files where you are guessing.")


def build_propose_tool(proposals: dict, rate_limiter=None):
    """Creates a propose_file_action_tool that writes into the given *proposals* dict.

    This avoids module-level mutable state: the caller owns the dict and passes it in.
    """

    @tool(args_schema=FileActionInput)
    def propose_file_action_tool(
        file_path: str,
        suggested_name: str,
        suggested_category: str,
        summary_reason: str,
        is_junk: bool,
        confidence: int = 85,
    ) -> str:
        """Registers the final decision on how to rename and categorize a file.
        You MUST call this tool for each file you analyze once you've decided what to do with it.
        """
        if rate_limiter:
            rate_limiter.acquire_sync()

        proposals[file_path] = {
            "file_path": file_path,
            "suggested_name": suggested_name,
            "suggested_category": suggested_category,
            "summary_reason": summary_reason,
            "is_junk": is_junk,
            "confidence": confidence,
        }
        return f"Recorded proposal for {Path(file_path).name} -> {suggested_category}/{suggested_name} (Confidence: {confidence}%)"

    return propose_file_action_tool


class DirectoryActionInput(BaseModel):
    directory_path: str = Field(description="The original absolute path of the directory.")
    decision: str = Field(description="Must be either 'move_intact' or 'split'. Use 'move_intact' if it's a cohesive project folder. Use 'split' if it's a messy dump folder.")
    suggested_name: str = Field(default="", description="If moving intact, the new descriptive name for the folder (e.g., 'Project_Alpha'). Leave empty if splitting.")
    suggested_category: str = Field(default="", description="If moving intact, the main logical destination folder (e.g., 'Work/Projects'). Leave empty if splitting.")
    reasoning: str = Field(description="Short explanation of this choice.")


def build_propose_directory_tool(proposals: dict, rate_limiter=None):
    """Creates a propose_directory_action_tool."""

    @tool(args_schema=DirectoryActionInput)
    def propose_directory_action_tool(
        directory_path: str,
        decision: str,
        reasoning: str,
        suggested_name: str = "",
        suggested_category: str = "",
    ) -> str:
        """Registers the final decision on how to handle a DIRECTORY.
        If the directory is a cohesive project, use decision='move_intact'.
        If it's a messy dump folder with unrelated files, use decision='split'.
        """
        if rate_limiter:
            rate_limiter.acquire_sync()

        if decision not in ["move_intact", "split"]:
            return "Error: decision must be 'move_intact' or 'split'."

        proposals[directory_path] = {
            "file_path": directory_path,  # Use same key name for compatibility
            "is_dir": True,
            "decision": decision,
            "suggested_name": suggested_name,
            "suggested_category": suggested_category,
            "summary_reason": reasoning,
            "is_junk": False,
            "confidence": 95,
        }
        return f"Recorded directory proposal for {Path(directory_path).name} -> {decision}"

    return propose_directory_action_tool
