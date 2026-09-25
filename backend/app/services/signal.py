"""信号机业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date
from typing import Any

from app.store import store

MODULE = "signal"
REQUIRED_FIELDS = ["设备编号", "设备类型", "安装位置"]
STATUS_ORDER = ["待检修", "运用正常", "故障停用", "已更换"]
ACTION_RULES = {"确认检修": "运用正常", "登记故障": "故障停用", "更换设备": "已更换"}
NEGATIVE_ACTIONS = []

SORT_ORDERS = ("due", "due_desc")
FIELD_FILTERS = ("设备编号", "设备类型", "安装位置")


def _parse_date(value: Any) -> date | None:
    """只认 YYYY-MM-DD；空值或样例文本一律按“没有日期”处理。"""
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


def _parse_cycle_months(value: Any) -> int | None:
    """检修周期统一折算成月：支持 12、"12"、"12个月"、"1年"，取不到就返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        months = int(value)
        return months if months > 0 else None
    match = re.fullmatch(r"\s*(\d+)\s*(个月|月|年)?\s*", str(value))
    if not match:
        return None
    months = int(match.group(1)) * (12 if match.group(2) == "年" else 1)
    return months if months > 0 else None


def add_months(day: date, months: int) -> date:
    """按日历月加周期：1 月 31 日加一个月是 2 月 28 日（对齐到当月最后一天）。

    不能用 30 天近似，否则跨月（尤其跨 2 月）算出来的到期日会差一天。
    """
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def compute_due_date(row: dict[str, Any]) -> date | None:
    """下次检修日只认一个口径：上次检修日 + 检修周期；缺任一项都算不出来。"""
    last = _parse_date(row.get("上次检修日"))
    months = _parse_cycle_months(row.get("检修周期"))
    if last is None or months is None:
        return None
    return add_months(last, months)


class SignalService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        filters: dict[str, str] | None = None,
        status: str | None = None,
        sort: str = "due",
        page: int = 1,
        size: int = 20,
        today: date | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """正常队列：只放能算出下次检修日的设备，缺上次检修日的不混进来。"""
        today = today or date.today()
        rows = [row for row in store.rows(MODULE) if compute_due_date(row) is not None]
        rows = self._apply_filters(rows, keyword=keyword, filters=filters, status=status)
        rows = self._sort_rows(rows, sort)
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._decorate(row, today) for row in rows[start:start + size]], total

    def list_missing(
        self,
        *,
        keyword: str | None = None,
        filters: dict[str, str] | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        today: date | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """缺上次检修日（或检修周期）的设备：单独列出，按设备编号给稳定顺序。"""
        today = today or date.today()
        rows = [row for row in store.rows(MODULE) if compute_due_date(row) is None]
        rows = self._apply_filters(rows, keyword=keyword, filters=filters, status=status)
        rows = self._sort_rows(rows, "code")
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._decorate(row, today) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None
        return self._decorate(row, date.today())

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"信号机 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于信号机可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"信号机已{action}"

    def _apply_filters(
        self,
        rows: list[dict[str, Any]],
        *,
        keyword: str | None,
        filters: dict[str, str] | None,
        status: str | None,
    ) -> list[dict[str, Any]]:
        if keyword and keyword.strip():
            rows = [row for row in rows if keyword.strip() in str(row.get("设备编号", ""))]
        for field, value in (filters or {}).items():
            if field in FIELD_FILTERS and value and value.strip():
                rows = [row for row in rows if value.strip() in str(row.get(field, ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return rows

    def _sort_rows(self, rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
        if sort == "code":
            return sorted(rows, key=self._code_key)
        # 到期日升序时超期设备（到期日早于今天）自然排在最前；
        # 同一天到期的按设备编号、再按 id 兜底，保证每次翻页、每次刷新顺序都一致。
        ordered = sorted(rows, key=lambda row: (compute_due_date(row) or date.max, *self._code_key(row)))
        if sort == "due_desc":
            ordered.reverse()
        return ordered

    @staticmethod
    def _code_key(row: dict[str, Any]) -> tuple[str, int]:
        return str(row.get("设备编号", "")), int(row.get("id", 0))

    @staticmethod
    def _decorate(row: dict[str, Any], today: date) -> dict[str, Any]:
        """返回前重算下次检修日并标注是否超期，不改动库里的原始记录。"""
        item = dict(row)
        due = compute_due_date(row)
        item["下次检修日"] = due.isoformat() if due else ""
        item["overdue"] = bool(due and due < today)
        return item
