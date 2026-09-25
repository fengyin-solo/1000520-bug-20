"""信号机接口：维护信号机，覆盖确认检修、登记故障、更换设备等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.signal import LIST_FIELDS, SignalService

router = APIRouter(prefix="/api/signal", tags=["信号机"])

service = SignalService()

STATUSES = ["待检修", "运用正常", "故障停用", "已更换"]
SORTS = {"due_asc", "due_desc"}


def _parse_sort(value: str | None) -> str:
    """排序参数只认到期日升/降序；非法值回落到默认升序，不把请求打成 400。"""
    return value if value in SORTS else "due_asc"


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按设备编号检索（忽略空白与大小写）"),
    status: str | None = Query(default=None, description="待检修、运用正常、故障停用、已更换"),
    page: int = 1,
    size: int = 20,
    sort: str | None = Query(default=None, description="due_asc（默认）或 due_desc，超期始终最前"),
) -> PageResult[dict]:
    """按设备编号与状态过滤信号机列表；没有数据时返回空页，不报错。

    到期日由后端按上次检修日与检修周期统一重算；缺上次检修日的设备放在
    unscheduled_items 里单独列出，不混入正常队列。
    """
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total, meta, unscheduled = service.list_entries(
        keyword=keyword,
        status=status,
        page=page,
        size=size,
        sort=_parse_sort(sort),
    )
    return PageResult(
        items=items,
        total=total,
        page=max(page, 1),
        size=size,
        overdue=meta["overdue"],
        unscheduled=meta["unscheduled"],
        unscheduled_items=unscheduled,
        sort=meta["sort"],
        today=meta["today"],
    )


@router.get("/export")
def export_entries(
    keyword: str | None = Query(default=None, description="按设备编号检索（忽略空白与大小写）"),
    status: str | None = Query(default=None, description="待检修、运用正常、故障停用、已更换"),
    sort: str | None = Query(default=None, description="due_asc（默认）或 due_desc，超期始终最前"),
) -> dict[str, Any]:
    """导出信号机清单：沿用当前检索条件与到期排序，待排期设备单独分组，便于其他入口核对。"""
    items, total, meta, unscheduled = service.list_entries(
        keyword=keyword,
        status=status,
        page=1,
        size=10000,
        sort=_parse_sort(sort),
    )
    return {
        "module": "signal",
        "total": total,
        "overdue": meta["overdue"],
        "unscheduled": meta["unscheduled"],
        "sort": meta["sort"],
        "today": meta["today"],
        "fields": LIST_FIELDS,
        "items": items,
        "unscheduled_items": unscheduled,
    }


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条信号机明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"信号机 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条信号机，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="信号机已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条信号机执行确认检修、登记故障、更换设备；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
