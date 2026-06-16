"""LangChain LLM layer.

Replaces the hand-rolled ``call_claude_api`` (boto3 SigV4 + raw Azure HTTP)
with LangChain chat models, while preserving the same dual-provider behaviour:

* **Primary**  – ``ChatBedrockConverse`` (AWS Bedrock, Converse API)
* **Fallback** – ``ChatAnthropic`` pointed at the Azure Anthropic endpoint

Public API mirrors the legacy ``(text, error)`` contract so callers/nodes don't
need to handle exceptions:

    text, error = invoke_text(system_message, user_message, max_tokens=...)
    text, error = await ainvoke_text(system_message, user_message, ...)
"""
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from . import config

logger = logging.getLogger(__name__)

_bedrock_client = None  # cached boto3 bedrock-runtime client


def _get_bedrock_boto_client():
    global _bedrock_client
    if _bedrock_client is None:
        import boto3
        from botocore.config import Config

        _bedrock_client = boto3.client(
            service_name="bedrock-runtime",
            region_name=config.AWS_REGION,
            aws_access_key_id=config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
            config=Config(
                connect_timeout=config.LLM_TIMEOUT_SECONDS,
                read_timeout=config.LLM_TIMEOUT_SECONDS,
                retries={"max_attempts": 2},
            ),
        )
    return _bedrock_client


def _build_bedrock(max_tokens, temperature):
    from langchain_aws import ChatBedrockConverse

    return ChatBedrockConverse(
        model=config.BEDROCK_MODEL_ID,
        client=_get_bedrock_boto_client(),
        max_tokens=max_tokens,
        temperature=temperature,
    )


def _build_azure(max_tokens, temperature):
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=config.AZURE_MODEL,
        api_key=config.AZURE_API_KEY,
        base_url=config.AZURE_BASE_URL,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=config.LLM_TIMEOUT_SECONDS,
        default_headers={"anthropic-version": "2023-06-01"},
    )


def _extract_text(message) -> str:
    """AIMessage.content may be a string or a list of content blocks."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def _messages(system_message, user_message):
    msgs = []
    if system_message:
        msgs.append(SystemMessage(content=system_message))
    msgs.append(HumanMessage(content=user_message))
    return msgs


def invoke_text(system_message, user_message, max_tokens=None, temperature=None):
    """Synchronous generation. Returns ``(text, error)`` — error is None on success."""
    max_tokens = max_tokens or config.DEFAULT_MAX_TOKENS
    temperature = config.DEFAULT_TEMPERATURE if temperature is None else temperature
    messages = _messages(system_message, user_message)

    if config.HAS_BEDROCK:
        try:
            logger.info("LLM: invoking AWS Bedrock (%s)", config.BEDROCK_MODEL_ID)
            text = _extract_text(_build_bedrock(max_tokens, temperature).invoke(messages))
            if text:
                return text, None
            logger.warning("Bedrock returned empty content; trying Azure fallback")
        except Exception as e:
            logger.error("Bedrock call failed (%s): %s", type(e).__name__, e)

    if config.HAS_AZURE:
        try:
            logger.info("LLM: invoking Azure Anthropic (%s)", config.AZURE_MODEL)
            text = _extract_text(_build_azure(max_tokens, temperature).invoke(messages))
            if text:
                return text, None
            return None, "Azure returned empty content"
        except Exception as e:
            logger.error("Azure call failed (%s): %s", type(e).__name__, e)
            return None, f"Azure API Error ({type(e).__name__}): {e}"

    return None, "No AI API credentials configured (neither AWS Bedrock nor Azure)"


async def ainvoke_text(system_message, user_message, max_tokens=None, temperature=None):
    """Async generation. Returns ``(text, error)`` — error is None on success."""
    max_tokens = max_tokens or config.DEFAULT_MAX_TOKENS
    temperature = config.DEFAULT_TEMPERATURE if temperature is None else temperature
    messages = _messages(system_message, user_message)

    if config.HAS_BEDROCK:
        try:
            logger.info("LLM: invoking AWS Bedrock async (%s)", config.BEDROCK_MODEL_ID)
            text = _extract_text(
                await _build_bedrock(max_tokens, temperature).ainvoke(messages)
            )
            if text:
                return text, None
            logger.warning("Bedrock returned empty content; trying Azure fallback")
        except Exception as e:
            logger.error("Bedrock async call failed (%s): %s", type(e).__name__, e)

    if config.HAS_AZURE:
        try:
            logger.info("LLM: invoking Azure Anthropic async (%s)", config.AZURE_MODEL)
            text = _extract_text(
                await _build_azure(max_tokens, temperature).ainvoke(messages)
            )
            if text:
                return text, None
            return None, "Azure returned empty content"
        except Exception as e:
            logger.error("Azure async call failed (%s): %s", type(e).__name__, e)
            return None, f"Azure API Error ({type(e).__name__}): {e}"

    return None, "No AI API credentials configured (neither AWS Bedrock nor Azure)"
