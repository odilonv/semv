---
name: test-agent-examples
description: >-
  Use this skill when the user asks to test the agent, run a dry-run, or see how the AI categorizes files without modifying the actual file system.
---

# Test Agent on Examples

This skill is used to safely test the `semv` agent's reasoning capabilities on the provided dummy data in the `examples/` directory.

## Steps

1. To test the basic categorization reasoning, run:
   ```bash
   poetry run semv organize examples/test_folder/ --dry-run
   ```

2. To test the advanced Directory Reasoning (complex folders with nested projects), run:
   ```bash
   poetry run semv organize examples/test_folder_advanced/ --dry-run
   ```

3. **Important**: Always use the `--dry-run` flag when testing to ensure files are not actually moved or sent to the Recycle Bin. Observe the Rich table output in the terminal to validate the agent's proposals.
