from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Immutable, validated domain value object."""

    model_config = ConfigDict(frozen=True, extra="forbid")
