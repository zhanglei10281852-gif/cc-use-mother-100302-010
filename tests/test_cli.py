"""命令行接口测试：完整管理流程走查。"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from product_portfolio.cli import main as cli_main
from support import rec


def run_cli(*argv: str) -> dict:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exit_code = cli_main(list(argv))
    if exit_code != 0:
        raise AssertionError(f"CLI 退出码异常: {exit_code}")
    return json.loads(buffer.getvalue())


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = str(Path(self.tmp.name) / "store.json")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def cli(self, *argv: str) -> dict:
        return run_cli("--store", self.store, *argv)

    def test_full_flow(self) -> None:
        product = self.cli("add-product", "C1", "命令行产品")
        self.assertEqual(product["product_code"], "C1")
        self.cli("add-config", "C1", "A1")

        records_file = Path(self.tmp.name) / "records.json"
        records_file.write_text(
            json.dumps(
                [
                    rec("C-R1", product="C1"),
                    rec("C-R2", product="C1", kind="adoption", details={"verified": True, "evidence": "合同"}),
                    rec("C-R3", product="C1", kind="improvement_commitment",
                        details={"text": "优化工艺", "status": "promised"}),
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        report = self.cli("import", str(records_file))
        self.assertEqual(report["summary"], {"imported": 3, "duplicates": 0, "rejected": 0})
        again = self.cli("import", str(records_file))
        self.assertEqual(again["summary"]["duplicates"], 3)

        timeline = self.cli("timeline", "C1")
        self.assertEqual(len(timeline["entries"]), 3)

        review = self.cli("review-freeze", "C1", "G1")
        self.assertEqual(review["status"], "frozen")
        self.assertEqual(len(review["sample"]), 3)

        finding = self.cli(
            "review-finding", review["review_id"],
            "--kind", "fact", "--text", "试装反馈稳定", "--evidence", "C-R1",
        )
        self.assertEqual(finding["kind"], "fact")

        decision = self.cli(
            "review-decide", review["review_id"],
            "--action", "improve", "--rationale", "先落实工艺改进", "--commitments", "C-R3",
        )
        self.assertEqual(decision["action"], "improve")

        shown = self.cli("review-show", review["review_id"])
        self.assertEqual(shown["status"], "decided")
        self.assertEqual(shown["late_records"], [])

        comparison = self.cli("compare", "--group-by", "industry")
        self.assertTrue(any(row["product_code"] == "C1" for row in comparison["rows"]))

        trace = self.cli("finding-evidence", finding["finding_id"])
        self.assertEqual([r["record_id"] for r in trace["records"]], ["C-R1"])

    def test_review_revise_via_cli(self) -> None:
        self.cli("add-product", "C2", "修订产品")
        self.cli("add-config", "C2", "A1")
        records_file = Path(self.tmp.name) / "records.json"
        records_file.write_text(json.dumps([rec("C2-R1", product="C2")], ensure_ascii=False), encoding="utf-8")
        self.cli("import", str(records_file))
        review = self.cli("review-freeze", "C2", "G1")
        revision = self.cli("review-revise", review["review_id"])
        self.assertEqual(revision["revision_no"], 2)
        self.assertEqual(revision["revision_of"], review["review_id"])


if __name__ == "__main__":
    unittest.main()
