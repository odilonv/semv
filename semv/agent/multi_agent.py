import os
from typing import TypedDict, Annotated
from pathlib import Path

from langchain_mistralai import ChatMistralAI
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import create_react_agent

from semv.agent.tools import list_directory_tool, build_propose_tool
from semv.config import load_config
from semv.logger import get_logger
from semv.rate_limiter import RateLimiter, with_retry

logger = get_logger("agent.multi")


def update_proposals(left: dict, right: dict) -> dict:
    if left is None:
        left = {}
    if right is None:
        right = {}
    return {**left, **right}


class AgentState(TypedDict):
    directory_path: str
    files: list[dict]
    feedback: str | None
    
    # These will be populated by the supervisor
    code_files: list[dict]
    finance_files: list[dict]
    general_files: list[dict]
    
    # Reducer to merge dicts from parallel branches
    proposals: Annotated[dict, update_proposals]
    
    # Error tracking per branch
    errors: Annotated[dict, update_proposals]


def _resolve_api_key() -> str:
    config = load_config()
    key = config.get("api_key") or os.environ.get("MISTRAL_API_KEY")
    if not key:
        raise ValueError("No Mistral API key found. Run 'semv organize' to configure.")
    return key


def supervisor_node(state: AgentState):
    """Router that splits files based on extension heuristics."""
    code_files = []
    finance_files = []
    general_files = []
    
    code_exts = {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss",
        ".json", ".toml", ".yaml", ".yml", ".xml", ".sh", ".bat", ".ps1",
        ".go", ".rs", ".java", ".kt", ".c", ".cpp", ".h", ".rb", ".php",
        ".sql", ".r", ".m", ".swift", ".dart", ".lua", ".pl",
        ".dockerfile", ".makefile", ".cmake", ".gradle",
    }
    finance_exts = {".csv", ".xlsx", ".xls", ".pdf"}
    
    for f in state["files"]:
        ext = Path(f["path"]).suffix.lower()
        name = Path(f["path"]).name.lower()
        
        if ext in code_exts or name in ("makefile", "dockerfile", ".gitignore", ".env"):
            code_files.append(f)
        elif ext in finance_exts:
            finance_files.append(f)
        else:
            general_files.append(f)
    
    logger.info(
        "Supervisor split: %d code, %d finance, %d general",
        len(code_files), len(finance_files), len(general_files),
    )
    
    return {
        "code_files": code_files,
        "finance_files": finance_files,
        "general_files": general_files,
        "proposals": {},
        "errors": {},
    }


def create_specialized_agent(
    files: list[dict],
    directory_path: str,
    feedback: str | None,
    role_prompt: str,
    agent_name: str,
    rate_limiter: RateLimiter | None = None,
) -> dict:
    """Create and run a specialized agent with error isolation."""
    if not files:
        return {"proposals": {}, "errors": {}}
        
    try:
        api_key = _resolve_api_key()
        llm = ChatMistralAI(
            model="mistral-small-latest",
            temperature=0,
            api_key=api_key,
            max_retries=3,
        )
        
        proposals = {}
        propose_tool = build_propose_tool(proposals)
        tools = [list_directory_tool, propose_tool]
        
        prompt = role_prompt
        if feedback:
            prompt += f"\nCRITICAL USER FEEDBACK: The user rejected your last proposal: '{feedback}'. Adjust accordingly."
            
        agent = create_react_agent(llm, tools, prompt=prompt)
        
        files_list = ""
        for f in files:
            files_list += f"\nFile: {f['path']}\nContent Snippet:\n---\n{f['content']}\n---\n"
            
        user_message = f"Target Directory: {directory_path}\n\nPlease analyze and propose an action for the following files:\n{files_list}"
        
        def _invoke():
            return agent.invoke({"messages": [("user", user_message)]})

        if rate_limiter:
            with_retry(_invoke, rate_limiter)
        else:
            _invoke()

        logger.info("%s agent returned %d proposals", agent_name, len(proposals))
        return {"proposals": proposals, "errors": {}}
        
    except Exception as e:
        logger.error("%s agent failed: %s", agent_name, e)
        return {
            "proposals": {},
            "errors": {agent_name: str(e)},
        }


# Global rate limiter shared across agent nodes
_rate_limiter: RateLimiter | None = None


def set_rate_limiter(limiter: RateLimiter):
    """Set the rate limiter for multi-agent execution."""
    global _rate_limiter
    _rate_limiter = limiter


def code_agent_node(state: AgentState):
    prompt = """You are a Code/Developer Expert Agent. 
Organize the provided source code, config, and script files.
RULES:
1. Always put them in the 'Code' root folder.
2. Group files by language, framework, or project (e.g. 'Code/Python', 'Code/React_App').
3. Keep the names technically accurate (e.g., snake_case for python).
4. Calibrate confidence: 95-100 obvious, 70-85 educated guesses, <60 ambiguous.
5. Use propose_file_action_tool for EVERY file."""
    return create_specialized_agent(
        state["code_files"], state["directory_path"], state["feedback"],
        prompt, "Code", _rate_limiter,
    )


def finance_agent_node(state: AgentState):
    prompt = """You are a Finance/Accounting Expert Agent. 
Organize the provided PDFs, CSVs, and spreadsheets.
RULES:
1. Always put them in the 'Finance' root folder.
2. Group by type (e.g., 'Finance/Invoices', 'Finance/Receipts', 'Finance/Budgets').
3. Extract dates, amounts, and client names for renaming (e.g., 'invoice_clientName_march2024.pdf').
4. Calibrate confidence: 95-100 obvious, 70-85 educated guesses, <60 ambiguous.
5. Use propose_file_action_tool for EVERY file."""
    return create_specialized_agent(
        state["finance_files"], state["directory_path"], state["feedback"],
        prompt, "Finance", _rate_limiter,
    )


def general_agent_node(state: AgentState):
    config = load_config()
    custom_tax = config.get("taxonomy", ["Work", "Personal", "Media", "Archives"])
    tax_str = ", ".join(f"'{t}'" for t in custom_tax if t not in ["Code", "Finance"])
    
    prompt = f"""You are a General Organization Agent.
Organize the provided generic files (images, text, random docs).
RULES:
1. Use standard root folders: {tax_str}
2. Group files by project, event, or theme.
3. If an image has EXIF data, use it to group by date/event.
4. Rename with descriptive snake_case names.
5. Calibrate confidence: 95-100 obvious, 70-85 educated guesses, <60 ambiguous.
6. Use propose_file_action_tool for EVERY file."""
    return create_specialized_agent(
        state["general_files"], state["directory_path"], state["feedback"],
        prompt, "General", _rate_limiter,
    )


def run_multi_agent(
    directory_path: str,
    files_with_content: list[dict],
    feedback: str | None = None,
    rate_limiter: RateLimiter | None = None,
) -> dict:
    """Run the multi-agent graph with error isolation per branch."""
    if rate_limiter:
        set_rate_limiter(rate_limiter)

    builder = StateGraph(AgentState)
    
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("code_agent", code_agent_node)
    builder.add_node("finance_agent", finance_agent_node)
    builder.add_node("general_agent", general_agent_node)
    
    builder.add_edge(START, "supervisor")
    # Sequential execution (LangGraph free doesn't support true fan-out without
    # checkpointing). Each agent has error isolation so one failure won't block others.
    builder.add_edge("supervisor", "code_agent")
    builder.add_edge("code_agent", "finance_agent")
    builder.add_edge("finance_agent", "general_agent")
    builder.add_edge("general_agent", END)
    
    graph = builder.compile()
    
    initial_state = {
        "directory_path": directory_path,
        "files": files_with_content,
        "feedback": feedback,
        "code_files": [],
        "finance_files": [],
        "general_files": [],
        "proposals": {},
        "errors": {},
    }
    
    result = graph.invoke(initial_state)
    
    # Report any branch errors
    errors = result.get("errors", {})
    if errors:
        for agent_name, error in errors.items():
            logger.warning("Agent '%s' had errors: %s", agent_name, error)
    
    return result["proposals"]
