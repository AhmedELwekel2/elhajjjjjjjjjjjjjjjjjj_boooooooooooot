"""StateGraph definitions — one workflow per report type.

Each graph is the deterministic pipeline:

    fetch → filter → enhance → generate → render → END

with an error short-circuit that routes straight to END if a node sets
``state["error"]``. Graphs are compiled once at import.
"""
from langgraph.graph import END, StateGraph

from . import nodes
from .state import ReportState


def _route_after_generate(state: ReportState) -> str:
    return "end" if state.get("error") else "render"


def _build(fetch, filter_, enhance, generate, render):
    g = StateGraph(ReportState)
    g.add_node("fetch", fetch)
    g.add_node("filter", filter_)
    g.add_node("enhance", enhance)
    g.add_node("generate", generate)
    g.add_node("render", render)

    g.set_entry_point("fetch")
    g.add_edge("fetch", "filter")
    g.add_edge("filter", "enhance")
    g.add_edge("enhance", "generate")
    g.add_conditional_edges("generate", _route_after_generate, {"render": "render", "end": END})
    g.add_edge("render", END)
    return g.compile()


daily_graph = _build(
    nodes.fetch_daily, nodes.filter_articles, nodes.enhance_daily,
    nodes.generate_daily, nodes.render_daily,
)

periodic_graph = _build(
    nodes.fetch_periodic, nodes.filter_articles, nodes.enhance_periodic,
    nodes.generate_periodic, nodes.render_periodic,
)

magazine_graph = _build(
    nodes.fetch_periodic, nodes.filter_articles, nodes.enhance_periodic,
    nodes.generate_magazine, nodes.render_magazine,
)
