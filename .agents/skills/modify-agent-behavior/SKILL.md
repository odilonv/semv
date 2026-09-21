---
name: modify-agent-behavior
description: >-
  Use this skill when the user wants to update the agent's system prompt, modify its reasoning loop, or add/change the tools (function calling) it has access to.
---

# Modify Agent Behavior

This skill guides you to the correct files when modifying the core AI logic of `semv`.

## Architecture Context

`semv` uses a LangGraph ReAct agent powered by Mistral AI. The logic is separated into tools and the agent orchestrator.

## Steps to Modify

1. **Changing Tools / Function Calling Schemas**:
   - Open [semv/agent/tools.py](../../../semv/agent/tools.py).
   - This file contains the `@tool` decorators and Pydantic schemas (e.g., `propose_file_action_tool`). Modify the schemas here if you want the agent to output different data.

2. **Changing the System Prompt or ReAct Loop**:
   - Open [semv/agent/organizer_agent.py](../../../semv/agent/organizer_agent.py).
   - Here you can modify the core `system_prompt` and how user feedback is injected into the state.

3. **Verification**:
   - After making changes, always use the `test-agent-examples` skill to verify that the agent still produces valid proposals and hasn't broken the output schema.
