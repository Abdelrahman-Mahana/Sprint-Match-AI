"""Write the compiled agent graph as Mermaid (LangGraph's own renderer) to docs/agent_graph.mmd.

Usage:  uv run python -m scripts.export_graph
"""

from __future__ import annotations

from pathlib import Path

from app.agents.graph import mermaid

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    target = ROOT / "docs" / "agent_graph.mmd"
    target.write_text(mermaid(), encoding="utf-8")
    print(f"Wrote {target}")
