import os

from langchain_mistralai import ChatMistralAI
from langgraph.prebuilt import create_react_agent

from semv.agent.tools import list_directory_tool, read_file_snippet_tool, build_propose_tool
from semv.config import load_config

SYSTEM_PROMPT = """\
You are an advanced agentic file organizer.
Your goal is to organize files in a given directory by suggesting a new name and a category (folder) for each file.

To achieve this, you MUST follow these steps:
1. Use `list_directory_tool` on the target directory to see what folders and files already exist.
2. Read the content snippet for each file provided in my message to understand its context.
3. Call `propose_file_action_tool` for EACH file to register your decision.

CRITICAL EXPERT TAXONOMY RULES:
- Use broad, standard macro-categories as the root folder, and create logical subfolders inside them to keep things perfectly organized (e.g., 'Work/Meetings', 'Finance/Invoices', 'Code/Python').
- ONLY use the following standard ROOT folders unless absolutely impossible:
  * 'Work' (Professional documents, business reports, meeting notes)
  * 'Personal' (Private notes, grocery lists, personal letters)
  * 'Finance' (Invoices, receipts, tax documents)
  * 'Code' (Source code, HTML, CSS, configs, scripts)
  * 'Media' (Images, videos, audio)
  * 'Archives' (ZIP files, backups)
- GROUP BY THEME/PROJECT: If multiple files share the same context, theme, or project, group them together in a specific shared subfolder (e.g., 'Work/Project_Alpha' instead of scattering them into 'Work/Reports' and 'Work/Notes'). Look for similarities in titles or content.
- NEVER create micro-categories at the root level (e.g., do not create 'Web' at the root, use 'Code/Web').
- NEVER create generic type-based folders at the root (e.g., do not create 'Notes' or 'Reports', classify them by domain into 'Work/Notes' or 'Personal/Notes').
- HEAVILY PRIORITIZE reusing existing folders and subfolders if they align with the taxonomy. Use `list_directory_tool` to see them.

Guidelines for proposing actions:
- Rename files using clear, descriptive names in snake_case. Keep the original extension.
- If a file is useless (e.g., installers, temp files, caches, dumps), mark `is_junk` as True (these will be safely moved to the OS Recycle Bin).
- CRITICAL: Calibrate your confidence score honestly! Use 95-100 for exact matches and obvious files. Use 70-85 for educated guesses (e.g. vague names or lack of context). Use <60 if the file is ambiguous and you are just guessing.
- You must call `propose_file_action_tool` for EVERY file in the list provided by the user.
"""


def _resolve_api_key() -> str:
    config = load_config()
    key = config.get("api_key") or os.environ.get("MISTRAL_API_KEY")
    if not key:
        raise ValueError("No Mistral API key found. Run 'semv organize' to configure.")
    return key


def run_organizer_agent(directory_path: str, files_with_content: list[dict], feedback: str | None = None) -> dict:
    """Runs the ReAct agent on the given files and returns a dict of proposals.

    Each call gets its own proposals dict — no shared mutable state.
    files_with_content is a list of dicts: {"path": str, "content": str}
    """
    api_key = _resolve_api_key()
    llm = ChatMistralAI(model="mistral-small-latest", temperature=0, api_key=api_key, max_retries=10)

    proposals: dict = {}
    propose_tool = build_propose_tool(proposals)

    tools = [list_directory_tool, propose_tool]

    config = load_config()
    custom_tax = config.get("taxonomy")
    if custom_tax:
        tax_str = ", ".join(f"'{t}'" for t in custom_tax)
        dynamic_prompt = SYSTEM_PROMPT.replace(
            "ONLY use the following standard ROOT folders unless absolutely impossible:\n  * 'Work' (Professional documents, business reports, meeting notes)\n  * 'Personal' (Private notes, grocery lists, personal letters)\n  * 'Finance' (Invoices, receipts, tax documents)\n  * 'Code' (Source code, HTML, CSS, configs, scripts)\n  * 'Media' (Images, videos, audio)\n  * 'Archives' (ZIP files, backups)",
            f"ONLY use the following USER-DEFINED standard ROOT folders unless absolutely impossible: {tax_str}"
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

    agent.invoke({"messages": [("user", user_message)]})

    return proposals

