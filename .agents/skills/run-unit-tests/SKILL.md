---
name: run-unit-tests
description: >-
  Use this skill when the user asks to run the test suite, verify the code works, or check for regressions in the semv codebase.
---

# Run Unit Tests

This skill executes the test suite for the `semv` project.

## Steps

1. Ensure you are in the project root directory.
2. The project uses Poetry for dependency management. Run the tests using:
   ```bash
   poetry run pytest test/
   ```
3. Analyze the test output. If there are failures, read the error tracebacks carefully to identify which module (`cli`, `organizer`, `text_extraction`, or `agent`) is causing the issue.
