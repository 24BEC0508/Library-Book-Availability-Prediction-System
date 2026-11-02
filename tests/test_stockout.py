"""
Unit tests for Stockout Estimator and Inventory Projection Logic.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from ml.academic_calendar import AcademicCalendar
from ml.live_inventory_manager import LiveInventoryManager
from ml.stockout_estimator import StockoutEstimator


class TestStockoutEstimator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()
        inv_path = Path(cls.test_dir) / "live_inventory.json"
        tx_path = Path(cls.test_dir) / "live_transactions.json"
        cal_path = Path(cls.test_dir) / "academic_calendar.json"
        calendar = AcademicCalendar(storage_file=cal_path)
        inv_mgr = LiveInventoryManager(
            inventory_file=inv_path,
            transactions_file=tx_path,
            books_raw_path=ROOT / "data" / "raw" / "books.csv",
        )
        cls.estimator = StockoutEstimator(calendar=calendar, inventory_mgr=inv_mgr)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_zero_available_copies_critical_boundary(self):
        """When available copies = 0, risk must be CRITICAL and days to stockout = 0."""
        res = self.estimator.estimate_stockout(
            book_id="B08510",
            available_copies=0,
            days_to_first_exam=7,
            exam_type="CAT2",
        )
        self.assertEqual(res["stockout_assessment"]["risk_level"], "CRITICAL")
        self.assertEqual(res["stockout_assessment"]["estimated_days_to_stockout"], 0)
        self.assertEqual(res["inventory"]["available_copies"], 0)

    def test_negative_demand_prevention(self):
        """Verify demand predictions are strictly clamped at 0.0 even under negative linear terms."""
        pred = self.estimator.predict_demand(
            department="CSE",
            total_copies=25,
            checkouts_1d=0.0,
            checkouts_3d=0.0,
            checkouts_7d=0.0,
            returns_7d=0.0,
            days_to_first_exam=100,  # far away
            exam_type="CAT1",
        )
        self.assertGreaterEqual(pred["bounded_prediction"], 0.0)

    def test_abundant_inventory_low_risk(self):
        """When available copies are large (e.g. 28), stockout risk must be LOW."""
        res = self.estimator.estimate_stockout(
            book_id="B01926",
            available_copies=28,
            days_to_first_exam=30,
            exam_type="FAT",
        )
        self.assertEqual(res["stockout_assessment"]["risk_level"], "LOW")
        self.assertIsNone(res["stockout_assessment"]["estimated_days_to_stockout"])
        self.assertEqual(res["stockout_assessment"]["confidence"], "HIGH")

    def test_fcfs_borrowing_policy_intact(self):
        """Confirm output explicitly enforces FCFS borrowing policy."""
        res = self.estimator.estimate_stockout(book_id="B08510")
        self.assertEqual(
            res["inventory"]["borrowing_policy"],
            "First Come, First Served (FCFS)",
        )

    def test_unknown_book_id_error_handling(self):
        """Verify non-existent book returns an error status instead of crashing."""
        res = self.estimator.estimate_stockout(book_id="NON_EXISTENT_BOOK_99999")
        self.assertIn("error", res)
        self.assertEqual(res["status"], "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
