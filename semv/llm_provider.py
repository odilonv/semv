import json
import os
from pydantic import BaseModel, Field
from mistralai import Mistral

# 1. THE SCHEMA: This defines exactly how the AI must respond.
# Pydantic will guarantee that our Python code receives these exact fields.
class FileAction(BaseModel):
    is_junk: bool = Field(
        ..., 
        description="True if the file is an installer, temp file, or useless draft."
    )
    suggested_name: str = Field(
        ..., 
        description="Clear, descriptive name in snake_case, including the date if found."
    )
    suggested_category: str = Field(
        ..., 
        description="Main logical destination folder (e.g., Administrative, Code, Images, Invoices)."
    )
    summary_reason: str = Field(
        ..., 
        description="Short explanation of this choice (15 words max)."
    )
    
class MistralCloudProvider:
    def __init__(self, api_key: str = None):
        key = api_key or os.getenv("MISTRAL_API_KEY")
        if not key:
            raise ValueError("MISTRAL_API_KEY environment variable is not set.")
        self.client = Mistral(api_key=key)
        self.model = "mistral-small-latest"
        
    def analyze_text(self, text_content: str, existing_folders: list[str] = None) -> FileAction:
        
        folders_context = ""
        if existing_folders:
            folders_list = ", ".join(existing_folders)
            folders_context = f"\nExisting folders in this directory: [{folders_list}].\nCRITICAL: Prefer choosing one of these existing folders if it matches the theme perfectly. If none fit, invent a new logical folder name."

        prompt = f"""
        You are an advanced OS file organizer system.
        Analyze the following file content and categorize it.
        {folders_context}
        
        File content snippet:
        ---
        {text_content[:2000]} 
        ---
        """
        
        response = self.client.chat.complete(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        
        raw_json_string = response.choices[0].message.content
        
        return FileAction.model_validate_json(raw_json_string)
    
    
# 3. THE LOCAL PROVIDER
class MistralLocalProvider:
    def __init__(self):
        from huggingface_hub import hf_hub_download
        from llama_cpp import Llama
        
        # We download (or load from cache) Mistral-7B-Instruct (Quantized Q4_K_M ~ 4.1GB)
        # This will automatically show a progress bar in the terminal on the first run!
        model_path = hf_hub_download(
            repo_id="MaziyarPanahi/Mistral-7B-Instruct-v0.3-GGUF",
            filename="Mistral-7B-Instruct-v0.3.Q4_K_M.gguf"
        )
        
        # Load the model into RAM (n_ctx is the context window, n_gpu_layers offloads to GPU if available)
        self.llm = Llama(model_path=model_path, n_ctx=2048, n_gpu_layers=-1, verbose=False)

    def analyze_text(self, text_content: str, existing_folders: list[str] = None) -> FileAction:
        
        folders_context = ""
        if existing_folders:
            folders_list = ", ".join(existing_folders)
            folders_context = f"\nExisting folders in this directory: [{folders_list}].\nCRITICAL: Prefer choosing one of these existing folders if it matches the theme perfectly. If none fit, invent a new logical folder name."

        prompt = f"""
        Analyze this file content and return a JSON matching the schema.
        {folders_context}
        
        Content:
        ---
        {text_content[:2000]}
        ---
        """
        
        # llama-cpp-python has built-in JSON schema enforcement!
        response = self.llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            response_format={
                "type": "json_object",
                "schema": FileAction.model_json_schema()
            },
            temperature=0.1
        )
        
        raw_json_string = response["choices"][0]["message"]["content"]
        return FileAction.model_validate_json(raw_json_string)

# 4. THE FACTORY
def get_llm_provider():
    """Reads the configuration and returns the appropriate Provider."""
    config = load_config()
    
    if config.get("mode") == "cloud":
        if "api_key" not in config:
            raise ValueError("Cloud mode selected but no API key found.")
        return MistralCloudProvider(api_key=config["api_key"])
    
    elif config.get("mode") == "local":
        return MistralLocalProvider()
    
    else:
        raise ValueError("System is not configured. Run 'semv scan' to trigger setup.")