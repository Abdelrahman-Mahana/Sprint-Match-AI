from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, SmallInteger, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base, TimestampMixin, uuid_pk


class CompanyModel(TimestampMixin, Base):
    """Tenant boundary. Every top-level entity references a company."""

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64))
    working_weekdays: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger))

    __table_args__ = (
        CheckConstraint("cardinality(working_weekdays) > 0", name="working_weekdays_not_empty"),
    )


class DepartmentModel(Base):
    __tablename__ = "departments"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))

    __table_args__ = (UniqueConstraint("company_id", "name"),)


class TeamModel(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = uuid_pk()
    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))

    __table_args__ = (UniqueConstraint("department_id", "name"),)


class CalendarHolidayModel(Base):
    """Company-wide non-working days."""

    __tablename__ = "calendar_holidays"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    holiday_date: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(120))

    __table_args__ = (UniqueConstraint("company_id", "holiday_date"),)
