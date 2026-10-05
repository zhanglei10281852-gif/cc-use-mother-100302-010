"""阶段评审测试：冻结样本、事实/推断/假设、决策、迟到数据与修订。"""

import unittest

from product_portfolio.models import SegmentFilter
from support import make_service, rec

ADOPTION = {"verified": True, "evidence": "合同与验收单", "units": 5}
COMMITMENT = {"text": "优化热处理工艺", "status": "promised", "due": "2026-09-30T00:00:00+00:00"}


class ReviewFreezeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service, self.clock = make_service()
        self.service.import_records(
            [
                rec("R1"),
                rec("R2", kind="adoption", stage="首单", details=ADOPTION),
                rec("R3", industry="风电"),
                rec("R4", condition="连续运行"),
                rec("R5", kind="improvement_commitment", details=COMMITMENT),
            ]
        )

    def freeze(self, scope: SegmentFilter | None = None):
        return self.service.freeze_review("P1", "G1", scope or SegmentFilter(industry="机器人"))

    def test_freeze_captures_sample_and_stats(self) -> None:
        review = self.freeze()
        self.assertEqual(set(review.sample_ids), {"R1", "R2", "R4", "R5"})  # R3 属于风电，不在范围内
        self.assertEqual(review.stats["total_records"], 4)
        self.assertEqual(review.stats["verified_adoptions"], 1)
        for entry in review.sample:
            stored = self.service.store.get_record(entry["record_id"])
            self.assertEqual(entry["fingerprint"], stored.record.fingerprint())

    def test_same_gate_cannot_be_frozen_twice(self) -> None:
        self.freeze()
        with self.assertRaises(ValueError):
            self.freeze()

    def test_fact_requires_evidence(self) -> None:
        review = self.freeze()
        with self.assertRaises(ValueError):
            self.service.add_finding(review.review_id, "fact", "没有证据的事实")

    def test_evidence_must_be_in_frozen_sample(self) -> None:
        review = self.freeze()
        with self.assertRaisesRegex(ValueError, "冻结样本"):
            self.service.add_finding(review.review_id, "fact", "引用范围外记录", evidence=["R3"])

    def test_evidence_must_match_finding_segment(self) -> None:
        review = self.freeze()
        with self.assertRaisesRegex(ValueError, "细分范围"):
            self.service.add_finding(
                review.review_id,
                "fact",
                "高负载工况结论却引用连续运行记录",
                evidence=["R4"],
                segment_scope=SegmentFilter(condition="高负载"),
            )

    def test_finding_scope_cannot_exceed_review_scope(self) -> None:
        review = self.freeze()
        with self.assertRaisesRegex(ValueError, "超出评审范围"):
            self.service.add_finding(
                review.review_id,
                "fact",
                "把机器人结论套到风电",
                evidence=["R1"],
                segment_scope=SegmentFilter(industry="风电"),
            )

    def test_hypothesis_requires_verification_plan(self) -> None:
        review = self.freeze()
        with self.assertRaises(ValueError):
            self.service.add_finding(review.review_id, "hypothesis", "无计划的假设")
        finding = self.service.add_finding(
            review.review_id, "hypothesis", "大扭矩场景可能同样适用", verification_plan="Q4 安排两家客户试装验证"
        )
        self.assertEqual(finding.kind.value, "hypothesis")

    def test_fact_and_inference_with_valid_evidence(self) -> None:
        review = self.freeze()
        fact = self.service.add_finding(review.review_id, "fact", "试装反馈稳定", evidence=["R1"])
        inference = self.service.add_finding(review.review_id, "inference", "高负载工况适配良好", evidence=["R1", "R2"])
        self.assertEqual(fact.finding_id, f"{review.review_id}-F1")
        self.assertEqual(inference.finding_id, f"{review.review_id}-F2")


class ReviewDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service, self.clock = make_service()
        self.service.import_records(
            [
                rec("R1"),
                rec("R5", kind="improvement_commitment", details=COMMITMENT),
            ]
        )
        self.review = self.service.freeze_review("P1", "G1")

    def test_decide_continue(self) -> None:
        decision = self.service.decide(self.review.review_id, "continue", "指标达标")
        self.assertEqual(decision.action.value, "continue")
        self.assertEqual(self.service.store.get_review(self.review.review_id).status.value, "decided")

    def test_restrict_requires_restrictions(self) -> None:
        with self.assertRaises(ValueError):
            self.service.decide(self.review.review_id, "restrict", "部分场景不适用")
        decision = self.service.decide(
            self.review.review_id,
            "restrict",
            "连续运行工况暂不支持",
            restrictions=[SegmentFilter(condition="连续运行")],
        )
        self.assertEqual(len(decision.restrictions), 1)

    def test_improve_requires_commitments_in_sample(self) -> None:
        with self.assertRaises(ValueError):
            self.service.decide(self.review.review_id, "improve", "先改进再推广")
        with self.assertRaisesRegex(ValueError, "冻结样本"):
            self.service.decide(self.review.review_id, "improve", "引用样本外承诺", commitment_ids=["RX"])
        with self.assertRaisesRegex(ValueError, "不是改进承诺"):
            self.service.decide(self.review.review_id, "improve", "引用错误类型", commitment_ids=["R1"])
        decision = self.service.decide(self.review.review_id, "improve", "落实工艺改进", commitment_ids=["R5"])
        self.assertEqual(decision.commitment_ids, ("R5",))

    def test_decided_review_is_closed(self) -> None:
        self.service.decide(self.review.review_id, "continue", "达标")
        with self.assertRaises(ValueError):
            self.service.add_finding(self.review.review_id, "fact", "事后补充", evidence=["R1"])
        with self.assertRaises(ValueError):
            self.service.decide(self.review.review_id, "exit", "重复决策")


class LateDataAndRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service, self.clock = make_service()
        self.service.import_records([rec("R1"), rec("R2", kind="adoption", details=ADOPTION)])
        self.review = self.service.freeze_review("P1", "G1")
        self.frozen_stats = dict(self.review.stats)

    def test_late_record_does_not_change_frozen_review(self) -> None:
        self.clock.advance(days=1)
        self.service.import_records([rec("R9", occurred="2026-05-01T08:00:00+00:00")])
        late = self.service.late_records(self.review.review_id)
        self.assertEqual([item["record_id"] for item in late], ["R9"])
        unchanged = self.service.store.get_review(self.review.review_id)
        self.assertEqual(unchanged.stats, self.frozen_stats)
        self.assertEqual(len(unchanged.sample), 2)

    def test_record_occurred_after_freeze_is_not_late(self) -> None:
        self.clock.advance(days=1)
        self.service.import_records([rec("R9", occurred="2026-08-01T08:00:00+00:00")])
        self.assertEqual(self.service.late_records(self.review.review_id), [])

    def test_revision_includes_late_data_and_keeps_original(self) -> None:
        self.clock.advance(days=1)
        self.service.import_records([rec("R9", occurred="2026-05-01T08:00:00+00:00")])
        revision = self.service.revise_review(self.review.review_id)
        self.assertEqual(revision.revision_no, 2)
        self.assertEqual(revision.revision_of, self.review.review_id)
        self.assertEqual(set(revision.sample_ids), {"R1", "R2", "R9"})
        original = self.service.store.get_review(self.review.review_id)
        self.assertEqual(original.stats, self.frozen_stats)
        self.assertEqual(len(original.sample), 2)
        chain = self.service.review_view(revision.review_id)["revision_chain"]
        self.assertEqual(chain, [self.review.review_id, revision.review_id])

    def test_revision_must_start_from_latest(self) -> None:
        self.clock.advance(days=1)
        revision = self.service.revise_review(self.review.review_id)
        with self.assertRaisesRegex(ValueError, "最新版本"):
            self.service.revise_review(self.review.review_id)
        third = self.service.revise_review(revision.review_id)
        self.assertEqual(third.revision_no, 3)


if __name__ == "__main__":
    unittest.main()
