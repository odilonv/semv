import os

from langchain_mistralai import ChatMistralAI
from langgraph.prebuilt import create_react_agent

from semv.agent.tools import list_directory_tool, build_propose_tool
from semv.config import load_config
from semv.logger import get_logger
from semv.rate_limiter import RateLimiter, with_retry

logger = get_logger("agent.organizer")

SYSTEM_PROMPT = """\
You are an advanced agentic file organizer.
Your goal is to organize files in a given directory by suggesting a new name and a category (folder) for each file.

Steps:
1. Use `list_directory_tool` on the target directory to see existing folders.
2. Call `propose_file_action_tool` for EACH file to register your decision.

TAXONOMY RULES:
- Use broad root folders with logical subfolders (e.g., 'Work/Meetings', 'Finance/Invoices', 'Code/Python').
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
    propose_tool = build_propose_tool(proposals)

    tools = [list_directory_tool, propose_tool]

    config = load_config()
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

    logger.info("Agent returned %d proposals", len(proposals))
    return proposals
