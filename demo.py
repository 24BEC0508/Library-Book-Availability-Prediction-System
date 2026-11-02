"""
Autonomous Demo & End-to-End Walkthrough Script.

Demonstrates:
1. Model loading and metadata inspection.
2. Authoritative academic calendar and automatic countdown (Asia/Kolkata).
3. Inference across four real inventory scenarios (Critical, High Risk, Moderate Risk, Safe).
4. Administrator calendar correction and dynamic prediction adjustment without retraining.
5. FCFS borrowing policy guarantee.
"""

from pathlib import Path
import json
from ml.stockout_estimator import StockoutEstimator

ROOT = Path(__file__).resolve().parent


def run_demo():
    print("=" * 75)
    print("LIBRARY BOOK AVAILABILITY PREDICTION & STOCKOUT EARLY WARNING")
    print("WITH AUTHORITATIVE ADMIN ACADEMIC CALENDAR & AUTOMATIC COUNTDOWN")
    print("=" * 75)

    estimator = StockoutEstimator()
    cal = estimator.calendar
    today = cal.get_current_date()
    next_exam = cal.get_next_applicable_exam()

    print(f"Current Date (Asia/Kolkata): {today}")
    print(f"Active Academic Schedule: {next_exam['label']}")
    print(f"Calendar Status: {next_exam['status']} | Model Feature: {next_exam['exam_type_model']}")
    print(f"Model: Multiple Linear Regression (NumPy lstsq from scratch)")
    print(f"Training Cutoff: before {estimator.train_cutoff_date}")
    print(f"Catalog Size: {len(estimator.books_df):,} books across {len(estimator.departments)} departments")
    print("=" * 75)

    test_cases = [
        {
            "desc": "SCENARIO 1: CRITICAL - BOOK CURRENTLY OUT OF STOCK",
            "book_id": "B16469",
            "avail": 0,
        },
        {
            "desc": "SCENARIO 2: HIGH SHORTAGE RISK - LOW INVENTORY BEFORE PEAK EXAM",
            "book_id": "B08510",
            "avail": 1,
        },
        {
            "desc": "SCENARIO 3: MODERATE SHORTAGE RISK - THIN INVENTORY BUFFER",
            "book_id": "B00037",
            "avail": 2,
        },
        {
            "desc": "SCENARIO 4: ABUNDANT INVENTORY - LOW STOCKOUT RISK",
            "book_id": "B01926",
            "avail": 27,
        },
    ]

    for case in test_cases:
        print(f"\n{case['desc']}")
        print("-" * 75)
        res = estimator.estimate_stockout(
            book_id=case["book_id"],
            available_copies=case["avail"],
        )

        inv = res["inventory"]
        ml = res["ml_prediction"]
        risk = res["stockout_assessment"]
        acad = res["academic_calendar"]

        print(f"Book: [{res['book_id']}] \"{res['title']}\" by {res['author']} ({res['department']})")
        print(f"Current Inventory: {inv['available_copies']} available / {inv['total_copies']} total copies ({inv['checked_out_copies']} on loan)")
        print(f"Policy: {inv['borrowing_policy']}")
        print(f"Academic Countdown: {acad['countdown_label']} (Rush Period: {acad['is_exam_rush_period']})")
        print(f"ML 7-Day Predicted Demand: {ml['predicted_next_7d_checkouts']} checkouts (Burn rate: {ml['daily_burn_rate']} copies/day)")
        print(f"Stockout Risk Level: >>> {risk['risk_level']} <<<")
        print(f"Estimated Stockout: {risk['estimated_days_to_stockout']} days ({risk['estimated_stockout_date']})")
        print(f"Warning: {risk['warning_message']}")
        print(f"Explanation & Uncertainty: {risk['explanation']} [Confidence: {risk['confidence']}]")

    print("\n" + "=" * 75)
    print("ADMINISTRATIVE CALENDAR ADJUSTMENT DEMO (WITHOUT MODEL RETRAINING)")
    print("=" * 75)
    print("Simulating University Admin shifting exam dates via Admin Portal:")
    original_fat = cal.get_entry("CAL-2026F-FAT")
    print(f"Before shift: Fall FAT starts on {original_fat['start_date']} ({next_exam['days_remaining']} days remaining)")

    # Shift exam to 10 days away
    shift_date = (today + timedelta(days=10)).strftime("%Y-%m-%d")
    cal.update_entry("CAL-2026F-FAT", {
        "start_date": shift_date,
        "end_date": (today + timedelta(days=18)).strftime("%Y-%m-%d"),
        "notes": "Emergency schedule shift by registrar",
    })
    new_exam = cal.get_next_applicable_exam()
    print(f"After shift:  Fall FAT starts on {new_exam['start_date']} ({new_exam['days_remaining']} days remaining)")

    # Run inference again
    pred_shifted = estimator.estimate_stockout(book_id="B08510", available_copies=1)
    print(f"Subsequent Demand Prediction for B08510: {pred_shifted['ml_prediction']['predicted_next_7d_checkouts']} checkouts")
    print(f"Countdown Label: {pred_shifted['academic_calendar']['countdown_label']}")
    print(f"Rush Period Active: {pred_shifted['academic_calendar']['is_exam_rush_period']}")

    # Restore original dates
    cal.update_entry("CAL-2026F-FAT", {
        "start_date": "2026-11-20",
        "end_date": "2026-11-30",
        "notes": "Final Assessment Test (Fall 2026)",
    })
    print("Restored original academic schedule.")

    print("\n" + "=" * 75)
    print("DEMO COMPLETE: HOW TO RUN LOCAL API & WEB APPLICATION")
    print("=" * 75)
    print("1. Start Local Web Portal:   python app/api.py")
    print("   Open in browser:          http://127.0.0.1:8000")
    print("2. Run 26 Unit Tests:        python -m unittest discover tests")
    print("3. Check Upcoming Calendar:  curl http://127.0.0.1:8000/api/calendar/upcoming")
    print("=" * 75)


if __name__ == "__main__":
    from datetime import timedelta
    run_demo()
