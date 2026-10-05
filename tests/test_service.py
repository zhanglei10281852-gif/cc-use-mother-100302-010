"""服务层集成测试：配置版本隔离、管理层对比、结论追溯与持久化。"""

import tempfile
import unittest
from pathlib import Path

from product_portfolio.models import SegmentFilter
from product_portfolio.service import PortfolioService
from product_portfolio.store import JsonStore
from support import FakeClock, make_service, rec

ADOPTION = {"verified": True, "evidence": "合同", "units": 3}


class ConfigVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service, self.clock = make_service()
        self.service.import_records(
            [
                rec("R1", version="A1", details={"channel": "trial", "summary": "A1 试装抖动"}),
                rec("R2", version="A2", details={"channel": "trial", "summary": "A2 试装平稳"}),
            ]
        )

    def test_upgrade_keeps_old_feedback_on_original_version(self) -> None:
        a1 = self.service.timeline("P1", SegmentFilter(config_version="A1"))
        a2 = self.service.timeline("P1", SegmentFilter(config_version="A2"))
        self.assertEqual([e["record_id"] for e in a1], ["R1"])
        self.assertEqual([e["record_id"] for e in a2], ["R2"])
        metrics_a1 = self.service.metrics("P1", SegmentFilter(config_version="A1"))
        self.assertEqual(metrics_a1["total_records"], 1)

    def test_timeline_merges_all_kinds_in_order(self) -> None:
        self.service.import_records(
            [
                rec("R3", kind="lost_order", occurred="2026-02-01T08:00:00+00:00", details={"reason": "价格"}),
                rec("R4", kind="field_issue", occurred="2026-04-01T08:00:00+00:00",
                    details={"severity": "low", "status": "open", "summary": "异响"}),
            ]
        )
        timeline = self.service.timeline("P1")
        # R3(02-01) < R1(03-01) = R2(03-01，同刻按入库序号) < R4(04-01)
        self.assertEqual([e["record_id"] for e in timeline], ["R3", "R1", "R2", "R4"])
        self.assertEqual({e["kind"] for e in timeline}, {"feedback", "lost_order", "field_issue"})


class CompareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service, self.clock = make_service()
        self.service.import_records(
            [
                rec("R1", kind="opportunity", details={"expected_units": 10}),
                rec("R2", kind="adoption", stage="首单", details=ADOPTION),
                rec("R3", industry="风电", kind="lost_order", details={"reason": "工况不匹配"}),
                rec("R4", product="P2", kind="opportunity", details={"expected_units": 6}),
            ]
        )

    def test_compare_groups_by_industry(self) -> None:
        result = self.service.compare(group_by="industry")
        rows = {(row["product_code"], row["group"]): row for row in result["rows"]}
        self.assertIn(("P1", "机器人"), rows)
        self.assertIn(("P1", "风电"), rows)
        self.assertIn(("P2", "机器人"), rows)
        robot = rows[("P1", "机器人")]
        self.assertEqual(robot["metrics"]["verified_adoptions"], 1)
        self.assertEqual(robot["metrics"]["evidence"]["verified_adoptions"], ["R2"])
        wind = rows[("P1", "风电")]
        self.assertEqual(wind["metrics"]["lost_orders"], 1)
        self.assertNotEqual(robot["metrics"]["total_records"], wind["metrics"]["total_records"])

    def test_compare_rejects_unknown_grouping(self) -> None:
        with self.assertRaises(ValueError):
            self.service.compare(group_by="region")

    def test_compare_with_scope_filter(self) -> None:
        result = self.service.compare(group_by="industry", scope=SegmentFilter(industry="风电"))
        rows = {(row["product_code"], row["group"]): row for row in result["rows"]}
        self.assertEqual(rows[("P1", "风电")]["metrics"]["lost_orders"], 1)
        # 过滤后无记录的产品保留占位行，便于管理层区分“无数据”与“被遗漏”
        self.assertEqual(rows[("P2", None)]["metrics"]["total_records"], 0)
        self.assertIsNone(rows[("P2", None)]["fit_score"])


class FindingEvidenceTests(unittest.TestCase):
    def test_trace_conclusion_to_records(self) -> None:
        service, _clock = make_service()
        service.import_records([rec("R1"), rec("R2", kind="adoption", details=ADOPTION)])
        review = service.freeze_review("P1", "G1")
        finding = service.add_finding(review.review_id, "fact", "试装稳定且已有验证采用", evidence=["R1", "R2"])
        trace = service.finding_evidence(finding.finding_id)
        self.assertEqual(trace["review_id"], review.review_id)
        self.assertEqual([r["record_id"] for r in trace["records"]], ["R1", "R2"])
        self.assertTrue(all(r["fingerprint_match"] for r in trace["records"]))


class PersistenceTests(unittest.TestCase):
    def test_reload_keeps_state_and_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "store.json")
            clock = FakeClock()
            service = PortfolioService(JsonStore(path, clock=clock))
            service.register_product("P1", "产品一")
            service.register_config_version("P1", "A1")
            batch = [rec("R1"), rec("R2", kind="adoption", details=ADOPTION)]
            service.import_records(batch)
            service.freeze_review("P1", "G1")

            reopened = PortfolioService(JsonStore(path, clock=clock))
            again = reopened.import_records(batch)  # 重启后重复导入仍然幂等
            self.assertEqual(again.imported, [])
            self.assertEqual(sorted(again.duplicates), ["R1", "R2"])
            self.assertEqual(reopened.metrics("P1"), service.metrics("P1"))
            view = reopened.review_view("RV-P1-G1-v1")
            self.assertEqual(len(view["sample"]), 2)


if __name__ == "__main__":
    unittest.main()
