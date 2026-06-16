# Quality & Excellence Bot — LangGraph Architecture

The bot is structured as a set of **deterministic LangGraph workflows**. Each
Telegram command maps to a compiled `StateGraph` that runs the same pipeline:

```
fetch → filter → enhance → generate (LLM) → render (PDF)
```

Non-LLM domain logic (scraping, content extraction, PDF rendering, usage
limits, Arabic prompt builders) is reused from the original
`telegram_bot_quality_arabic_claude_version` module via `_legacy.py`.

---

## 1. High-level layers

```mermaid
flowchart TB
    subgraph TG["Telegram Layer — agent/bot.py"]
        CMD["/news /weekly /monthly /magazine\n+ inline buttons"]
        DRV["_run_report()\nusage gate · astream · send PDFs"]
        CMD --> DRV
    end

    subgraph GR["Workflow Layer — agent/graphs.py"]
        DG["daily_graph"]
        PG["periodic_graph\n(weekly / monthly)"]
        MG["magazine_graph"]
    end

    subgraph CORE["Building Blocks"]
        NODES["agent/nodes.py\nasync pipeline nodes"]
        STATE["agent/state.py\nReportState (TypedDict)"]
        PROMPTS["agent/prompts.py\nArabic / magazine prompts"]
        LLM["agent/llm.py\nLangChain LLM layer"]
    end

    subgraph EXT["Domain + Providers"]
        LEGACY["_legacy.py → original module\nfetch · filter · enhance · PDF · usage"]
        BEDROCK["AWS Bedrock\nChatBedrockConverse"]
        AZURE["Azure Anthropic\nChatAnthropic (fallback)"]
    end

    DRV -->|astream ReportState| DG & PG & MG
    DG & PG & MG --> NODES
    NODES --> STATE
    NODES --> PROMPTS
    NODES --> LLM
    NODES --> LEGACY
    LLM --> BEDROCK
    LLM -.fallback.-> AZURE
    PROMPTS --> LEGACY
```

---

## 2. The workflow graph (shared shape)

All three graphs share the same topology, built by `graphs._build()`. A node
that sets `state["error"]` short-circuits straight to `END`.

```mermaid
flowchart LR
    START([START]) --> FETCH[fetch]
    FETCH --> FILTER[filter]
    FILTER --> ENHANCE[enhance]
    ENHANCE --> GENERATE[generate]
    GENERATE -->|error| END1([END])
    GENERATE -->|ok| RENDER[render]
    RENDER --> END2([END])
```

| Node | daily_graph | periodic_graph | magazine_graph |
|------|-------------|----------------|----------------|
| **fetch** | `fetch_daily` (NewsAPI + GNews + RSS) | `fetch_periodic` (RSS) | `fetch_periodic` (RSS) |
| **filter** | `filter_articles` (relevance + 60-day recency, fallback) | same | same |
| **enhance** | `enhance_daily` (≤20 articles) | `enhance_periodic` (≤8, weekly/monthly mode) | `enhance_periodic` |
| **generate** | `generate_daily` → Arabic blog markdown | `generate_periodic` → 2 themed blogs (Strategy + L&D) | `generate_magazine` → magazine JSON |
| **render** | `render_daily` → 1 PDF | `render_periodic` → up to 2 PDFs | `render_magazine` → 1 PDF |

---

## 3. State flow (`ReportState`)

A single `TypedDict` threads through every node; each node returns a partial
update that LangGraph merges.

```mermaid
flowchart LR
    subgraph S["ReportState"]
        direction TB
        I["inputs:\nreport_type · time_period\ncategory · keywords"]
        P["pipeline:\nraw_articles → articles\nenhanced_count"]
        C["content:\nblog_content / strategy_blog\nld_blog / magazine_data"]
        O["outputs:\n[{path, kind}]"]
        K["control:\nprogress · error"]
    end

    FETCH[fetch] -->|raw_articles| FILTER[filter]
    FILTER -->|articles| ENHANCE[enhance]
    ENHANCE -->|articles + enhanced_count| GENERATE[generate]
    GENERATE -->|content| RENDER[render]
    RENDER -->|outputs| BOT[bot._send_output]
    GENERATE -. progress .-> TG[Telegram message edit]
    FETCH -. progress .-> TG
    FILTER -. progress .-> TG
    ENHANCE -. progress .-> TG
```

`progress` is set by every node; `bot._run_report` reads it from each
`astream(stream_mode="values")` snapshot and live-edits the Telegram message.

---

## 4. LLM layer (`llm.py`)

Replaces the hand-rolled `call_claude_api`. Same dual-provider behavior, now via
LangChain chat models. Both `invoke_text` and `ainvoke_text` return
`(text, error)`.

```mermaid
flowchart TB
    CALL["ainvoke_text(system, user, max_tokens, temperature)"]
    CALL --> HASB{HAS_BEDROCK?}
    HASB -->|yes| BED["ChatBedrockConverse\nBEDROCK_MODEL_ID"]
    BED -->|text| OUT["(text, None)"]
    BED -->|empty / exception| HASA
    HASB -->|no| HASA{HAS_AZURE?}
    HASA -->|yes| AZ["ChatAnthropic\nAZURE_BASE_URL + AZURE_MODEL"]
    AZ -->|text| OUT
    AZ -->|error| ERR["(None, error)"]
    HASA -->|no| ERR
```

---

## 5. Request lifecycle (example: `/weekly`)

```mermaid
sequenceDiagram
    actor U as User
    participant B as bot.py
    participant G as periodic_graph
    participant N as nodes
    participant L as LLM (Bedrock→Azure)
    participant D as _legacy (domain)

    U->>B: /weekly
    B->>B: check_usage_limit("weekly")
    B->>U: "⏳ preparing..." (message)
    B->>G: astream(ReportState)
    G->>N: fetch_periodic
    N->>D: fetch_rss_quality()
    N-->>B: progress 1/4 → edit message
    G->>N: filter_articles
    N->>D: filter_relevant + recency
    N-->>B: progress 2/4 → edit
    G->>N: enhance_periodic
    N->>D: enhance_articles_with_content()
    N-->>B: progress 3/4 → edit
    G->>N: generate_periodic
    N->>D: categorize_articles()
    N->>L: ainvoke_text (Strategy)
    N->>L: ainvoke_text (L&D)
    N-->>B: progress 4/4 → edit
    G->>N: render_periodic
    N->>D: create_quality_blog_pdf() ×2
    G-->>B: final state {outputs:[...]}
    B->>U: send PDF(s) + ✅ menu
```

---

## 6. Routing & reuse

```mermaid
flowchart TB
    CB[CallbackQueryHandler\nbutton_handler]
    CB -->|get_news| NR[news_report → daily_graph]
    CB -->|generate_weekly| WR[weekly_report → periodic_graph]
    CB -->|generate_monthly| MR[monthly_report → periodic_graph]
    CB -->|generate_magazine| MGR[magazine_report → magazine_graph]
    CB -->|main_menu · category_* · pdf_* · pagination| LEG[legacy.button_handler]

    CMDS[CommandHandlers]
    CMDS -->|start · help · categories · keywords · reset · usage| LEG2[legacy handlers unchanged]
    CMDS -->|news · weekly · monthly · magazine| GRAPHS[graph-driven handlers]
```

**LLM-driven generators** run through the graphs; **non-LLM features** (menu,
category browsing, pagination, on-demand PDF buttons, keyword setup, usage/reset)
delegate to the original module so their behavior is unchanged.

---

## 7. File map

| File | Responsibility |
|------|----------------|
| `agent/bot.py` | Telegram entry point; usage gate, graph streaming, PDF sending, routing |
| `agent/graphs.py` | Compiles `daily_graph`, `periodic_graph`, `magazine_graph` |
| `agent/nodes.py` | Async pipeline nodes (fetch/filter/enhance/generate/render) |
| `agent/state.py` | `ReportState` TypedDict |
| `agent/prompts.py` | System/user prompt builders (Arabic blogs + magazine JSON) |
| `agent/llm.py` | LangChain LLM layer (Bedrock primary, Azure fallback) |
| `agent/config.py` | Env-driven config + model IDs |
| `agent/_legacy.py` | Bridge to the original module's domain functions |
| `telegram_bot_quality_arabic_claude_version.py` | Original module — reused domain logic, still runnable standalone |
```
