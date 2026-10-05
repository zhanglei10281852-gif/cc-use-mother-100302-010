"""端到端演示的不变量测试。"""

import unittest

from product_portfolio.demo import run_demo


class DemoTests(unittest.TestCase):
    def test_demo_invariants(self) -> None:
        result = run_demo()
        self.assertEqual(result["first_import"]["rejected"], 0)
        self.assertGreater(result["first_import"]["imported"], 0)
        # 重复导入完全幂等
        self.assertEqual(result["second_import"]["imported"], 0)
        self.assertEqual(result["second_import"]["rejected"], 0)
        self.assertEqual(result["second_import"]["duplicates"], result["first_import"]["imported"])
        # 迟到数据只触发修订，不改写原评审
        self.assertEqual(len(result["late_records_after_freeze"]), 1)
        self.assertEqual(
            result["revision"]["sample_size"],
            result["review"]["frozen_sample_size"] + 1,
        )
        self.assertTrue(result["revision"]["revision_of"])
        # 八款新品全部进入对比
        products = {row["product_code"] for row in result["comparison_rows"]}
        self.assertEqual(len(products), 8)
        # 结论可追溯到记录且指纹一致
        self.assertTrue(result["finding_evidence"]["fingerprints_match"])
        self.assertTrue(result["finding_evidence"]["record_ids"])


if __name__ == "__main__":
    unittest.main()
