"""幂等导入与统一口径测试。"""

import unittest

from support import make_opportunity, make_record, make_service


class IngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = make_service()

    def test_import_adds_records(self) -> None:
        result = self.service.import_records([make_record("FB-1"), make_record("FB-2")])
        self.assertEqual(result.added, ("FB-1", "FB-2"))
        self.assertEqual(len(self.service.timeline("P1")), 2)

    def test_duplicate_import_keeps_statistics(self) -> None:
        batch = [make_record("FB-1"), make_record("FB-2", kind="lost_order")]
        self.service.import_records(batch)
        before = self.service.compare()
        again = self.service.import_records(batch)
        self.assertEqual(again.added, ())
        self.assertEqual(sorted(again.duplicates), ["FB-1", "FB-2"])
        self.assertEqual(again.conflicts, ())
        self.assertEqual(self.service.compare(), before)

    def test_conflicting_record_is_rejected_and_state_untouched(self) -> None:
        self.service.import_records([make_record("FB-1", summary="原始内容")])
        changed = make_record("FB-1", summary="被篡改的内容")
        result = self.service.import_records([changed])
        self.assertEqual(result.added, ())
        self.assertEqual(len(result.conflicts), 1)
        self.assertEqual(self.service.get_record("FB-1").summary, "原始内容")

    def test_invalid_records_are_reported_not_stored(self) -> None:
        bad_adoption = make_record("FB-1", details={})  # 采用结果缺少验证依据
        unknown_product = make_record("FB-2", product="PX")
        unknown_version = make_record("FB-3", version="V9")
        unknown_opportunity = make_record("FB-4", opportunity="OP-404")
        bad_stage = make_record("FB-5", stage="试驾")
        result = self.service.import_records(
            [bad_adoption, unknown_product, unknown_version, unknown_opportunity, bad_stage]
        )
        self.assertEqual(result.added, ())
        self.assertEqual(len(result.conflicts), 5)
        self.assertEqual(self.service.timeline("P1"), [])

    def test_opportunity_import_is_idempotent(self) -> None:
        first = self.service.import_opportunities([make_opportunity("OP-1")])
        self.assertEqual(first.added, ("OP-1",))
        second = self.service.import_opportunities([make_opportunity("OP-1")])
        self.assertEqual(second.duplicates, ("OP-1",))
        conflict = self.service.import_opportunities([make_opportunity("OP-1", industry="风电")])
        self.assertEqual(len(conflict.conflicts), 1)

    def test_record_can_link_registered_opportunity(self) -> None:
        self.service.import_opportunities([make_opportunity("OP-1")])
        result = self.service.import_records([make_record("FB-1", opportunity="OP-1")])
        self.assertEqual(result.added, ("FB-1",))

    def test_text_normalization_unifies_department_calibers(self) -> None:
        self.service.import_records(
            [
                make_record("FB-1", industry=" 机器人 ", conditions="高频  往复"),
                make_record("FB-2", industry="机器人", conditions="高频 往复"),
            ]
        )
        rows = self.service.compare()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["record_count"], 2)

    def test_dimension_matching_ignores_case(self) -> None:
        self.service.import_records(
            [
                make_record("FB-1", industry="Wind"),
                make_record("FB-2", industry="wind"),
            ]
        )
        rows = self.service.compare()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["record_count"], 2)


if __name__ == "__main__":
    unittest.main()
