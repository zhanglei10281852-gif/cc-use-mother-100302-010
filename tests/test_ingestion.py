"""幂等导入与主数据校验测试。"""

import unittest

from support import make_service, rec


class IngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service, self.clock = make_service()

    def test_import_new_records(self) -> None:
        report = self.service.import_records([rec("R1"), rec("R2")])
        self.assertEqual(report.imported, ["R1", "R2"])
        self.assertEqual(report.duplicates, [])
        self.assertEqual(report.rejected, [])

    def test_duplicate_import_does_not_change_statistics(self) -> None:
        batch = [rec("R1"), rec("R2", kind="adoption", details={"verified": True, "evidence": "合同"})]
        self.service.import_records(batch)
        before = self.service.metrics("P1")
        report = self.service.import_records(batch)
        after = self.service.metrics("P1")
        self.assertEqual(report.imported, [])
        self.assertEqual(sorted(report.duplicates), ["R1", "R2"])
        self.assertEqual(before, after)

    def test_conflicting_identity_is_rejected(self) -> None:
        self.service.import_records([rec("R1")])
        changed = rec("R1", details={"channel": "quote", "summary": "被改写的报价反馈"})
        report = self.service.import_records([changed])
        self.assertEqual(report.imported, [])
        self.assertEqual(report.duplicates, [])
        self.assertEqual(len(report.rejected), 1)
        self.assertIn("冲突", report.rejected[0]["reason"])

    def test_unknown_product_is_rejected(self) -> None:
        report = self.service.import_records([rec("R9", product="PX")])
        self.assertEqual(report.imported, [])
        self.assertIn("产品不存在", report.rejected[0]["reason"])

    def test_unknown_config_version_is_rejected(self) -> None:
        report = self.service.import_records([rec("R9", version="Z9")])
        self.assertEqual(report.imported, [])
        self.assertIn("配置版本未登记", report.rejected[0]["reason"])

    def test_invalid_records_are_rejected(self) -> None:
        cases = [
            rec("B1", kind="feedback", details={"channel": "unknown", "summary": "x"}),
            rec("B2", kind="adoption", details={"verified": True}),
            rec("B3", kind="not-a-kind"),
            rec("B4", kind="lost_order", details={}),
        ]
        report = self.service.import_records(cases)
        self.assertEqual(report.imported, [])
        self.assertEqual(len(report.rejected), 4)

    def test_partial_import_keeps_valid_records(self) -> None:
        report = self.service.import_records([rec("OK1"), rec("BAD", version="Z9")])
        self.assertEqual(report.imported, ["OK1"])
        self.assertEqual(len(report.rejected), 1)
        self.assertEqual(self.service.metrics("P1")["total_records"], 1)


if __name__ == "__main__":
    unittest.main()
