"""命令行接口测试：完整流程与幂等性。"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from product_portfolio.cli import main
from support import make_record


def run_cli(*argv: str, store: str) -> tuple[int, dict | None]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(list(argv), store_path=store)
    out = stdout.getvalue().strip()
    payload = json.loads(out) if out else None
    if code != 0:
        return code, {"error": stderr.getvalue().strip()}
    return code, payload


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = str(Path(self.tmp.name) / "store.json")
        self.import_file = str(Path(self.tmp.name) / "import.json")
        records = [
            make_record("FB-1", industry="机器人", occurred="2026-01-05"),
            make_record("FB-2", industry="机器人", kind="lost_order", occurred="2026-01-08"),
            make_record("FB-3", industry="风电", occurred="2026-01-06"),
        ]
        Path(self.import_file).write_text(
            json.dumps({"opportunities": [], "records": records}, ensure_ascii=False),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_full_review_flow_via_cli(self) -> None:
        code, _ = run_cli(
            "add-product", "--code", "P1", "--name", "减速器1号", "--launched-at", "2025-10-01",
            store=self.store,
        )
        self.assertEqual(code, 0)
        code, _ = run_cli(
            "add-config", "--product", "P1", "--version", "V1", "--released-at", "2025-10-01",
            store=self.store,
        )
        self.assertEqual(code, 0)

        code, payload = run_cli("import", "--file", self.import_file, store=self.store)
        self.assertEqual(code, 0)
        self.assertEqual(payload["data"]["records"]["added"], ["FB-1", "FB-2", "FB-3"])

        # 重复导入：全部判重，统计不变
        code, payload = run_cli("import", "--file", self.import_file, store=self.store)
        self.assertEqual(code, 0)
        self.assertEqual(payload["data"]["records"]["added"], [])
        self.assertEqual(
            sorted(payload["data"]["records"]["duplicates"]), ["FB-1", "FB-2", "FB-3"]
        )

        code, payload = run_cli(
            "review-create", "--product", "P1", "--industry", "机器人", store=self.store
        )
        self.assertEqual(code, 0)
        review_id = payload["data"]["review_id"]
        sample_ids = [pair[0] for pair in payload["data"]["revisions"][0]["sample"]]
        self.assertEqual(sample_ids, ["FB-1", "FB-2"])

        code, _ = run_cli(
            "review-conclude", "--review", review_id,
            "--statement", "试装转化成立", "--evidence-class", "fact", "--records", "FB-1",
            store=self.store,
        )
        self.assertEqual(code, 0)

        code, payload = run_cli(
            "review-decide", "--review", review_id, "--kind", "continue",
            "--rationale", "结果可验证", store=self.store,
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["data"]["revisions"][0]["decision"]["kind"], "continue")

        # 迟到数据 → 修订
        late_file = str(Path(self.tmp.name) / "late.json")
        Path(late_file).write_text(
            json.dumps({"records": [make_record("FB-4", industry="机器人",
                                              occurred="2026-01-20")]}),
            encoding="utf-8",
        )
        code, _ = run_cli("import", "--file", late_file, store=self.store)
        self.assertEqual(code, 0)
        code, payload = run_cli("late", "--review", review_id, store=self.store)
        self.assertEqual([r["record_id"] for r in payload["data"]], ["FB-4"])
        code, payload = run_cli(
            "review-revise", "--review", review_id, "--reason", "迟到采用结果",
            store=self.store,
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["data"]["revisions"]), 2)

        code, payload = run_cli("compare", store=self.store)
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["data"]), 2)

        code, payload = run_cli("trace-review", "--review", review_id, store=self.store)
        self.assertEqual(code, 0)
        self.assertTrue(
            all(payload["data"]["revisions"][0]["sample_integrity"].values())
        )

    def test_cli_errors_are_json_on_stderr(self) -> None:
        code, payload = run_cli("review-show", "--review", "RV-9999", store=self.store)
        self.assertEqual(code, 1)
        self.assertIn("评审不存在", payload["error"])

    def test_store_file_is_persistent_across_invocations(self) -> None:
        run_cli(
            "add-product", "--code", "P1", "--name", "减速器1号", "--launched-at", "2025-10-01",
            store=self.store,
        )
        code, payload = run_cli("products", store=self.store)
        self.assertEqual(code, 0)
        self.assertEqual([p["product_code"] for p in payload["data"]], ["P1"])


if __name__ == "__main__":
    unittest.main()
