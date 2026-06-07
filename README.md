# semv — Semantic file organizer

A small command-line tool that reads file contents (text, code, PDF) and suggests clearer filenames
and simple organization actions. semv runs in your terminal, offers a quick interactive review,
and can run in the background to watch folders.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org)

---

## What it does

semv extracts concise semantic information from files and uses it to propose:

- human-friendly filenames and folder suggestions
- detection of near-duplicate files using vector embeddings
- batch review so you can accept or reject many suggestions at once

You decide how suggestions are applied — nothing is renamed without your confirmation.

---

## Key points

- Content-aware: works from file content, not just extensions or timestamps.
- Privacy-first: supports local model inference (no cloud) or optional cloud APIs.
- Lightweight: written in Python, no Docker required for normal use.
- Watcher mode: optional daemon watches folders and queues items for review.

---

## Install

Recommended (install globally with pipx):

```bash
pipx install git+https://github.com/odilonv/semv.git
```

Or for local development:

```bash
git clone https://github.com/odilonv/semv.git
cd semv
poetry install
pip install -e .
```

---

## Usage (examples)

- Start background watcher:

```bash
semv daemon
```

- Scan a folder immediately:

```bash
semv scan --path ~/Downloads
```

- Open the interactive review UI:

```bash
semv review
```

---

## Internals (brief)

- CLI: `Typer` + a lightweight TUI for batch review
- Storage: SQLite for state and resilience
- Embeddings/DB: ChromaDB for deduplication and similarity
- Models: local inference via `llama-cpp-python` or remote APIs

---

## Contributing

Patches and issues welcome. Quick start for contributors:

```bash
git clone https://github.com/odilonv/semv.git
cd semv
poetry install
pip install -e .
```

If you add features, please include tests or a short usage example.
