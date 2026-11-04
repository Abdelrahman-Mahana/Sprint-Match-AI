"""Event-loop factory for uvicorn on Windows.

psycopg's async mode (used by LangGraph's ``AsyncPostgresSaver``) cannot run on Windows' default
``ProactorEventLoop``. Start the API with::

    uvicorn app.main:create_app --factory --loop app.core.event_loop:selector_loop_factory

On Linux/macOS (and in Docker) the default loop already works.
"""

from __future__ import annotations

import asyncio


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Called by uvicorn for a custom ``--loop`` import string: must return a new event loop."""
    return asyncio.SelectorEventLoop()
