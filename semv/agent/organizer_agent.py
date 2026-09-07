import os

from langchain_mistralai import ChatMistralAI
from langgraph.prebuilt import create_react_agent

from semv.agent.tools import (
    list_directory_tool,
    read_file_snippet_tool,
    build_propose_tool,
    build_propose_directory_tool,
)
from semv.config import load_config
from semv.logger import get_logger
from semv.rate_limiter import RateLimiter, with_retry

logger = get_logger("agent.organizer")

SYSTEM_PROMPT = """\
You are an advanced agentic file organizer.
Your goal is to organize files in a given directory by suggesting a new name and a category (folder) for each file.

Steps:
For FILES:
- Use propose_file_action_tool to decide its category and name.

For DIRECTORIES:
- Analyze its name. If unsure, use list_directory_tool to see what's inside.
- If it's a cohesive project, asset pack, or logical grouping (e.g., 'Project_Alpha', 'React_Template'), use propose_directory_action_tool with decision='move_intact'.
- If it's a messy dump folder with unrelated files (e.g., 'New folder', 'temp_dump', 'Desktop_files'), use propose_directory_action_tool with decision='split'.

You MUST call propose_file_action_tool or propose_directory_action_tool for EVERY item provided.

TAXONOMY RULES:
- Use broad root folders with logical subfolders, and don't hesitate to create specific sub-topic folders inside them if it makes sense (e.g., 'Work/Meetings/Q3', 'Finance/Invoices/2024', 'Media/Images/Personal/Vacances_plage').
- Keep names clean, descriptive, snake_case, and without dates unless crucial.
- ONLY use these ROOT folders unless impossible:
  * 'Work' (Professional documents, business reports, meeting notes)
  * 'Personal' (Private notes, grocery lists, personal letters)
  * 'Finance' (Invoices, receipts, tax documents)
  * 'Code' (Source code, HTML, CSS, configs, scripts)
  * 'Media' (Images, videos, audio)
  * 'Archives' (ZIP files, backups)
- GROUP BY THEME/PROJECT: similar files share subfolders.
- NEVER create micro-categories at root (e.g., no 'Web', use 'Code/Web').
- REUSE existing folders from `list_directory_tool`.

NAMING: snake_case, keep original extension.
JUNK: installers, temp files, caches → is_junk=True.
CONFIDENCE: 95-100 obvious, 70-85 guesses, <60 ambiguous.
CRITICAL: Call `propose_file_action_tool` for EVERY file.
"""

CLEAN_SYSTEM_PROMPT = """\
You are an advanced file cleanup assistant.
Your ONLY goal is to identify files that are junk and can be safely deleted.

Steps:
For FILES:
- Use propose_file_action_tool to decide if it is junk.
- ALWAYS set suggested_category="[Recycle Bin]", keep the original name, and provide a summary_reason.

For DIRECTORIES:
- Analyze its name. If unsure, use list_directory_tool to see what's inside.
- If it's a messy dump folder, use propose_directory_action_tool with decision='split' to evaluate its contents.

You MUST call propose_file_action_tool or propose_directory_action_tool for EVERY item provided.

RULES:
- is_junk=true for installers, temp files, caches, useless logs, duplicated fragments, or redundant data.
- is_junk=false for actual user documents, code, media, etc.
- CONFIDENCE: 95-100 obvious, 70-85 guesses, <60 ambiguous.
- CRITICAL: Call `propose_file_action_tool` for EVERY file.
"""


def _resolve_api_key() -> str:
    config = load_config()
    key = config.get("api_key") or os.environ.get("MISTRAL_API_KEY")
    if not key:
        raise ValueError("No Mistral API key found. Run 'semv organize' to configure.")
    return key


def run_organizer_agent(
    directory_path: str,
    files_with_content: list[dict],
    feedback: str | None = None,
    rate_limiter: RateLimiter | None = None,
    operation_mode: str = "organize",
) -> dict:
    """Runs the ReAct agent on the given files and returns a dict of proposals.

    Each call gets its own proposals dict — no shared mutable state.
    files_with_content is a list of dicts: {"path": str, "content": str}
    """
    api_key = _resolve_api_key()
    llm = ChatMistralAI(
        model="mistral-small-latest",
        temperature=0,
        api_key=api_key,
        max_retries=3,  # Low retries here; we handle retries at a higher level
    )

    proposals: dict = {}
    propose_tool = build_propose_tool(proposals, rate_limiter=rate_limiter)
    propose_dir_tool = build_propose_directory_tool(proposals, rate_limiter=rate_limiter)

    tools = [list_directory_tool, propose_tool, propose_dir_tool]

    config = load_config()
    
    if operation_mode == "clean":
        dynamic_prompt = CLEAN_SYSTEM_PROMPT
    else:
        custom_tax = config.get("taxonomy")
        if custom_tax:
            tax_str = ", ".join(f"'{t}'" for t in custom_tax)
            dynamic_prompt = SYSTEM_PROMPT.replace(
                "ONLY use these ROOT folders unless impossible:\n  * 'Work' (Professional documents, business reports, meeting notes)\n  * 'Personal' (Private notes, grocery lists, personal letters)\n  * 'Finance' (Invoices, receipts, tax documents)\n  * 'Code' (Source code, HTML, CSS, configs, scripts)\n  * 'Media' (Images, videos, audio)\n  * 'Archives' (ZIP files, backups)",
                f"ONLY use these USER-DEFINED ROOT folders unless impossible: {tax_str}"
            )
        else:
            dynamic_prompt = SYSTEM_PROMPT

    if feedback:
        dynamic_prompt += (
            f"\nCRITICAL USER FEEDBACK: The user previously rejected your proposal "
            f"and said: '{feedback}'. Please adjust your organization strategy accordingly."
        )

    agent = create_react_agent(llm, tools, prompt=dynamic_prompt)

    files_list = ""
    for f in files_with_content:
        files_list += f"\nFile: {f['path']}\nContent Snippet:\n---\n{f['content']}\n---\n"

    user_message = f"Target Directory: {directory_path}\n\nPlease analyze and propose an action for the following files:\n{files_list}"

    def _invoke():
        return agent.invoke({"messages": [("user", user_message)]})

    if rate_limiter:
        logger.debug("Invoking agent with rate limiter for %d files", len(files_with_content))
        with_retry(_invoke, rate_limiter)
    else:
        _invoke()

    logger.debug("Agent returned %d proposals", len(proposals))
    return proposals
