"""适配度统计与追溯测试。"""

import unittest

from product_portfolio.analytics import compute_metrics
from product_portfolio.models import FeedbackRecord
from support import make_record, make_service


def build_records() -> list[dict]:
    return [
        make_record("FB-A1", industry="机器人", kind="adoption", occurred="2026-01-05"),
        make_record("FB-A2", industry="机器人", kind="adoption", occurred="2026-01-12"),
        make_record("FB-L1", industry="机器人", kind="lost_order", occurred="2026-01-08"),
        make_record(
            "FB-I1",
            industry="机器人",
            kind="field_issue",
            occurred="2026-01-15",
            details={"severity": "high"},
        ),
        make_record(
            "FB-C1",
            industry="机器人",
            kind="improvement_commitment",
            occurred="2026-01-16",
            details={"owner": "产品经理", "status": "open"},
        ),
        make_record(
            "FB-C2",
            industry="机器人",
            kind="improvement_commitment",
            occurred="2026-01-17",
            details={"owner": "产品经理", "status": "done"},
        ),
        make_record("FB-W1", industry="风电", kind="adoption", occurred="2026-01-06"),
    ]


class AnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = make_service()
        self.service.import_records(build_records())

    def test_metrics_are_deterministic_and_traceable(self) -> None:
        records = [
            FeedbackRecord.from_dict(r.to_dict())
            for r in self.service.timeline("P1", industry="机器人")
        ]
        metrics = compute_metrics(records)
        self.assertEqual(metrics["verified_adoptions"], 2)
        self.assertEqual(metrics["lost_orders"], 1)
        self.assertEqual(metrics["field_issues"], 1)
        self.assertEqual(metrics["severity_weighted_issues"], 4)
        self.assertEqual(metrics["open_commitments"], 1)
        self.assertAlmostEqual(metrics["win_rate"], 2 / 3)
        self.assertAlmostEqual(metrics["issue_load"], 2.0)
        self.assertEqual(metrics["fit_score"], round(100 * 2 / 3 - 20))
        self.assertEqual(metrics["evidence"]["verified_adoptions"], ["FB-A1", "FB-A2"])
        self.assertEqual(metrics["evidence"]["lost_orders"], ["FB-L1"])
        self.assertEqual(metrics["evidence"]["field_issues"], ["FB-I1"])
        self.assertEqual(metrics["evidence"]["open_commitments"], ["FB-C1"])

    def test_fit_score_is_none_without_outcomes(self) -> None:
        metrics = compute_metrics(
            [FeedbackRecord.from_dict(make_record("FB-X", kind="field_issue",
                                                  details={"severity": "low"}))]
        )
        self.assertIsNone(metrics["fit_score"])
        self.assertIsNone(metrics["win_rate"])

    def test_compare_groups_by_segment_without_leakage(self) -> None:
        rows = self.service.compare()
        self.assertEqual(len(rows), 2)
        robotics = next(r for r in rows if r["industry"] == "机器人")
        wind = next(r for r in rows if r["industry"] == "风电")
        self.assertEqual(robotics["record_count"], 6)
        self.assertEqual(wind["record_count"], 1)
        robotics_ids = set(robotics["evidence"]["verified_adoptions"])
        self.assertNotIn("FB-W1", robotics_ids)
        self.assertEqual(wind["evidence"]["verified_adoptions"], ["FB-W1"])

    def test_compare_filters_by_stage_and_version(self) -> None:
        rows = self.service.compare(customer_stage="trial", config_version="V1")
        self.assertTrue(all(r["record_count"] > 0 for r in rows))
        rows = self.service.compare(config_version="V2")
        self.assertEqual(rows, [])

    def test_trace_returns_full_records_behind_metrics(self) -> None:
        result = self.service.trace("P1", industry="机器人")
        self.assertEqual(result["metrics"]["verified_adoptions"], 2)
        adoption_records = result["records"]["verified_adoptions"]
        self.assertEqual([r["record_id"] for r in adoption_records], ["FB-A1", "FB-A2"])
        self.assertEqual(adoption_records[0]["details"]["verification_ref"], "PO-2026-001")

    def test_trace_review_resolves_conclusion_records_and_integrity(self) -> None:
        review = self.service.create_review("P1", industry="机器人")
        review = self.service.add_conclusion(
            review.review_id, "两家客户验证通过", "fact", ["FB-A1", "FB-A2"]
        )
        review = self.service.decide(review.review_id, "continue", "继续推广")
        trace = self.service.trace_review(review.review_id)
        revision = trace["revisions"][0]
        self.assertTrue(all(revision["sample_integrity"].values()))
        conclusion = revision["conclusions"][0]
        self.assertEqual(conclusion["evidence_class"], "fact")
        self.assertEqual(
            [r["record_id"] for r in conclusion["records"]], ["FB-A1", "FB-A2"]
        )
        self.assertEqual(revision["decision"]["kind"], "continue")


if __name__ == "__main__":
    unittest.main()
