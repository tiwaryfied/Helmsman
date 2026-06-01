"""Pydantic schemas exchanged with the frontend."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    max_turns: int = 4


class SQLRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=10_000)
    use_cache: bool = True


class TableRef(BaseModel):
    schema_name: str
    table_name: str


class ColumnInfo(BaseModel):
    column_name: str
    data_type: str | None = None
    description: str | None = None
    ordinal_position: int | None = None


class CatalogTable(BaseModel):
    schema_name: str
    table_name: str
    columns: list[ColumnInfo] = []


class CatalogResponse(BaseModel):
    mode: str
    coral_version: str
    schemas: list[str]
    tables: list[CatalogTable]
    cache: dict[str, int]


class VoyageCreate(BaseModel):
    name: str
    description: str | None = None
    sql: str
    cadence: Literal["once", "hourly", "daily", "weekly"] = "daily"
    alert_when: str | None = None  # human-language rule, e.g. "rows > 0"


class VoyageRow(BaseModel):
    id: int
    name: str
    description: str | None
    sql: str
    cadence: str
    alert_when: str | None
    last_run_at: str | None
    last_row_count: int | None
    last_elapsed_ms: int | None
    last_status: str | None
    created_at: str


class IncidentRequest(BaseModel):
    symptom: str = Field(..., min_length=2, max_length=400)
