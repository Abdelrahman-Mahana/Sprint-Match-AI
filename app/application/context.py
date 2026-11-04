from __future__ import annotations

from dataclasses import dataclass

from app.domain.entities.organization import Company
from app.domain.enums import Role


@dataclass(frozen=True)
class RequestContext:
    """Who is asking, for which tenant. Every service call is scoped to ``company``."""

    company: Company
    actor: str
    role: Role
    request_id: str
