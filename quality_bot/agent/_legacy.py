"""Bridge to the Hajj & Umrah bot module.

The ``telegram_bot_hajj`` module (in the repo root) holds the battle-tested,
non-LLM domain logic: scraping (haj.gov.sa + CNN Arabic), recency filtering,
content extraction, PDF rendering, usage limits, and the Arabic prompt helpers.
Importing it here keeps that logic as the single source of truth instead of
duplicating it.

Importing the module has no side effects beyond loading env vars, registering
fonts, and building the Bedrock client — the Telegram application is only built
inside its ``main()`` guard, which we never call.
"""
import os
import sys

# ``telegram_bot_hajj`` lives in the repo root (one level above ``quality_bot``).
# Make both the package parent (``quality_bot``) and the repo root importable so
# the module resolves whether the agent is launched as ``python -m agent.bot``
# from ``quality_bot`` or from the repo root.
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # quality_bot
_REPO_ROOT = os.path.dirname(_PARENT)                                    # ibdb_V1
for _p in (_REPO_ROOT, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Load env vars deterministically BEFORE importing the Hajj module — it requires
# AWS_BEARER_TOKEN_BEDROCK at import time and otherwise relies on the current
# working directory to find ``.env`` (which varies under uvicorn / servers).
from dotenv import load_dotenv  # noqa: E402

for _env in (os.path.join(_PARENT, ".env"), os.path.join(_REPO_ROOT, ".env")):
    if os.path.exists(_env):
        load_dotenv(_env)

import telegram_bot_hajj as legacy  # noqa: E402

# --- News acquisition & processing -----------------------------------------
fetch_hajgov_news = legacy.fetch_hajgov_news
fetch_cnn_hajj_news = legacy.fetch_cnn_hajj_news
filter_recent_articles = legacy.filter_recent_articles
categorize_articles = legacy.categorize_articles
categorize_articles_for_blogs = legacy.categorize_articles_for_blogs
enhance_articles_with_content = legacy.enhance_articles_with_content
clean_deduplicate_articles = legacy.clean_deduplicate_articles
format_news_message = legacy.format_news_message
scrape_og_image = legacy.scrape_og_image

# --- Prompt builders --------------------------------------------------------
build_keyword_instruction_block = legacy.build_keyword_instruction_block
keywords_summary_text = legacy.keywords_summary_text
parse_keyword_input = legacy.parse_keyword_input
get_user_keywords = legacy.get_user_keywords
KEYWORD_INPUT_INSTRUCTIONS = legacy.KEYWORD_INPUT_INSTRUCTIONS

# --- PDF / rendering --------------------------------------------------------
create_hajj_blog_pdf = legacy.create_hajj_blog_pdf
render_magazine_pdf = legacy.render_magazine_pdf
render_newspaper_pdf = legacy.render_newspaper_pdf
build_fallback_hajj_blog_content = legacy.build_fallback_hajj_blog_content

# --- Usage limits & admin ---------------------------------------------------
check_usage_limit = legacy.check_usage_limit
increment_usage = legacy.increment_usage
reset_user_usage = legacy.reset_user_usage
get_usage_status = legacy.get_usage_status
get_user_id = legacy.get_user_id
USAGE_LIMITS = legacy.USAGE_LIMITS
ADMIN_USER_IDS = legacy.ADMIN_USER_IDS

# --- Config -----------------------------------------------------------------
TELEGRAM_TOKEN = legacy.TELEGRAM_TOKEN
