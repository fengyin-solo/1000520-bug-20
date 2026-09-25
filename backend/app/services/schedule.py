"""检修到期口径（唯一事实来源）。

信号机以及后续其他设备入口（转辙机、轨道电路、联锁等）的「下次检修日」都必须走这里的
规则，保证不同列表、导出、详情核对出来的到期日完全一致：

- 日期一律按日历日解析与计算，不按 30 天估算月长，跨月不会再差一天；
- 检修周期支持「3个月 / 6月 / 1年 / 30天 / 90日」，纯数字按「月」处理；
- 超期 = 到期日早于今天（今天到期不算超期）；
- 到期排序：超期的排在最前（超期越久越靠前），其余按到期日先后；
  同一天到期时用「设备编号、id」做稳定的次序，避免每次翻页都错位。
"""
from __future__ import annotations

from datetime import date, timedelta
import re
from typing import Any, Iterable, Literal

Cycle = tuple[Literal["months", "days"], int]

DATE_FMT = "%Y-%m-%d"


def parse_iso_date(value: Any) -> date | None:
    """严格解析 YYYY-MM-DD。

    datetime.strptime 会把 2026-9-1、20260901 之类也吃进去，这里要求四位、两位、两位
    且确实是合法日历日（2 月 30 日会被拒掉），避免脏数据参与到期计算。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None
    year_s, month_s, day_s = text[:4], text[5:7], text[8:]
    if not (year_s.isdigit() and month_s.isdigit() and day_s.isdigit()):
        return None
    try:
        return date(int(year_s), int(month_s), int(day_s))
    except ValueError:
        return None


def iso_text(value: date | None) -> str | None:
    return value.strftime(DATE_FMT) if value is not None else None


def parse_cycle(value: Any) -> Cycle | None:
    """解析检修周期。

    认「3个月 / 6月 / 一年 / 90天 / 30日」这类写法，也认「3」这种纯数字（按月）。
    解析不出来或周期非正数时返回 None——调用方按「待排期」处理，不能硬算。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        amount = int(value)
        return ("months", amount) if amount > 0 else None
    text = str(value).strip()
    if not text:
        return None
    digits = ""
    for ch in text:
        if ch.isdigit():
            digits += ch
        else:
            break
    if not digits:
        return None
    amount = int(digits)
    if amount <= 0:
        return None
    unit = text[len(digits):].strip()
    if not unit:
        return ("months", amount)
    if unit in ("月", "个月", "M", "m"):
        return ("months", amount)
    if unit in ("年", "周年", "岁", "Y", "y"):
        return ("months", amount * 12)
    if unit in ("天", "日", "D", "d"):
        return ("days", amount)
    return None


def add_calendar_months(base: date, months: int) -> date:
    """按日历加整月数。

    1 月 31 日加 1 个月落在 2 月的最后一天（截断），而不是 3 月 3 日；
    用「同月同日、越界取月末」保证 1 月 31 → 2 月 28、12 月 31 → 1 月 31 这类
    跨月、跨年场景都不会差一天。
    """
    total = base.year * 12 + (base.month - 1) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    # 下个月的第 0 天就是本月最后一天，自动处理大小月与闰年；12 月要进位到次年 1 月。
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    last_day = (date(next_year, next_month, 1) - timedelta(days=1)).day
    return date(year, month, min(base.day, last_day))


def next_due_date(last_date: date | None, cycle_value: Any) -> date | None:
    """按上次检修日 + 检修周期重算下次检修日；缺任一口径都算不出来。"""
    if last_date is None:
        return None
    cycle = parse_cycle(cycle_value)
    if cycle is None:
        return None
    unit, amount = cycle
    if unit == "months":
        return add_calendar_months(last_date, amount)
    return last_date + timedelta(days=amount)


def is_overdue(due: date | None, today: date) -> bool:
    """到期日早于今天才算超期；今天到期仍在期限内。"""
    return due is not None and due < today


def normalize_code(value: Any) -> str:
    """设备编号归一化：去掉空白与连字符等分隔符并转大写。

    SIGN-0001、sign 0001、SIGN_0001 归并成同一个 SIGN0001，避免「检索经常对不上是哪一台」。
    """
    return re.sub(r"[\s\-_/]", "", str(value or "")).upper()


def code_matches(code: Any, keyword: str) -> bool:
    """按设备编号检索：归一化后做包含匹配，空关键字视为不限制。"""
    needle = normalize_code(keyword)
    if not needle:
        return True
    return needle in normalize_code(code)


def due_sort_key(row: dict[str, Any], today: date, desc: bool) -> tuple[Any, ...]:
    """到期排序键。

    - 超期的永远在最前，超期越久越靠前（升序、降序都如此，这是业务硬规则）；
    - 其余按到期日先后，desc 时远的在前；
    - 同一天到期，用归一化设备编号、再用 id 兜底，次序稳定可复现。
    """
    due = parse_iso_date(row.get("到期日"))
    code = normalize_code(row.get("设备编号"))
    entry_id = int(row.get("id", 0) or 0)
    if due is not None and is_overdue(due, today):
        # 超期的永远在最前，且超期越久（到期日越早）越靠前，与 desc 无关。
        return (0, due, code, entry_id)
    if due is None:
        # 理论上待排期数据不会进入正常队列，兜底放到最后。
        return (2, date.max, code, entry_id)
    return (1, due if not desc else -due.toordinal(), code, entry_id)


def sort_by_due(
    rows: Iterable[dict[str, Any]],
    *,
    today: date,
    desc: bool = False,
) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: due_sort_key(row, today, desc))


def annotate_due(
    row: dict[str, Any],
    *,
    today: date,
    last_field: str = "上次检修日",
    cycle_field: str = "检修周期",
    stored_next_field: str = "下次检修日",
) -> dict[str, Any]:
    """在不改动原始登记字段的前提下，给一条记录补上统一的到期口径字段。

    输出字段：
    - 到期日：按「上次检修日 + 检修周期」重算出的权威下次检修日（ISO 文本）；
    - 超期 / 到期状态：是否已超期（到期日 < 今天）；
    - 到期核对：与原登记的下次检修日比对的结论，供其他入口的列表核对；
    - 待排期 / 待排期原因：缺上次检修日或周期无法识别时给出，这类设备不进正常队列。
    """
    last_date = parse_iso_date(row.get(last_field))
    cycle_text = row.get(cycle_field)
    due = next_due_date(last_date, cycle_text)

    annotated = dict(row)
    if due is None:
        annotated["待排期"] = True
        if last_date is None and not str(row.get(last_field) or "").strip():
            reason = "缺上次检修日"
        elif last_date is None:
            reason = f"上次检修日格式应为 YYYY-MM-DD：{row.get(last_field)!r}"
        elif parse_cycle(cycle_text) is None:
            reason = f"检修周期无法识别：{cycle_text!r}"
        else:  # pragma: no cover - 理论上走不到，留作兜底
            reason = "到期口径不完整"
        annotated["待排期原因"] = reason
        annotated["到期日"] = None
        annotated["超期"] = False
        annotated["到期状态"] = "待排期"
        annotated["到期核对"] = ""
        return annotated

    annotated["待排期"] = False
    annotated["待排期原因"] = ""
    annotated["到期日"] = iso_text(due)
    overdue = is_overdue(due, today)
    annotated["超期"] = overdue
    annotated["到期状态"] = "超期" if overdue else ("今日到期" if due == today else "正常")

    stored_next = parse_iso_date(row.get(stored_next_field))
    if stored_next is None:
        annotated["到期核对"] = (
            "未登记"
            if not str(row.get(stored_next_field) or "").strip()
            else f"登记值不是有效日期，应以 {iso_text(due)} 为准"
        )
    elif stored_next == due:
        annotated["到期核对"] = "一致"
    else:
        annotated["到期核对"] = f"不一致，应为 {iso_text(due)}"
    return annotated
