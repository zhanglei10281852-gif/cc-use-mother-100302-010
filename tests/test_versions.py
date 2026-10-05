"""配置版本隔离测试：升级后旧反馈保留在原版本。"""

import unittest

from product_portfolio import ConflictError, NotFoundError
from support import make_record, make_service


class ConfigVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = make_service()
        self.service.import_records(
            [
                make_record("FB-V1-A", version="V1", occurred="2025-12-01"),
                make_record("FB-V1-B", version="V1", kind="field_issue", occurred="2025-12-20"),
            ]
        )

    def test_upgrade_keeps_old_feedback_on_original_version(self) -> None:
        self.service.import_records([make_record("FB-V2-A", version="V2", occurred="2026-04-01")])

        v1_rows = self.service.compare(config_version="V1")
        self.assertEqual(len(v1_rows), 1)
        self.assertEqual(v1_rows[0]["record_count"], 2)
        self.assertEqual(
            sorted(v1_rows[0]["evidence"]["verified_adoptions"]), ["FB-V1-A"]
        )

        v2_rows = self.service.compare(config_version="V2")
        self.assertEqual(len(v2_rows), 1)
        self.assertEqual(v2_rows[0]["record_count"], 1)

        v1_timeline = self.service.timeline("P1", config_version="V1")
        self.assertEqual([r.record_id for r in v1_timeline], ["FB-V1-A", "FB-V1-B"])

    def test_version_chain_is_registered(self) -> None:
        versions = self.service.list_config_versions("P1")
        self.assertEqual([v.version for v in versions], ["V1", "V2"])
        self.assertEqual(versions[1].supersedes, "V1")

    def test_duplicate_version_is_rejected(self) -> None:
        with self.assertRaises(ConflictError):
            self.service.add_config_version("P1", "V1", "2026-01-01")

    def test_supersedes_must_exist(self) -> None:
        with self.assertRaises(NotFoundError):
            self.service.add_config_version("P2", "V2", "2026-01-01", supersedes="V0")

    def test_feedback_for_unregistered_version_is_rejected(self) -> None:
        result = self.service.import_records([make_record("FB-V3", version="V3")])
        self.assertEqual(result.added, ())
        self.assertEqual(len(result.conflicts), 1)


if __name__ == "__main__":
    unittest.main()
