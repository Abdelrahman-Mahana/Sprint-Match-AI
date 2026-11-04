"""Default compiled agent graph (no checkpointer) for introspection, diagrams and simple embedding.

Runtime execution goes through ``AgentRuntime`` (``app.agents.runtime``), which compiles the same builder with
the configured checkpointer and retry policy.
"""

from __future__ import annotations

from app.agents.builder import AgentGraph, build_graph, mermaid

recommendation_graph: AgentGraph = build_graph()

__all__ = ["AgentGraph", "build_graph", "mermaid", "recommendation_graph"]
