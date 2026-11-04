from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import Field

from app.domain.entities.base import DomainModel


class Company(DomainModel):
    id: UUID
    code: str
    name: str
    timezone: str
    # Python weekday numbers (Monday=0 ... Sunday=6) that are normally working days.
    working_weekdays: tuple[int, ...] = Field(min_length=1)


class Holiday(DomainModel):
    holiday_date: date
    name: str


class Sprint(DomainModel):
    id: UUID
    project_id: UUID
    name: str
    start_date: date
    end_date: date
    status: str
