"""到期口径单元测试：只依赖标准库，直接 python3 -m unittest 即可跑。"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import schedule  # noqa: E402

TODAY = date(2026, 9, 25)


class ParseIsoDateTest(unittest.TestCase):
    def test_valid_dates(self):
        self.assertEqual(schedule.parse_iso_date("2026-09-25"), date(2026, 9, 25))
        self.assertEqual(schedule.parse_iso_date("2026-02-28"), date(2026, 2, 28))

    def test_rejects_loose_formats(self):
        for value in ("2026-9-1", "20260925", "2026/09/25", "", None, 20260925):
            self.assertIsNone(schedule.parse_iso_date(value), value)

    def test_rejects_impossible_calendar_days(self):
        # 这正是「跨月差一天」问题的根源之一：2 月没有 30、31 日。
        self.assertIsNone(schedule.parse_iso_date("2026-02-30"))
        self.assertIsNone(schedule.parse_iso_date("2026-02-31"))
        self.assertIsNone(schedule.parse_iso_date("2026-13-01"))

    def test_leap_day_accepted(self):
        self.assertEqual(schedule.parse_iso_date("2028-02-29"), date(2028, 2, 29))
        self.assertIsNone(schedule.parse_iso_date("2026-02-29"))


class ParseCycleTest(unittest.TestCase):
    def test_month_variants(self):
        self.assertEqual(schedule.parse_cycle("3个月"), ("months", 3))
        self.assertEqual(schedule.parse_cycle("6月"), ("months", 6))
        self.assertEqual(schedule.parse_cycle("2"), ("months", 2))
        self.assertEqual(schedule.parse_cycle(3), ("months", 3))

    def test_year_and_days(self):
        self.assertEqual(schedule.parse_cycle("1年"), ("months", 12))
        self.assertEqual(schedule.parse_cycle("2周年"), ("months", 24))
        self.assertEqual(schedule.parse_cycle("30天"), ("days", 30))
        self.assertEqual(schedule.parse_cycle("90日"), ("days", 90))

    def test_invalid(self):
        for value in ("", None, "0个月", "-1月", "三个月", "abc"):
            self.assertIsNone(schedule.parse_cycle(value), value)


class NextDueDateTest(unittest.TestCase):
    def due(self, last, cycle):
        return schedule.next_due_date(schedule.parse_iso_date(last), cycle)

    def test_cross_month_plain(self):
        self.assertEqual(self.due("2026-08-25", "1个月"), date(2026, 9, 25))
        self.assertEqual(self.due("2026-06-20", "3个月"), date(2026, 9, 20))

    def test_cross_month_end_clamped_not_spilled(self):
        # 月末加月必须截断到当月最后一天，不能多算到次月（这就是差一天的 bug）。
        self.assertEqual(self.due("2026-01-31", "1个月"), date(2026, 2, 28))
        self.assertEqual(self.due("2025-12-31", "2个月"), date(2026, 2, 28))
        self.assertEqual(self.due("2026-01-31", "3个月"), date(2026, 4, 30))
        self.assertEqual(self.due("2026-01-31", "1年"), date(2027, 1, 31))
        self.assertEqual(self.due("2028-01-31", "1个月"), date(2028, 2, 29))

    def test_cross_year(self):
        self.assertEqual(self.due("2026-12-31", "2个月"), date(2027, 2, 28))
        self.assertEqual(self.due("2026-11-30", "6个月"), date(2027, 5, 30))

    def test_days(self):
        self.assertEqual(self.due("2026-08-20", "90天"), date(2026, 11, 18))
        self.assertEqual(self.due("2026-08-26", "30天"), date(2026, 9, 25))

    def test_missing_inputs(self):
        self.assertIsNone(schedule.next_due_date(None, "3个月"))
        self.assertIsNone(schedule.next_due_date(TODAY, None))
        self.assertIsNone(schedule.next_due_date(TODAY, "三个月"))


class OverdueTest(unittest.TestCase):
    def test_boundary_is_due_today(self):
        self.assertTrue(schedule.is_overdue(date(2026, 9, 24), TODAY))
        self.assertFalse(schedule.is_overdue(date(2026, 9, 25), TODAY))
        self.assertFalse(schedule.is_overdue(date(2026, 9, 26), TODAY))
        self.assertFalse(schedule.is_overdue(None, TODAY))


class CodeMatchTest(unittest.TestCase):
    def test_normalization(self):
        self.assertTrue(schedule.code_matches("SIGN-0001", "sign 0001"))
        self.assertTrue(schedule.code_matches("sign 0002", "SIGN-0002"))
        self.assertTrue(schedule.code_matches("SIGN-0001", " sign-0001 "))
        self.assertFalse(schedule.code_matches("SIGN-0001", "SIGN-0002"))
        # 空关键字不过滤。
        self.assertTrue(schedule.code_matches("SIGN-0001", ""))


class SortTest(unittest.TestCase):
    def row(self, row_id, code, due):
        return {"id": row_id, "设备编号": code, "到期日": due}

    def test_overdue_first_even_desc(self):
        rows = [
            self.row(1, "SIGN-0010", "2026-10-01"),
            self.row(2, "SIGN-0001", "2026-09-20"),  # 超期
            self.row(3, "SIGN-0005", "2026-09-26"),
            self.row(4, "SIGN-0002", "2025-12-31"),  # 超期最久
        ]
        ordered = schedule.sort_by_due(rows, today=TODAY)
        self.assertEqual([r["id"] for r in ordered], [4, 2, 3, 1])

        ordered_desc = schedule.sort_by_due(rows, today=TODAY, desc=True)
        # 降序只影响未超期部分；超期的仍然在最前、超期越久越靠前。
        self.assertEqual([r["id"] for r in ordered_desc], [4, 2, 1, 3])

    def test_same_due_tie_is_stable(self):
        rows_a = [
            self.row(1, "SIGN-0011", "2026-09-24"),
            self.row(2, "SIGN-0004", "2026-09-24"),
        ]
        rows_b = list(reversed(rows_a))
        # 同一天到期：按设备编号（再按 id）排，输入顺序不影响结果，翻页不会错位。
        self.assertEqual(
            [r["id"] for r in schedule.sort_by_due(rows_a, today=TODAY)],
            [r["id"] for r in schedule.sort_by_due(rows_b, today=TODAY)],
        )
        self.assertEqual(
            [r["id"] for r in schedule.sort_by_due(rows_b, today=TODAY)],
            [2, 1],
        )


class AnnotateTest(unittest.TestCase):
    def test_consistent_and_mismatch(self):
        ok = schedule.annotate_due(
            {"id": 1, "设备编号": "SIGN-0001", "上次检修日": "2026-06-20", "检修周期": "3个月", "下次检修日": "2026-09-20"},
            today=TODAY,
        )
        self.assertEqual(ok["到期日"], "2026-09-20")
        self.assertEqual(ok["到期核对"], "一致")
        self.assertEqual(ok["到期状态"], "超期")

        bad = schedule.annotate_due(
            {"id": 2, "设备编号": "SIGN-0010", "上次检修日": "2026-09-01", "检修周期": "1个月", "下次检修日": "2026-10-10"},
            today=TODAY,
        )
        self.assertEqual(bad["到期日"], "2026-10-01")
        self.assertEqual(bad["到期核对"], "不一致，应为 2026-10-01")

    def test_today_due_is_not_overdue(self):
        row = schedule.annotate_due(
            {"id": 3, "设备编号": "SIGN-0003", "上次检修日": "2026-06-25", "检修周期": "3个月", "下次检修日": "2026-09-25"},
            today=TODAY,
        )
        self.assertEqual(row["到期状态"], "今日到期")
        self.assertFalse(row["超期"])

    def test_missing_last_date_goes_unscheduled(self):
        row = schedule.annotate_due(
            {"id": 12, "设备编号": "SIGN-0012", "上次检修日": "", "检修周期": "3个月", "下次检修日": ""},
            today=TODAY,
        )
        self.assertTrue(row["待排期"])
        self.assertEqual(row["待排期原因"], "缺上次检修日")
        self.assertIsNone(row["到期日"])

    def test_bad_cycle_goes_unscheduled(self):
        row = schedule.annotate_due(
            {"id": 13, "设备编号": "SIGN-0013", "上次检修日": "2026-08-26", "检修周期": "", "下次检修日": ""},
            today=TODAY,
        )
        self.assertTrue(row["待排期"])
        self.assertIn("检修周期", row["待排期原因"])


if __name__ == "__main__":
    unittest.main()
