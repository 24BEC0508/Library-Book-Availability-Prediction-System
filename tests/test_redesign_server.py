"""
Integration Tests for Redesigned Student & Administrator Portals and Static Asset Serving.
"""

import json
import threading
import time
import urllib.request
import unittest
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from app.api import LibraryApiHandler


class TestRedesignServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Start the HTTP server on an ephemeral port in a daemon thread
        cls.port = 8899
        cls.server = HTTPServer(("127.0.0.1", cls.port), LibraryApiHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path, headers=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode("utf-8"), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8"), e.headers

    def _post(self, path, data, headers=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        payload = json.dumps(data).encode("utf-8")
        h = {"Content-Type": "application/json"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, data=payload, headers=h, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode("utf-8"), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8"), e.headers

    def test_01_student_portal_served_at_root(self):
        status, body, headers = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("Apex University Library", body)
        self.assertIn("Find Library Books & Forecast Availability", body)
        self.assertIn("/css/student.css", body)
        self.assertIn("/js/student.js", body)

    def test_02_admin_portal_served_at_admin_route(self):
        status, body, headers = self._get("/admin")
        self.assertEqual(status, 200)
        self.assertIn("Staff Circulation & Management Console", body)
        self.assertIn("Circulation Desk Operations", body)
        self.assertIn("/css/admin.css", body)
        self.assertIn("/js/admin.js", body)

    def test_03_static_assets_served_cleanly(self):
        assets = [
            "/css/common.css",
            "/css/student.css",
            "/css/admin.css",
            "/js/config.js",
            "/js/student.js",
            "/js/admin.js",
        ]
        for asset in assets:
            status, body, headers = self._get(asset)
            self.assertEqual(status, 200, f"Asset {asset} failed with {status}")
            self.assertGreater(len(body), 50)

    def test_04_departments_endpoint(self):
        status, body, _ = self._get("/api/departments")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("departments", data)
        self.assertIn("CSE", data["departments"])
        self.assertIn("Civil", data["departments"])

    def test_05_books_search_with_pagination(self):
        status, body, _ = self._get("/api/books?q=Materials&limit=5&offset=0")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("total_matches", data)
        self.assertIn("books", data)
        self.assertEqual(data["limit"], 5)
        self.assertEqual(data["offset"], 0)
        self.assertLessEqual(len(data["books"]), 5)

    def test_06_admin_stats_authentication_boundary(self):
        # 1. Unauthenticated request rejected
        status, body, _ = self._get("/api/admin/stats")
        self.assertEqual(status, 401)

        # 2. Authenticated request accepted
        status, body, _ = self._get("/api/admin/stats", headers={"X-Admin-Key": "library-admin-secret-key-2026"})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("total_titles", data)
        self.assertIn("total_copies", data)
        self.assertIn("available_copies", data)
        self.assertIn("on_loan_copies", data)

    def test_07_circulation_checkout_and_return_flow(self):
        book_id = "B08510"
        admin_headers = {"X-Admin-Key": "library-admin-secret-key-2026"}

        # Checkout
        status, body, _ = self._post(
            "/api/transactions/checkout",
            {
                "book_id": book_id,
                "student_id": "TEST_STUDENT_INTEGRATION",
                "notes": "Integration test checkout"
            },
            headers=admin_headers
        )
        self.assertEqual(status, 201)
        co_data = json.loads(body)
        self.assertEqual(co_data["data"]["action"], "CHECKOUT")

        # Return
        status, body, _ = self._post(
            "/api/transactions/return",
            {
                "book_id": book_id,
                "student_id": "TEST_STUDENT_INTEGRATION",
                "notes": "Integration test return"
            },
            headers=admin_headers
        )
        self.assertEqual(status, 200)
        ret_data = json.loads(body)
        self.assertEqual(ret_data["data"]["action"], "RETURN")

    def test_08_predict_availability_and_stockout(self):
        status, body, _ = self._get("/api/predict/B08510")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["book_id"], "B08510")
        self.assertIn("inventory", data)
        self.assertIn("demand_forecast", data)
        self.assertIn("stockout_assessment", data)
        self.assertIn("academic_calendar", data)


if __name__ == "__main__":
    unittest.main()
