"""阶段评审测试：冻结样本、证据分级、决策、迟到数据修订。"""

import unittest

from product_portfolio import StateError, ValidationError
from support import make_record, make_service


class ReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = make_service()
        self.service.import_records(
            [
                make_record("FB-R1", industry="机器人", occurred="2026-01-05"),
                make_record(
                    "FB-R2",
                    industry="机器人",
                    kind="lost_order",
                    occurred="2026-01-08",
                ),
                make_record("FB-W1", industry="风电", occurred="2026-01-06"),
            ]
        )
        self.review = self.service.create_review("P1", industry="机器人")

    # -- 冻结样本 ---------------------------------------------------------

    def test_freeze_captures_scoped_sample_and_stats(self) -> None:
        revision = self.review.latest
        self.assertEqual(revision.revision_no, 1)
        self.assertEqual(revision.sample_ids, ("FB-R1", "FB-R2"))
        self.assertEqual(revision.stats["verified_adoptions"], 1)
        self.assertEqual(revision.stats["lost_orders"], 1)
        self.assertEqual(revision.stats["win_rate"], 0.5)

    def test_frozen_revision_is_immutable_after_late_import(self) -> None:
        before = self.service.get_review(self.review.review_id).latest.to_dict()
        self.service.import_records(
            [make_record("FB-R3", industry="机器人", occurred="2026-01-20")]
        )
        after = self.service.get_review(self.review.review_id).latest.to_dict()
        self.assertEqual(before, after)

    # -- 结论证据分级 -------------------------------------------------------

    def test_fact_requires_records_from_sample(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.add_conclusion(self.review.review_id, "没有依据的事实", "fact")

    def test_inference_requires_records(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.add_conclusion(self.review.review_id, "没有依据的推断", "inference")

    def test_hypothesis_must_not_cite_records(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.add_conclusion(
                self.review.review_id, "引用记录的假设", "hypothesis", ["FB-R1"]
            )

    def test_conclusion_cannot_cite_records_outside_sample(self) -> None:
        # FB-W1 属于风电细分市场，机器人评审引用它即跨市场套用
        with self.assertRaises(ValidationError):
            self.service.add_conclusion(
                self.review.review_id, "套用风电结论", "fact", ["FB-W1"]
            )

    def test_conclusion_citation_is_case_insensitive(self) -> None:
        review = self.service.add_conclusion(
            self.review.review_id, "试装转化成立", "fact", ["fb-r1"]
        )
        self.assertEqual(review.latest.conclusions[0].record_ids, ("FB-R1",))

    def test_add_conclusion_appends_to_current_revision(self) -> None:
        review = self.service.add_conclusion(
            self.review.review_id, "试装转化成立", "fact", ["FB-R1"]
        )
        review = self.service.add_conclusion(
            review.review_id, "价格可能是主要失单因素", "hypothesis"
        )
        classes = [c.evidence_class for c in review.latest.conclusions]
        self.assertEqual(classes, ["fact", "hypothesis"])

    # -- 决策 ---------------------------------------------------------------

    def test_decision_requires_conclusion_first(self) -> None:
        with self.assertRaises(StateError):
            self.service.decide(self.review.review_id, "continue", "无理由")

    def test_restrict_requires_allowed_scope(self) -> None:
        self.service.add_conclusion(self.review.review_id, "转化成立", "fact", ["FB-R1"])
        with self.assertRaises(ValidationError):
            self.service.decide(self.review.review_id, "restrict", "仅高频工况可用")

    def test_improve_requires_commitment_records(self) -> None:
        self.service.add_conclusion(self.review.review_id, "转化成立", "fact", ["FB-R1"])
        with self.assertRaises(ValidationError):
            self.service.decide(self.review.review_id, "improve", "先改进密封")
        with self.assertRaises(ValidationError):
            self.service.decide(
                self.review.review_id, "improve", "先改进密封",
                improvement_record_ids=["FB-R1"],  # 不是改进承诺
            )

    def test_decided_revision_rejects_further_changes(self) -> None:
        self.service.add_conclusion(self.review.review_id, "转化成立", "fact", ["FB-R1"])
        self.service.decide(self.review.review_id, "continue", "继续推广")
        with self.assertRaises(StateError):
            self.service.add_conclusion(self.review.review_id, "补充", "hypothesis")
        with self.assertRaises(StateError):
            self.service.decide(self.review.review_id, "exit", "改主意")

    # -- 迟到数据与修订 -------------------------------------------------------

    def test_late_records_are_detected(self) -> None:
        self.service.import_records(
            [make_record("FB-R3", industry="机器人", occurred="2026-01-20")]
        )
        late = self.service.late_records(self.review.review_id)
        self.assertEqual([r.record_id for r in late], ["FB-R3"])

    def test_revise_requires_late_data(self) -> None:
        with self.assertRaises(StateError):
            self.service.revise_review(self.review.review_id, "没有新数据")

    def test_revision_refreezes_sample_and_keeps_history(self) -> None:
        self.service.add_conclusion(self.review.review_id, "转化成立", "fact", ["FB-R1"])
        self.service.decide(self.review.review_id, "continue", "继续推广")
        self.service.import_records(
            [make_record("FB-R3", industry="机器人", occurred="2026-01-20")]
        )
        updated = self.service.revise_review(self.review.review_id, "迟到采用结果到达")

        self.assertEqual(len(updated.revisions), 2)
        first, second = updated.revisions
        # 历史修订保持冻结原样
        self.assertEqual(first.sample_ids, ("FB-R1", "FB-R2"))
        self.assertEqual(first.decision.kind, "continue")
        self.assertEqual(len(first.conclusions), 1)
        # 新修订重新冻结全部匹配记录，结论与决策清空待重评
        self.assertEqual(second.revision_no, 2)
        self.assertEqual(second.sample_ids, ("FB-R1", "FB-R2", "FB-R3"))
        self.assertEqual(second.stats["verified_adoptions"], 2)
        self.assertEqual(second.reason, "迟到采用结果到达")
        self.assertEqual(second.conclusions, ())
        self.assertIsNone(second.decision)

    def test_revision_allows_new_decision_cycle(self) -> None:
        self.service.import_records(
            [make_record("FB-R3", industry="机器人", occurred="2026-01-20")]
        )
        updated = self.service.revise_review(self.review.review_id, "迟到数据")
        updated = self.service.add_conclusion(
            updated.review_id, "两家客户均采用", "fact", ["FB-R1", "FB-R3"]
        )
        updated = self.service.decide(updated.review_id, "continue", "扩大推广")
        self.assertEqual(updated.latest.decision.kind, "continue")

    def test_closed_review_cannot_be_revised(self) -> None:
        self.service.add_conclusion(self.review.review_id, "转化成立", "fact", ["FB-R1"])
        self.service.decide(self.review.review_id, "continue", "继续推广")
        self.service.close_review(self.review.review_id)
        self.service.import_records(
            [make_record("FB-R3", industry="机器人", occurred="2026-01-20")]
        )
        with self.assertRaises(StateError):
            self.service.revise_review(self.review.review_id, "迟到数据")

    def test_undecided_review_cannot_close(self) -> None:
        with self.assertRaises(StateError):
            self.service.close_review(self.review.review_id)


if __name__ == "__main__":
    unittest.main()
