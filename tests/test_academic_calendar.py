"""
Unit tests for Admin-Managed Academic Calendar and Automatic Exam Countdown.

Verifies:
1. The countdown is correct for today's date (Asia/Kolkata).
2. The countdown updates dynamically when reference date changes.
3. The next applicable exam is selected from multiple calendar entries.
4. Completed and cancelled exams are excluded appropriately.
5. An exam currently in progress is represented as IN_PROGRESS with 0 days remaining.
6. No upcoming exam produces a safe non-exam fallback.
7. Timezone boundaries are handled consistently (Asia/Kolkata).
8. Student requests cannot modify calendar records (401 Unauthorized).
9. Administrator updates affect subsequent predictions without retraining the model.
10. Countdown is never taken from student input fields.
"""

import json
import unittest
import tempfile
from pathlib import Path
from datetime import date, datetime, timedelta
import zoneinfo

ROOT = Path(__file__).resolve().parent.parent
from ml.academic_calendar import AcademicCalendar, DEFAULT_ADMIN_KEY
from ml.stockout_estimator import StockoutEstimator
from aws.lambda_function import lambda_handler


class TestAcademicCalendar(unittest.TestCase):
    def setUp(self):
        # Use a temporary file for isolated test execution
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cal_file = Path(self.temp_dir.name) / "test_calendar.json"
        self.calendar = AcademicCalendar(storage_file=self.cal_file, tz_name="Asia/Kolkata")
        self.estimator = StockoutEstimator(calendar=self.calendar)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_countdown_correct_for_today(self):
        """1. Verify countdown calculation relative to Asia/Kolkata today."""
        today = self.calendar.get_current_date()
        next_exam = self.calendar.get_next_applicable_exam()

        self.assertIn(next_exam["status"], ["UPCOMING", "IN_PROGRESS"])
        if next_exam["status"] == "UPCOMING":
            start_d = datetime.strptime(next_exam["start_date"], "%Y-%m-%d").date()
            expected_days = (start_d - today).days
            self.assertEqual(next_exam["days_remaining"], expected_days)
            self.assertGreater(next_exam["days_remaining"], 0)

    def test_countdown_updates_when_date_changes(self):
        """2. Verify countdown updates dynamically when date changes."""
        # Query on 2026-11-01 -> Fall FAT on 2026-11-20 should be 19 days
        res_nov1 = self.calendar.get_next_applicable_exam(as_of_date=date(2026, 11, 1))
        self.assertEqual(res_nov1["exam_type"], "FAT")
        self.assertEqual(res_nov1["days_remaining"], 19)

        # Query on 2026-11-15 -> Fall FAT on 2026-11-20 should be 5 days
        res_nov15 = self.calendar.get_next_applicable_exam(as_of_date=date(2026, 11, 15))
        self.assertEqual(res_nov15["exam_type"], "FAT")
        self.assertEqual(res_nov15["days_remaining"], 5)

        # Query on 2026-11-19 -> 1 day remaining
        res_nov19 = self.calendar.get_next_applicable_exam(as_of_date=date(2026, 11, 19))
        self.assertEqual(res_nov19["days_remaining"], 1)

    def test_next_applicable_exam_selected_from_multiple_entries(self):
        """3. Next chronological exam is selected among multiple calendar entries."""
        # On 2026-12-05 (after Fall FAT 2026-11-30), next exam should be Winter CAT-1 on 2027-01-26
        res = self.calendar.get_next_applicable_exam(as_of_date=date(2026, 12, 5))
        self.assertEqual(res["exam_type"], "CAT-1")
        self.assertEqual(res["start_date"], "2027-01-26")
        self.assertEqual(res["days_remaining"], 52)

    def test_completed_and_cancelled_exams_excluded(self):
        """4. Completed and cancelled exams are excluded from selection."""
        # Deactivate/cancel Fall FAT
        self.calendar.deactivate_entry("CAL-2026F-FAT", reason="Cancelled by senate")

        # Now on 2026-11-01, Fall FAT is cancelled, so it must skip directly to Winter CAT-1 (2027-01-26)
        res = self.calendar.get_next_applicable_exam(as_of_date=date(2026, 11, 1))
        self.assertEqual(res["exam_type"], "CAT-1")
        self.assertEqual(res["start_date"], "2027-01-26")

    def test_exam_in_progress_representation(self):
        """5. Exam happening now is represented as IN_PROGRESS with 0 days remaining."""
        # Fall FAT runs 2026-11-20 to 2026-11-30
        res_in_prog = self.calendar.get_next_applicable_exam(as_of_date=date(2026, 11, 25))
        self.assertEqual(res_in_prog["status"], "IN_PROGRESS")
        self.assertEqual(res_in_prog["days_remaining"], 0)
        self.assertIn("in progress", res_in_prog["label"].lower())

    def test_no_upcoming_exam_safe_fallback(self):
        """6. When no upcoming exams exist, safe fallback is returned."""
        # Query far in future (e.g. 2030) after all scheduled exams
        res_empty = self.calendar.get_next_applicable_exam(as_of_date=date(2030, 1, 1))
        self.assertEqual(res_empty["status"], "NO_EXAM_SCHEDULED")
        self.assertIsNone(res_empty["days_remaining"])
        self.assertIn("No upcoming exam scheduled", res_empty["label"])

        # Check inference with safe fallback
        pred = self.estimator.estimate_stockout(book_id="B08510", as_of_date="2030-01-01")
        self.assertEqual(pred["academic_calendar"]["status"], "NO_EXAM_SCHEDULED")
        self.assertFalse(pred["academic_calendar"]["is_exam_rush_period"])

    def test_timezone_boundary_consistency(self):
        """7. Verify timezone handling consistently respects Asia/Kolkata."""
        kolkata_dt = self.calendar.get_current_datetime()
        self.assertEqual(str(kolkata_dt.tzinfo), "Asia/Kolkata")
        # Offset should be +05:30
        offset = kolkata_dt.utcoffset()
        self.assertEqual(offset, timedelta(hours=5, minutes=30))

    def test_student_cannot_modify_calendar(self):
        """8. Unauthenticated student requests are rejected with 401."""
        # Student tries to post a new exam without key
        event_student = {
            "httpMethod": "POST",
            "path": "/admin/calendar",
            "body": json.dumps({
                "academic_year": "2026-2027",
                "semester": "Fall",
                "exam_type": "CAT-1",
                "start_date": "2026-10-15",
                "end_date": "2026-10-20",
            }),
            "headers": {},
        }
        res = lambda_handler(event_student, None)
        self.assertEqual(res["statusCode"], 401)
        body = json.loads(res["body"])
        self.assertIn("Unauthorized", body["error"])

    def test_admin_updates_affect_predictions_without_retraining(self):
        """9. Administrator date adjustments immediately affect subsequent predictions."""
        # Initial prediction as of 2026-10-10: upcoming exam is FAT in 41 days
        initial_pred = self.estimator.estimate_stockout(book_id="B08510", as_of_date="2026-10-10")
        self.assertEqual(initial_pred["academic_calendar"]["days_remaining"], 41)

        # Admin shifts FAT start date earlier to 2026-10-20 (10 days away - rush period!)
        self.calendar.update_entry("CAL-2026F-FAT", {
            "start_date": "2026-10-20",
            "end_date": "2026-10-28",
            "notes": "Emergency schedule shift by registrar",
        })

        # Subsequent prediction without retraining
        updated_pred = self.estimator.estimate_stockout(book_id="B08510", as_of_date="2026-10-10")
        self.assertEqual(updated_pred["academic_calendar"]["days_remaining"], 10)
        self.assertTrue(updated_pred["academic_calendar"]["is_exam_rush_period"])

    def test_countdown_never_taken_from_student_input(self):
        """10. Confirm prediction ignores student-supplied days_to_first_exam parameter."""
        # Malicious student tries to claim exam is in 1 day or 999 days
        tampered_pred = self.estimator.estimate_stockout(
            book_id="B08510",
            as_of_date="2026-10-10",
            days_to_first_exam=1,  # Fake countdown supplied
            exam_type="CAT1",      # Fake exam type supplied
        )
        # Must strictly use calendar value (41 days until FAT), NOT the student-supplied value (1)
        self.assertEqual(tampered_pred["academic_calendar"]["days_remaining"], 41)
        self.assertEqual(tampered_pred["academic_calendar"]["exam_type"], "FAT")


if __name__ == "__main__":
    unittest.main()
