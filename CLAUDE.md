# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

This repository is empty as of the initial commit. It contains only:

- `README.md` — a single line, `# buy_agent`
- `.gitignore` — the unmodified GitHub `Python.gitignore` template

There is no source code, no dependency manifest (`pyproject.toml` / `requirements.txt`), no test suite, and no CI configuration. Consequently there are **no build, lint, or test commands to document yet**, and there is no architecture to describe. Any command or module layout stated here that is not listed above would be a guess.

The Python `.gitignore` is the only signal about the intended stack; treat it as an intent, not a decision. Nothing has been chosen about packaging (uv / poetry / pip), test runner, or linter.

## When adding the first code

Replace the "Repository status" section above with the real build/lint/test commands (including how to run a single test) and a description of the architecture, once those exist. Until then, ask the user rather than assuming a toolchain.

## Environment

Development happens on Windows with PowerShell as the default shell; prefer PowerShell syntax for terminal commands, or use the Bash tool explicitly for POSIX scripts.
