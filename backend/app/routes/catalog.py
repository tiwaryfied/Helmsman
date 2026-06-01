"""Schema Atlas — exposes Coral's catalog to the UI."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import store
from ..auth import current_user
from ..coral_client import CORAL
from ..schemas import CatalogResponse, CatalogTable, ColumnInfo
from ..agent import fetch_catalog

router = APIRouter(
    prefix="/api/catalog",
    tags=["catalog"],
    dependencies=[Depends(current_user)],
)


@router.get("", response_model=CatalogResponse)
async def catalog(user: dict = Depends(current_user)) -> CatalogResponse:
    cat = await fetch_catalog()
    tables: list[CatalogTable] = []
    for schema, ts in cat.items():
        for t in ts:
            tables.append(CatalogTable(
                schema_name=schema,
                table_name=t["table_name"],
                columns=[ColumnInfo(**c) for c in t["columns"]],
            ))
    coral_v = await CORAL.coral_version()
    conns = store.connections_list(int(user["id"]))
    per_source_mode = "live" if any(c["status"] == "live" for c in conns) else "demo"
    return CatalogResponse(
        mode=per_source_mode,
        coral_version=coral_v,
        schemas=sorted(cat.keys()),
        tables=tables,
        cache=CORAL.cache_stats(),
    )
