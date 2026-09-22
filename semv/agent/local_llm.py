"""Local LLM support via llama-cpp-python.

Provides offline inference using a Mistral 7B GGUF model.
The model is automatically downloaded on first use to ~/.config/semv/models/.
"""

import os
from pathlib import Path

from semv.logger import get_logger

logger = get_logger("agent.local_llm")

MODELS_DIR = Path.home() / ".config" / "semv" / "models"
DEFAULT_MODEL_REPO = "TheBloke/Mistral-7B-Instruct-v0.2-GGUF"
DEFAULT_MODEL_FILE = "mistral-7b-instruct-v0.2.Q4_K_M.gguf"


def is_model_ready() -> bool:
    """Check whether the local GGUF model is already downloaded."""
    return (MODELS_DIR / DEFAULT_MODEL_FILE).exists()


def ensure_model_ready() -> Path:
    """Download the GGUF model if not already present. Returns the model path.

    This is safe to call from CLI code before agent processing starts,
    so the user sees a dedicated download step instead of a confusing
    spinner during agent processing.
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODELS_DIR / DEFAULT_MODEL_FILE

    if model_path.exists():
        logger.info("Local model found at %s", model_path)
        return model_path

    logger.info(
        "Downloading %s from %s (this may take a few minutes)...",
        DEFAULT_MODEL_FILE,
        DEFAULT_MODEL_REPO,
    )

    try:
        from huggingface_hub import hf_hub_download

        downloaded_path = hf_hub_download(
            repo_id=DEFAULT_MODEL_REPO,
            filename=DEFAULT_MODEL_FILE,
            local_dir=str(MODELS_DIR),
        )
        logger.info("Model downloaded to %s", downloaded_path)
        return Path(downloaded_path)
    except ImportError:
        raise ImportError(
            "huggingface-hub is required to download the local model. "
            "Install it with: pip install 'semv[local]'"
        )
    except Exception as e:
        raise RuntimeError(
            f"Failed to download model {DEFAULT_MODEL_FILE} from {DEFAULT_MODEL_REPO}: {e}"
        ) from e

_cached_llm = None


def get_local_llm():
    """Create and return a LangChain-compatible LLM backed by llama-cpp-python.

    Returns a ChatLlamaCpp instance ready to be used as a drop-in replacement
    for ChatMistralAI in the agent pipeline. The LLM is cached after first load.
    """
    global _cached_llm
    if _cached_llm is not None:
        return _cached_llm

    try:
        from langchain_community.chat_models import ChatLlamaCpp
    except ImportError:
        raise ImportError(
            "llama-cpp-python and langchain-community are required for local inference. "
            "Install them with: pip install 'semv[local]'"
        )

    model_path = ensure_model_ready()

    # Detect GPU layers: use all layers on GPU if available, else CPU-only
    n_gpu_layers = int(os.environ.get("SEMV_GPU_LAYERS", "-1"))

    llm = ChatLlamaCpp(
        model_path=str(model_path),
        temperature=0,
        n_ctx=8192,
        n_gpu_layers=n_gpu_layers,
        verbose=False,
    )

    logger.info(
        "Local LLM loaded: %s (n_gpu_layers=%d)",
        DEFAULT_MODEL_FILE,
        n_gpu_layers,
    )
    _cached_llm = llm
    return llm
