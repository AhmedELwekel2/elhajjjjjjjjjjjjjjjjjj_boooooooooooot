"""Runtime configuration for the LangGraph agent.

All values come from environment variables (loaded by the legacy module's
``load_dotenv()`` at import time) so deployment behaviour is unchanged.
"""
import os

# --- AWS Bedrock (primary LLM) ---------------------------------------------
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
# Inference-profile ID (or model ID) used by ChatBedrockConverse. Kept as the
# existing default so we don't reference a model the AWS account may not have
# provisioned. Set AWS_BEDROCK_INFERENCE_PROFILE_ID to use e.g.
# "anthropic.claude-opus-4-8".
BEDROCK_MODEL_ID = (
    os.getenv("AWS_BEDROCK_INFERENCE_PROFILE_ID")
    or "anthropic.claude-sonnet-4-20250514"
)

# --- Azure Anthropic endpoint (fallback LLM) -------------------------------
AZURE_API_KEY = os.getenv("AZURE_API_KEY", "") or None
AZURE_MODEL = os.getenv("AZURE_MODEL", "opus4.5")
# ChatAnthropic (the Anthropic SDK) appends "/v1/messages" to base_url, so we
# strip any trailing "/v1/messages" the legacy env var may carry.
_raw_azure_url = os.getenv(
    "AZURE_API_URL",
    "https://transformellica-gpt5-1.services.ai.azure.com/anthropic/v1/messages",
)
AZURE_BASE_URL = _raw_azure_url.rstrip("/")
for _suffix in ("/v1/messages", "/messages"):
    if AZURE_BASE_URL.endswith(_suffix):
        AZURE_BASE_URL = AZURE_BASE_URL[: -len(_suffix)]
        break

# --- Generation defaults ----------------------------------------------------
DEFAULT_MAX_TOKENS = 50000
DEFAULT_TEMPERATURE = 0.5
LLM_TIMEOUT_SECONDS = 600

HAS_BEDROCK = bool(AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY)
HAS_AZURE = bool(AZURE_API_KEY and AZURE_API_KEY != "your_azure_api_key_here")
