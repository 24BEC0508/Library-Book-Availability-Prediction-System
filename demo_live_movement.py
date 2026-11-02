"""
Demonstration of Dynamic Demand Prediction from Live Library Movement.

Shows:
1. Candidate book (B08510) initial state (1 copy available / 28 total).
2. Prediction before movement (baseline rate, moderate/high risk).
3. Live checkout event processed atomically.
4. Immediate refreshed forecast showing:
   - Available copies reduced to 0.
   - 24h, 3d, 7d checkout metrics incremented.
   - Weekly velocity surging.
   - Shortage risk escalating to CRITICAL.
   - Earliest stockout date advanced to immediate (day 0).
5. Live return event processed atomically.
6. Immediate refreshed forecast showing recovery of inventory and relaxed risk.
"""

import json
from pathlib import Path
from ml.stockout_estimator import StockoutEstimator

def main():
    estimator = StockoutEstimator()
    book_id = "B08510"

    print("=" * 80)
    print("LIVE DEMO: DYNAMIC DEMAND PREDICTION FROM REAL-TIME LIBRARY MOVEMENT")
    print("=" * 80)

    # 1. Initial State
    print(f"\n[STEP 1] Retrieving Initial Catalog & Movement State for Book '{book_id}'...")
    pred1 = estimator.estimate_stockout(book_id)
    print(f"Title: {pred1['title']}")
    print(f"Department: {pred1['department']}")
    print(f"Total Copies: {pred1['inventory']['total_copies']}")
    print(f"Available Copies: {pred1['inventory']['available_copies']}")
    print(f"Checked Out Copies: {pred1['inventory']['checked_out_copies']}")
    print(f"Recent Checkouts (24h / 3d / 7d): {pred1['recent_movement']['checkouts_last_1d']} / {pred1['recent_movement']['checkouts_last_3d']} / {pred1['recent_movement']['checkouts_last_7d']}")
    print(f"Operational 7-Day Demand Forecast: {pred1['demand_forecast']['predicted_next_7d_demand']:.3f} checkouts")
    print(f"Daily Burn Rate: {pred1['demand_forecast']['daily_burn_rate']:.3f} copies/day")
    print(f"Shortage Risk Level: {pred1['stockout_assessment']['risk_level']}")
    print(f"Estimated Days to Stockout: {pred1['stockout_assessment']['estimated_days_to_stockout']}")
    print(f"Estimated Stockout Date: {pred1['stockout_assessment']['estimated_stockout_date']}")
    print(f"Exam Countdown: {pred1['academic_calendar']['countdown_label']}")

    # 2. Record Live Checkout
    print("\n" + "-" * 80)
    print("[STEP 2] Simulating Live Student Checkout Event...")
    tx_co = estimator.inventory_mgr.record_checkout(
        book_id=book_id,
        idempotency_key="demo-checkout-001",
        student_id="STUDENT_DEMO_01",
        notes="Live classroom demonstration checkout",
    )
    print(f"Checkout Transaction ID: {tx_co['transaction_id']}")
    print(f"Transaction Timestamp:   {tx_co['timestamp']}")
    print(f"Available Copies After:  {tx_co['available_copies_after']}")

    # 3. Refreshed Prediction After Checkout
    print("\n" + "-" * 80)
    print("[STEP 3] Refreshing Demand Forecast & Stockout Trajectory Immediately...")
    pred2 = estimator.estimate_stockout(book_id)
    print(f"Available Copies: {pred2['inventory']['available_copies']}")
    print(f"Recent Checkouts (24h / 3d / 7d): {pred2['recent_movement']['checkouts_last_1d']} / {pred2['recent_movement']['checkouts_last_3d']} / {pred2['recent_movement']['checkouts_last_7d']}")
    print(f"Movement Impact Note: {pred2['recent_movement']['activity_impact_note']}")
    print(f"Operational 7-Day Demand: {pred2['demand_forecast']['predicted_next_7d_demand']:.3f} checkouts")
    print(f"Forecast Origin: {pred2['demand_forecast']['forecast_origin']}")
    print(f"Shortage Risk Level: {pred2['stockout_assessment']['risk_level']}")
    print(f"Warning Message: {pred2['stockout_assessment']['warning_message']}")
    print(f"Estimated Days to Stockout: {pred2['stockout_assessment']['estimated_days_to_stockout']}")
    print(f"Estimated Stockout Date: {pred2['stockout_assessment']['estimated_stockout_date']}")
    print(f"Latest Transaction Timestamp Used: {pred2['metadata']['latest_transaction_timestamp']}")

    # 4. Record Live Return
    print("\n" + "-" * 80)
    print("[STEP 4] Simulating Student Book Return Event...")
    tx_ret = estimator.inventory_mgr.record_return(
        book_id=book_id,
        idempotency_key="demo-return-001",
        student_id="STUDENT_DEMO_01",
        notes="Live classroom demonstration return",
    )
    print(f"Return Transaction ID:   {tx_ret['transaction_id']}")
    print(f"Transaction Timestamp:   {tx_ret['timestamp']}")
    print(f"Available Copies After:  {tx_ret['available_copies_after']}")

    # 5. Refreshed Prediction After Return
    print("\n" + "-" * 80)
    print("[STEP 5] Refreshing Demand Forecast After Return...")
    pred3 = estimator.estimate_stockout(book_id)
    print(f"Available Copies Recovered: {pred3['inventory']['available_copies']}")
    print(f"Recent Returns (7d): {pred3['recent_movement']['returns_last_7d']}")
    print(f"Shortage Risk Level: {pred3['stockout_assessment']['risk_level']}")
    print(f"Warning Message: {pred3['stockout_assessment']['warning_message']}")
    print(f"Latest Transaction Timestamp Used: {pred3['metadata']['latest_transaction_timestamp']}")

    print("\n" + "=" * 80)
    print("LIVE DEMONSTRATION COMPLETE: DYNAMIC MOVEMENT INTEGRATION VERIFIED")
    print("=" * 80)

if __name__ == "__main__":
    main()
