"""HTTP API 集成测试（标准库客户端 + 临时端口）。"""

import json
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import quote

from product_portfolio.http_api import serve
from support import rec


class HttpApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = serve(None, "127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_full_management_flow(self) -> None:
        status, _ = self.call("POST", "/products", {"product_code": "H1", "name": "HTTP 产品"})
        self.assertEqual(status, 200)
        status, _ = self.call("POST", "/config-versions", {"product_code": "H1", "version": "A1"})
        self.assertEqual(status, 200)

        batch = [
            rec("H-R1", product="H1"),
            rec("H-R2", product="H1", kind="adoption", details={"verified": True, "evidence": "合同"}),
        ]
        status, report = self.call("POST", "/imports", {"records": batch})
        self.assertEqual(status, 200)
        self.assertEqual(report["summary"]["imported"], 2)
        # 重复导入：统计不变
        status, report = self.call("POST", "/imports", {"records": batch})
        self.assertEqual(report["summary"]["duplicates"], 2)

        status, review = self.call("POST", "/reviews", {"product_code": "H1", "gate": "G1"})
        self.assertEqual(status, 200)
        self.assertEqual(review["status"], "frozen")
        self.assertEqual(len(review["sample"]), 2)

        status, finding = self.call(
            "POST",
            f"/reviews/{quote(review['review_id'])}/findings",
            {"kind": "fact", "text": "试装反馈稳定", "evidence": ["H-R1"]},
        )
        self.assertEqual(status, 200)

        status, decision = self.call(
            "POST", f"/reviews/{quote(review['review_id'])}/decision", {"action": "continue", "rationale": "达标"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(decision["action"], "continue")

        status, comparison = self.call("GET", "/compare?group_by=industry")
        self.assertEqual(status, 200)
        self.assertTrue(any(row["product_code"] == "H1" for row in comparison["rows"]))

        status, trace = self.call("GET", f"/findings/{quote(finding['finding_id'])}/evidence")
        self.assertEqual(status, 200)
        self.assertEqual([r["record_id"] for r in trace["records"]], ["H-R1"])

        status, view = self.call("GET", f"/reviews/{quote(review['review_id'])}")
        self.assertEqual(view["late_records"], [])

    def test_unknown_path_returns_404(self) -> None:
        status, body = self.call("GET", "/no-such-path")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_validation_error_returns_400(self) -> None:
        status, body = self.call("POST", "/reviews", {"product_code": "H1"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_missing_resource_returns_404(self) -> None:
        status, _ = self.call("GET", "/reviews/RV-NONE-G9-v1")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
