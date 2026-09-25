"""信号机接口：维护信号机，覆盖确认检修、登记故障、更换设备等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.signal import SORT_ORDERS, SignalService

router = APIRouter(prefix="/api/signal", tags=["信号机"])

service = SignalService()

LIST_FIELDS = ["设备编号", "设备类型", "安装位置", "显示制式", "所属区段", "上次检修日", "检修周期", "下次检修日", "设备状态"]
STATUSES = ["待检修", "运用正常", "故障停用", "已更换"]


def _check_page(page: int, size: int) -> None:
    if page < 1 or size < 1:
        raise HTTPException(status_code=400, detail="页码与每页条数都必须是正整数")
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")


def _check_sort(sort: str) -> None:
    if sort not in SORT_ORDERS:
        raise HTTPException(status_code=400, detail=f"排序方式「{sort}」不支持，可选：{'、'.join(SORT_ORDERS)}")


def _field_filters(device_code: str | None, device_type: str | None, location: str | None) -> dict[str, str]:
    filters: dict[str, str] = {}
    if device_code:
        filters["设备编号"] = device_code
    if device_type:
        filters["设备类型"] = device_type
    if location:
        filters["安装位置"] = location
    return filters


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按设备编号模糊检索"),
    device_code: str | None = Query(default=None, alias="设备编号", description="按设备编号检索"),
    device_type: str | None = Query(default=None, alias="设备类型", description="按设备类型检索"),
    location: str | None = Query(default=None, alias="安装位置", description="按安装位置检索"),
    status: str | None = Query(default=None, description="待检修、运用正常、故障停用、已更换"),
    sort: str = Query(default="due", description="due=超期优先、到期日近到远；due_desc=到期日远到近"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """正常检修队列：下次检修日按上次检修日+检修周期重算，超期排前、同日按设备编号；
    缺上次检修日的设备不混在这里，走 /api/signal/missing。没有数据时返回空页，不报错。"""
    _check_page(page, size)
    _check_sort(sort)
    items, total = service.list_entries(
        keyword=keyword,
        filters=_field_filters(device_code, device_type, location),
        status=status,
        sort=sort,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/missing", response_model=PageResult[dict])
def list_missing(
    keyword: str | None = Query(default=None, description="按设备编号模糊检索"),
    device_code: str | None = Query(default=None, alias="设备编号", description="按设备编号检索"),
    device_type: str | None = Query(default=None, alias="设备类型", description="按设备类型检索"),
    location: str | None = Query(default=None, alias="安装位置", description="按安装位置检索"),
    status: str | None = Query(default=None, description="待检修、运用正常、故障停用、已更换"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """缺上次检修日（或检修周期）的设备单独列出，不混入正常检修队列。"""
    _check_page(page, size)
    items, total = service.list_missing(
        keyword=keyword,
        filters=_field_filters(device_code, device_type, location),
        status=status,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    keyword: str | None = Query(default=None, description="按设备编号模糊检索"),
    device_code: str | None = Query(default=None, alias="设备编号", description="按设备编号检索"),
    device_type: str | None = Query(default=None, alias="设备类型", description="按设备类型检索"),
    location: str | None = Query(default=None, alias="安装位置", description="按安装位置检索"),
    status: str | None = Query(default=None, description="待检修、运用正常、故障停用、已更换"),
    sort: str = Query(default="due", description="与列表页一致的排序口径"),
) -> dict[str, Any]:
    """导出信号机清单：与列表页同一套过滤、重算与排序口径，方便两个入口互相核对。"""
    _check_sort(sort)
    filters = _field_filters(device_code, device_type, location)
    items, total = service.list_entries(keyword=keyword, filters=filters, status=status, sort=sort, page=1, size=10000)
    missing, missing_total = service.list_missing(keyword=keyword, filters=filters, status=status, page=1, size=10000)
    return {"module": "signal", "total": total, "items": items, "missing_total": missing_total, "missing_items": missing}


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
