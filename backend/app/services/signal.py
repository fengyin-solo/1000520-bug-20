"""信号机业务规则：状态流转、字段校验、到期重算、筛选排序口径都收在这里。"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.services import schedule
from app.store import store

MODULE = "signal"
REQUIRED_FIELDS = ["设备编号", "设备类型", "安装位置"]
STATUS_ORDER = ["待检修", "运用正常", "故障停用", "已更换"]
ACTION_RULES = {"确认检修": "运用正常", "登记故障": "故障停用", "更换设备": "已更换"}
NEGATIVE_ACTIONS = []

# 列表/导出的字段口径：检修周期是计算下次检修日的入参，不能再只看登记值。
LIST_FIELDS = [
    "设备编号", "设备类型", "安装位置", "显示制式", "所属区段",
    "上次检修日", "检修周期", "下次检修日", "到期日", "到期状态", "到期核对", "设备状态",
]


class SignalService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        sort: str = "due_asc",
        today: date | None = None,
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any], list[dict[str, Any]]]:
        """返回 (正常队列当前页, 正常队列总数, 统计信息, 待排期设备全量)。

        - 每次读取都按「上次检修日 + 检修周期」重算到期日，登记的下次检修日只用于核对；
        - 缺上次检修日（或周期无法识别）的设备进待排期清单，绝不混进正常队列；
        - 正常队列按到期日排序：超期优先，同到期日用设备编号、id 稳定兜底。
        """
        ref_today = today or date.today()
        annotated = [
            schedule.annotate_due(dict(row), today=ref_today)
            for row in store.rows(MODULE)
        ]

        def matches(row: dict[str, Any]) -> bool:
            if keyword and not schedule.code_matches(row.get("设备编号"), keyword):
                return False
            if status and row.get("status") != status:
                return False
            return True

        filtered = [row for row in annotated if matches(row)]
        unscheduled = [row for row in filtered if row.get("待排期")]
        scheduled = [row for row in filtered if not row.get("待排期")]

        desc = sort == "due_desc"
        scheduled = schedule.sort_by_due(scheduled, today=ref_today, desc=desc)

        total = len(scheduled)
        page_no = max(page, 1)
        start = (page_no - 1) * size
        page_rows = scheduled[start:start + size]

        overdue_count = sum(1 for row in scheduled if row.get("超期"))
        meta = {
            "overdue": overdue_count,
            "unscheduled": len(unscheduled),
            "sort": "due_desc" if desc else "due_asc",
            "today": ref_today.isoformat(),
        }
        return page_rows, total, meta, unscheduled

    def get_entry(self, entry_id: int, today: date | None = None) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None
        # 详情与列表用同一套口径，保证两边核对结果一致。
        return schedule.annotate_due(dict(row), today=today or date.today())

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

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        today: date | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
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
        # 确认检修：以今天为新的上次检修日，到期日交给读取时统一重算，
        # 老的动作入口、动作名与提示语照旧，不额外要求前端传参。
        if action == "确认检修":
            ref_today = today or date.today()
            entry["上次检修日"] = ref_today.isoformat()
            entry.pop("下次检修日", None)
        return entry, f"信号机已{action}"
