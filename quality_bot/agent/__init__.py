"""LangGraph-based agent package for the Hajj & Umrah News Telegram bot.

The heavy domain logic (news fetching from haj.gov.sa + CNN Arabic, filtering,
content extraction, PDF rendering, usage tracking, Arabic prompt builders)
lives in the ``telegram_bot_hajj`` module and is imported here via
``_legacy``. This package adds:

* ``llm``    – a LangChain LLM layer (Bedrock primary, Azure Anthropic fallback)
* ``state``  – typed graph state
* ``nodes``  – pipeline steps as graph nodes
* ``graphs`` – one StateGraph per report type
* ``bot``    – the Telegram layer that drives the graphs
"""
# Load environment variables as early as possible — before ``config`` reads them
# and before the Hajj module (which requires AWS_BEARER_TOKEN_BEDROCK at import)
# is loaded. This runs whenever any agent submodule is imported, regardless of
# the current working directory (so it works under uvicorn / servers too).
import os as _os

from dotenv import load_dotenv as _load_dotenv

_HERE = _os.path.dirname(_os.path.abspath(__file__))            # agent/
_QUALITY_BOT = _os.path.dirname(_HERE)                          # quality_bot/
_REPO_ROOT = _os.path.dirname(_QUALITY_BOT)                     # repo root
for _env in (_os.path.join(_QUALITY_BOT, ".env"), _os.path.join(_REPO_ROOT, ".env")):
    if _os.path.exists(_env):
        _load_dotenv(_env)
