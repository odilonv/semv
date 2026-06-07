# 🧹 semv (Semantic Move)

> **The command-line OS Agent that understands your files to organize them better.**

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org)
[![Mistral AI](https://img.shields.io/badge/AI-Mistral-orange.svg)](https://mistral.ai)
[![CLI](https://img.shields.io/badge/CLI-Typer-green.svg)](https://typer.tiangolo.com)

Classic cleaning tools rely on file extensions or creation dates. `semv` reads the actual content of your files (PDF, Code, Text), extracts the semantic context, and uses Generative AI to propose standardized renaming and logical categorization. 

All of this happens natively within your terminal.

---

## ✨ Key Features

* **🧠 Semantic Understanding:** Reads and comprehends the actual content of your documents, not just their metadata.
* **🔒 Privacy-First (Hybrid):** Run inference 100% locally (GGUF) for sensitive data, or via the Cloud (Mistral API) to save disk space. You choose.
* **👯 Duplicate Detection:** Identifies semantically identical files, even if their names are completely different (powered by ChromaDB).
* **⚡ "Batch-Approval" Efficiency:** Ultra-fast TUI (Terminal User Interface). The AI proposes, you validate everything in one click (inspired by `lazygit`).
* **📦 Zero Heavy Dependencies:** 100% Python. No Docker, no external background daemons required.

---

## 🚀 Installation

`semv` is designed to be installed globally on your machine using `pipx`.

```bash
# Direct installation from GitHub
pipx install git+[https://github.com/odilonv/semv.git](https://github.com/odilonv/semv.git)
```
*Upon the first execution, an interactive setup wizard will guide you to choose your inference engine (Local or Cloud).*

## 💻 Usage

The tool is built around 3 simple commands:

### 1. Background Watcher
Launch the silent daemon that monitors your target directories (e.g., Downloads).
```bash
semv daemon
```
*As soon as 10 files are processed and ready to be organized, you will receive a native OS notification.*

### 2. Forced Directory Scan
Ideal for cleaning up a messy folder or an old hard drive in one go.
```bash
semv scan --path ~/Downloads
```

### 3. Review and Organize
Open the interactive terminal interface to validate the AI's proposals.
```bash
semv review
```

## 🛠️ Under the Hood (Architecture)

This project was built to demonstrate a robust, asynchronous, and modern AI software architecture:

* **CLI & TUI:** `Typer`, `Rich`, `Questionary`.
* **Asynchronous Core:** `asyncio`, `watchdog` for OS-level event listening, and non-blocking I/O operations.
* **State & Memory:** `SQLite` (via `aiosqlite`) for process resilience.
* **RAG & Vectors:** Embedded `ChromaDB`.
* **LLM Engine:** `llama-cpp-python` (Local) / `mistralai` (Cloud) using the Strategy Pattern.
* **Structured Outputs:** `instructor` + `Pydantic v2` to constrain the LLM into strict JSON schemas.

---

## 🤝 Contributing

Pull Requests are highly welcome. For local development:

```bash
git clone [https://github.com/odilonv/semv.git](https://github.com/odilonv/semv.git)
cd semv
poetry install
pip install -e .
```