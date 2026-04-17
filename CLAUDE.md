# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**slay2agent** is a Python 3.11+ starter project. Currently minimal — a single `main.py` entry point with no external dependencies.

## Commands

```bash
# Run the application
python3 main.py
```

No build, lint, or test tooling is configured yet. To add dependencies, update the `dependencies` list in `pyproject.toml`.

## Architecture

The project is in its earliest stage:

- `main.py` — entry point, contains a single `main()` function
- `pyproject.toml` — project metadata (name: `slay2agent`, requires Python >=3.11, no deps)
- `.python-version` — pins Python to `3.11` for pyenv/asdf
