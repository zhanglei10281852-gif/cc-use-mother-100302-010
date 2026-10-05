"""统一统计口径测试：指标定义、溯源清单与适配度评分。"""

import unittest

from product_portfolio.models import MarketRecord, parse_instant
from product_portfolio.statistics import compute_metrics
from support import rec

NOW = parse_instant("2026-06-30T09:00:00+00:00")


def make_records() -> list[MarketRecord]:
    return [
        MarketRecord.from_dict(rec("O1", kind="opportunity", details={"expected_units": 10})),
        MarketRecord.from_dict(rec("O2", kind="opportunity", details={"expected_units": 5})),
        MarketRecord.from_dict(rec("A1", kind="adoption", details={"verified": True, "evidence": "合同", "units": 8})),
        MarketRecord.from_dict(rec("A2", kind="adoption", details={"verified": False})),
        MarketRecord.from_dict(rec("L1", kind="lost_order", details={"reason": "价格高于竞品"})),
        MarketRecord.from_dict(rec("F1", kind="field_issue", details={"severity": "high", "status": "open", "summary": "磨损"})),
        MarketRecord.from_dict(rec("F2", kind="field_issue", details={"severity": "low", "status": "resolved", "summary": "异响"})),
        MarketRecord.from_dict(rec("C1", kind="improvement_commitment", details={"text": "已完成", "status": "fulfilled"})),
        MarketRecord.from_dict(
            rec("C2", kind="improvement_commitment", details={"text": "已逾期", "status": "promised", "due": "2026-01-01T00:00:00+00:00"})
        ),
        MarketRecord.from_dict(rec("T1", kind="feedback", details={"channel": "trial", "summary": "正常"})),
    ]


class MetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.metrics = compute_metrics(make_records(), NOW)

    def test_counts_by_kind(self) -> None:
        counts = self.metrics["counts"]
        self.assertEqual(counts["opportunity"], 2)
        self.assertEqual(counts["adoption"], 2)
        self.assertEqual(counts["feedback"], 1)
        self.assertEqual(self.metrics["total_records"], 10)

    def test_adoption_rate_counts_verified_only(self) -> None:
        self.assertEqual(self.metrics["verified_adoptions"], 1)
        self.assertEqual(self.metrics["adoption_rate"], 0.5)
        self.assertEqual(self.metrics["win_rate"], 0.5)

    def test_lost_reasons_histogram(self) -> None:
        self.assertEqual(self.metrics["lost_reasons"], {"价格高于竞品": 1})

    def test_field_issues_and_commitments(self) -> None:
        self.assertEqual(self.metrics["field_issues"], {"open": 1, "resolved": 1})
        self.assertEqual(self.metrics["resolution_rate"], 0.5)
        self.assertEqual(self.metrics["commitments"]["fulfilled"], 1)
        self.assertEqual(self.metrics["commitments"]["overdue"], 1)
        self.assertEqual(self.metrics["fulfillment_rate"], 0.5)

    def test_fit_score_uses_all_components(self) -> None:
        self.assertEqual(self.metrics["fit_score"], 0.5)

    def test_evidence_traces_records(self) -> None:
        evidence = self.metrics["evidence"]
        self.assertEqual(evidence["verified_adoptions"], ["A1"])
        self.assertEqual(evidence["opportunities"], ["O1", "O2"])
        self.assertEqual(evidence["commitments_overdue"], ["C2"])
        self.assertEqual(evidence["field_issues_open"], ["F1"])

    def test_empty_records_yield_no_score(self) -> None:
        metrics = compute_metrics([], NOW)
        self.assertIsNone(metrics["fit_score"])
        self.assertIsNone(metrics["adoption_rate"])
        self.assertEqual(metrics["total_records"], 0)

    def test_partial_data_renormalizes_weights(self) -> None:
        only_adoption = [MarketRecord.from_dict(rec("A1", kind="adoption", details={"verified": True, "evidence": "合同"}))]
        metrics = compute_metrics(only_adoption, NOW)
        # 只有 win_rate 一个组件可用（无机会、无问题、无承诺），权重归一化后等于该组件值
        self.assertEqual(metrics["fit_score"], 1.0)
        self.assertIsNone(metrics["adoption_rate"])

    def test_score_components_are_capped_at_one(self) -> None:
        # 采用数超过机会登记数时 adoption_rate 原始值大于 1（暴露漏登），但评分组件截断到 1
        records = [
            MarketRecord.from_dict(rec("A1", kind="adoption", details={"verified": True, "evidence": "合同"})),
            MarketRecord.from_dict(rec("A2", kind="adoption", details={"verified": True, "evidence": "合同"})),
        ]
        metrics = compute_metrics(records, NOW)
        self.assertIsNone(metrics["adoption_rate"])  # 无机会登记，比率无定义
        self.assertLessEqual(metrics["fit_score"], 1.0)
        with_opportunity = records + [
            MarketRecord.from_dict(rec("O1", kind="opportunity", details={})),
        ]
        metrics = compute_metrics(with_opportunity, NOW)
        self.assertEqual(metrics["adoption_rate"], 2.0)  # 原始比率保留，提示机会漏登
        self.assertLessEqual(metrics["fit_score"], 1.0)


if __name__ == "__main__":
    unittest.main()
