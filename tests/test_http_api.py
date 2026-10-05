"""HTTP JSON API 冒烟测试。"""

import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

from product_portfolio import InMemoryStore, PortfolioService
from product_portfolio.http_api import make_handler
from support import make_record, ticking_clock


class HttpApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        service = PortfolioService(InMemoryStore(), clock=ticking_clock())
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_00_health(self) -> None:
        status, payload = self.call("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_01_full_flow(self) -> None:
        status, _ = self.call(
            "POST", "/products",
            {"product_code": "P1", "name": "减速器1号", "launched_at": "2025-10-01"},
        )
        self.assertEqual(status, 200)
        status, _ = self.call(
            "POST", "/config-versions",
            {"product_code": "P1", "version": "V1", "released_at": "2025-10-01"},
        )
        self.assertEqual(status, 200)

        records = [
            make_record("FB-1", industry="机器人"),
            make_record("FB-2", industry="风电", kind="lost_order"),
        ]
        status, payload = self.call("POST", "/imports", {"records": records})
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["records"]["added"], ["FB-1", "FB-2"])

        # 重复导入：幂等
        status, payload = self.call("POST", "/imports", {"records": records})
        self.assertEqual(payload["data"]["records"]["added"], [])
        self.assertEqual(sorted(payload["data"]["records"]["duplicates"]), ["FB-1", "FB-2"])

        status, payload = self.call(
            "POST", "/reviews", {"product_code": "P1", "industry": "机器人"}
        )
        self.assertEqual(status, 200)
        review_id = payload["data"]["review_id"]

        status, payload = self.call(
            "POST", f"/reviews/{review_id}/conclusions",
            {"statement": "试装转化成立", "evidence_class": "fact", "record_ids": ["FB-1"]},
        )
        self.assertEqual(status, 200)

        status, payload = self.call(
            "POST", f"/reviews/{review_id}/decision",
            {"kind": "continue", "rationale": "结果可验证"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["revisions"][0]["decision"]["kind"], "continue")

        status, payload = self.call("GET", "/compare")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["data"]), 2)

        status, payload = self.call("GET", f"/trace-review?review={review_id}")
        self.assertEqual(status, 200)
        self.assertEqual(
            payload["data"]["revisions"][0]["conclusions"][0]["records"][0]["record_id"],
            "FB-1",
        )

        industry = urllib.parse.quote("机器人")
        status, payload = self.call("GET", f"/timeline?product=P1&industry={industry}")
        self.assertEqual(status, 200)
        self.assertEqual([r["record_id"] for r in payload["data"]], ["FB-1"])

    def test_02_error_mapping(self) -> None:
        status, payload = self.call("GET", "/reviews/RV-9999")
        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])

        status, payload = self.call("POST", "/products", {"product_code": "P2"})
        self.assertEqual(status, 400)

        status, payload = self.call("GET", "/no-such-route")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
