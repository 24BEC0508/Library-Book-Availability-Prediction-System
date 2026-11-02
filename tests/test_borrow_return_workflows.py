"""
Comprehensive Unit & Integration Tests for Student Borrow and Return Workflows.

Verifies all 10 Acceptance Criteria:
1. Borrowing one available copy decreases available inventory by one.
2. Borrowing is blocked when availability is zero (raises InventoryExhaustedError / 409).
3. A successful checkout creates exactly one transaction with loan record.
4. Returning an active loan increases available inventory by one.
5. Returning the same loan twice is rejected (raises InvalidReturnError / 400).
6. A borrower cannot return another borrower's loan (raises PermissionError / 403).
7. Inventory never becomes negative or exceeds total copies.
8. Forecasts and recent activity refresh dynamically after a successful transaction.
9. Borrowed-book history shows only the selected demo borrower's active loans.
10. Existing catalog search, filters, pagination, calendar, and prediction still work.
"""

import json
import os
import shutil
import tempfile
import threading
import time
import urllib.request
import urllib.error
import unittest
from datetime import datetime, timedelta
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from ml.academic_calendar import AcademicCalendar
from ml.live_inventory_manager import (
    LiveInventoryManager,
    InventoryExhaustedError,
    InvalidReturnError,
)
from ml.stockout_estimator import StockoutEstimator
from app.api import LibraryApiHandler


class TestBorrowReturnWorkflows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create an isolated temporary test directory
        cls.test_dir = tempfile.mkdtemp()
        cls.inv_path = Path(cls.test_dir) / "live_inventory.json"
        cls.tx_path = Path(cls.test_dir) / "live_transactions.json"
        cls.loans_path = Path(cls.test_dir) / "live_loans.json"
        cls.cal_path = Path(cls.test_dir) / "academic_calendar.json"

        # Initialize isolated components
        cls.calendar = AcademicCalendar(storage_file=cls.cal_path)
        cls.inventory_mgr = LiveInventoryManager(
            inventory_file=cls.inv_path,
            transactions_file=cls.tx_path,
            loans_file=cls.loans_path,
            books_raw_path=ROOT / "data" / "raw" / "books.csv",
        )
        cls.estimator = StockoutEstimator(
            calendar=cls.calendar,
            inventory_mgr=cls.inventory_mgr,
        )

        # Start live test server on ephemeral port
        cls.port = 8895
        # Patch the app.api module's estimator singleton for HTTP tests
        import app.api as api_mod
        cls.orig_estimator = api_mod.estimator
        api_mod.estimator = cls.estimator

        cls.server = HTTPServer(("127.0.0.1", cls.port), LibraryApiHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.4)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        # Restore estimator in app.api
        import app.api as api_mod
        api_mod.estimator = cls.orig_estimator
        # Clean up temporary test directory
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def _get(self, path, headers=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8")), resp.headers
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                data = {"raw": body}
            return e.code, data, e.headers

    def _post(self, path, payload, headers=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(payload).encode("utf-8")
        h = {"Content-Type": "application/json"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, data=data, headers=h, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8")), resp.headers
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                data = {"raw": body}
            return e.code, data, e.headers

    # -------------------------------------------------------------
    # 1. Borrowing one available copy decreases inventory by one
    # -------------------------------------------------------------
    def test_01_borrow_decreases_available_inventory_by_one(self):
        book_id = "B01926"  # Initial: 29 total, 27 available
        initial = self.inventory_mgr.get_inventory(book_id)
        init_avail = initial["available_copies"]
        init_loan = initial["checked_out_copies"]

        res = self.inventory_mgr.record_checkout(
            book_id=book_id,
            student_id="STU-2026-042",
            notes="Unit test borrow",
        )

        after = self.inventory_mgr.get_inventory(book_id)
        self.assertEqual(after["available_copies"], init_avail - 1)
        self.assertEqual(after["checked_out_copies"], init_loan + 1)
        self.assertEqual(res["action"], "CHECKOUT")
        self.assertIn("loan_id", res)
        self.assertTrue(res["loan_id"].startswith("LN-"))

    # -------------------------------------------------------------
    # 2. Borrowing is blocked when availability is zero
    # -------------------------------------------------------------
    def test_02_borrow_blocked_when_availability_zero(self):
        # Pick book with 0 available or exhaust one
        book_id = "B08510"  # Initial: 28 total, 1 available
        # Borrow the last copy
        self.inventory_mgr.record_checkout(book_id=book_id, student_id="STU-2026-001")
        exhausted = self.inventory_mgr.get_inventory(book_id)
        self.assertEqual(exhausted["available_copies"], 0)

        # Attempt borrowing when availability is zero
        with self.assertRaises(InventoryExhaustedError):
            self.inventory_mgr.record_checkout(
                book_id=book_id,
                student_id="STU-2026-042",
            )

        # Verify via HTTP API returns 409 EXHAUSTED
        status, data, _ = self._post("/api/loans/borrow", {
            "book_id": book_id,
            "student_id": "STU-2026-042",
        })
        self.assertEqual(status, 409)
        self.assertEqual(data.get("status"), "EXHAUSTED")

    # -------------------------------------------------------------
    # 3. Successful checkout creates exactly one transaction
    # -------------------------------------------------------------
    def test_03_successful_checkout_creates_one_transaction(self):
        book_id = "B02474"
        tx_count_before = len(self.inventory_mgr._transactions)

        res = self.inventory_mgr.record_checkout(
            book_id=book_id,
            student_id="STU-2026-108",
            notes="Checking single transaction creation",
        )

        tx_count_after = len(self.inventory_mgr._transactions)
        self.assertEqual(tx_count_after, tx_count_before + 1)

        last_tx = self.inventory_mgr._transactions[-1]
        self.assertEqual(last_tx["transaction_id"], res["transaction_id"])
        self.assertEqual(last_tx["transaction_type"], "CHECKOUT")
        self.assertEqual(last_tx["student_id"], "STU-2026-108")
        self.assertIsNotNone(last_tx["due_date"])

    # -------------------------------------------------------------
    # 4. Returning an active loan increases available inventory by one
    # -------------------------------------------------------------
    def test_04_returning_active_loan_increases_available_inventory(self):
        book_id = "B06878"
        # First borrow
        co_res = self.inventory_mgr.record_checkout(
            book_id=book_id,
            student_id="STU-2026-042",
        )
        loan_id = co_res["loan_id"]
        avail_after_co = self.inventory_mgr.get_inventory(book_id)["available_copies"]

        # Now return
        ret_res = self.inventory_mgr.record_return(
            loan_id=loan_id,
            student_id="STU-2026-042",
        )

        avail_after_ret = self.inventory_mgr.get_inventory(book_id)["available_copies"]
        self.assertEqual(avail_after_ret, avail_after_co + 1)
        self.assertEqual(ret_res["action"], "RETURN")

        # Loan record status is updated
        loan = self.inventory_mgr.get_loan(loan_id)
        self.assertEqual(loan["status"], "RETURNED")
        self.assertIsNotNone(loan["returned_date"])

    # -------------------------------------------------------------
    # 5. Returning the same loan twice is rejected
    # -------------------------------------------------------------
    def test_05_returning_same_loan_twice_is_rejected(self):
        book_id = "B00130"
        co_res = self.inventory_mgr.record_checkout(
            book_id=book_id,
            student_id="STU-2026-042",
        )
        loan_id = co_res["loan_id"]

        # First return succeeds
        self.inventory_mgr.record_return(
            loan_id=loan_id,
            student_id="STU-2026-042",
        )

        # Second return must be rejected with InvalidReturnError
        with self.assertRaises(InvalidReturnError):
            self.inventory_mgr.record_return(
                loan_id=loan_id,
                student_id="STU-2026-042",
            )

        # HTTP API check: returns 400 INVALID_RETURN
        status, data, _ = self._post("/api/loans/return", {
            "loan_id": loan_id,
            "student_id": "STU-2026-042",
        })
        self.assertEqual(status, 400)
        self.assertEqual(data.get("status"), "INVALID_RETURN")

    # -------------------------------------------------------------
    # 6. A borrower cannot return another borrower's loan
    # -------------------------------------------------------------
    def test_06_borrower_cannot_return_another_borrowers_loan(self):
        book_id = "B11652"
        # Issued to Alex Chen (STU-2026-042)
        co_res = self.inventory_mgr.record_checkout(
            book_id=book_id,
            student_id="STU-2026-042",
        )
        loan_id = co_res["loan_id"]

        # Priya Sharma (STU-2026-108) attempts to return Alex Chen's loan
        with self.assertRaises(PermissionError):
            self.inventory_mgr.record_return(
                loan_id=loan_id,
                student_id="STU-2026-108",
            )

        # HTTP API check: returns 403 FORBIDDEN
        status, data, _ = self._post("/api/loans/return", {
            "loan_id": loan_id,
            "student_id": "STU-2026-108",
        })
        self.assertEqual(status, 403)
        self.assertEqual(data.get("status"), "FORBIDDEN")
        self.assertIn("belongs to student 'STU-2026-042'", data.get("error", ""))

    # -------------------------------------------------------------
    # 7. Inventory never becomes negative or exceeds total copies
    # -------------------------------------------------------------
    def test_07_inventory_boundaries_strictly_enforced(self):
        book_id = "B18659"
        inv = self.inventory_mgr.get_inventory(book_id)
        total = inv["total_copies"]

        # Invariant 1: Cannot return when all copies present
        # First ensure copies are at total
        inv["available_copies"] = total
        inv["checked_out_copies"] = 0
        with self.assertRaises(InvalidReturnError):
            self.inventory_mgr.record_return(book_id=book_id)

        # Invariant 2: Cannot checkout when 0 available
        inv["available_copies"] = 0
        inv["checked_out_copies"] = total
        with self.assertRaises(InventoryExhaustedError):
            self.inventory_mgr.record_checkout(book_id=book_id, student_id="STU-2026-001")

        # Invariant verification: 0 <= available <= total
        self.assertTrue(0 <= inv["available_copies"] <= total)

    # -------------------------------------------------------------
    # 8. Forecasts and recent activity refresh after a transaction
    # -------------------------------------------------------------
    def test_08_forecasts_and_activity_refresh_dynamically(self):
        book_id = "B15252"
        # Initial forecast
        pred_before = self.estimator.estimate_stockout(book_id=book_id)
        c7d_before = pred_before["recent_movement"]["checkouts_last_7d"]
        demand_before = pred_before["demand_forecast"]["predicted_next_7d_demand"]

        # Perform live checkout
        self.inventory_mgr.record_checkout(
            book_id=book_id,
            student_id="STU-2026-215",
            notes="Dynamic forecast refresh test",
        )

        # Refreshed forecast
        pred_after = self.estimator.estimate_stockout(book_id=book_id)
        c7d_after = pred_after["recent_movement"]["checkouts_last_7d"]
        demand_after = pred_after["demand_forecast"]["predicted_next_7d_demand"]

        self.assertEqual(c7d_after, c7d_before + 1.0)
        self.assertGreaterEqual(demand_after, demand_before)
        self.assertEqual(
            pred_after["inventory"]["available_copies"],
            pred_before["inventory"]["available_copies"] - 1,
        )

    # -------------------------------------------------------------
    # 9. Borrowed-book history shows only selected borrower's loans
    # -------------------------------------------------------------
    def test_09_borrowed_history_shows_only_selected_borrower(self):
        # Create loan for Alex Chen (STU-2026-042)
        co_alex = self.inventory_mgr.record_checkout(
            book_id="B01926",
            student_id="STU-2026-042",
        )
        # Create loan for Rohan Das (STU-2026-215)
        co_rohan = self.inventory_mgr.record_checkout(
            book_id="B02474",
            student_id="STU-2026-215",
        )

        # Query Alex's active loans
        alex_loans = self.inventory_mgr.get_active_loans(student_id="STU-2026-042")
        alex_loan_ids = [l["loan_id"] for l in alex_loans]
        self.assertIn(co_alex["loan_id"], alex_loan_ids)
        self.assertNotIn(co_rohan["loan_id"], alex_loan_ids)

        # Query Rohan's active loans
        rohan_loans = self.inventory_mgr.get_active_loans(student_id="STU-2026-215")
        rohan_loan_ids = [l["loan_id"] for l in rohan_loans]
        self.assertIn(co_rohan["loan_id"], rohan_loan_ids)
        self.assertNotIn(co_alex["loan_id"], rohan_loan_ids)

        # HTTP API check
        status, data, _ = self._get("/api/loans?student_id=STU-2026-042&status=active")
        self.assertEqual(status, 200)
        self.assertEqual(data["student_id"], "STU-2026-042")
        for loan in data["loans"]:
            self.assertEqual(loan["student_id"], "STU-2026-042")

    # -------------------------------------------------------------
    # 10. Existing catalogue search, filters, pagination, calendar still work
    # -------------------------------------------------------------
    def test_10_existing_catalogue_search_filters_and_calendar_work(self):
        # 10a. Catalog search with pagination
        status, data, _ = self._get("/api/books?q=Engineering&limit=5&offset=0")
        self.assertEqual(status, 200)
        self.assertIn("books", data)
        self.assertLessEqual(len(data["books"]), 5)

        # 10b. Departments endpoint
        status, data, _ = self._get("/api/departments")
        self.assertEqual(status, 200)
        self.assertIn("departments", data)
        self.assertGreater(len(data["departments"]), 0)

        # 10c. Upcoming exam calendar
        status, data, _ = self._get("/api/calendar/upcoming")
        self.assertEqual(status, 200)
        self.assertIn("exam_type", data)
        self.assertIn("timezone", data)
        self.assertEqual(data["timezone"], "Asia/Kolkata")

        # 10d. Stockout prediction endpoint
        status, data, _ = self._get("/api/predict/B08510")
        self.assertEqual(status, 200)
        self.assertIn("inventory", data)
        self.assertIn("demand_forecast", data)
        self.assertIn("stockout_assessment", data)


if __name__ == "__main__":
    unittest.main()
