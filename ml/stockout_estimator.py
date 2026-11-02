"""
Stockout Estimation and Dynamic Demand Prediction Inference Engine.

Integrates:
1. Live Library Movement: Retrieves fresh operational inventory and rolling transaction aggregates (1d, 3d, 7d).
2. Authoritative Academic Calendar: Obtains automatic countdown from Asia/Kolkata timezone.
3. Multi-Model Demand Forecasting:
   - Academic Baseline: Multiple Linear Regression (NumPy OLS from scratch).
   - Operational Dynamic Forecast: Incorporates recent borrowing velocity and historical exam surge rates.
4. Day-by-Day Physical Inventory Projection:
   - Evaluates daily trajectory over a 14-day forecast horizon.
   - Identifies the first date when projected inventory reaches zero.
   - Enforces FCFS borrowing and inventory boundaries.
"""

from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Dict, Any, Optional, List
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT / "model" / "regression_model.npz"
BOOKS_PATH = ROOT / "data" / "raw" / "books.csv"

from ml.academic_calendar import AcademicCalendar
from ml.live_inventory_manager import LiveInventoryManager

# Empirical historical checkout rates by department and exam period
DEPARTMENT_EXAM_BASELINES = {
    "CSE": {"CAT1": 0.045, "CAT2": 0.095, "FAT": 0.038, "NONE": 0.020},
    "ECE": {"CAT1": 0.040, "CAT2": 0.082, "FAT": 0.035, "NONE": 0.018},
    "IT": {"CAT1": 0.038, "CAT2": 0.078, "FAT": 0.032, "NONE": 0.018},
    "Common Engineering": {"CAT1": 0.036, "CAT2": 0.075, "FAT": 0.030, "NONE": 0.016},
    "EEE": {"CAT1": 0.035, "CAT2": 0.072, "FAT": 0.030, "NONE": 0.016},
    "Mathematics": {"CAT1": 0.035, "CAT2": 0.070, "FAT": 0.030, "NONE": 0.016},
    "Mechanical": {"CAT1": 0.032, "CAT2": 0.065, "FAT": 0.028, "NONE": 0.015},
    "Civil": {"CAT1": 0.030, "CAT2": 0.060, "FAT": 0.026, "NONE": 0.014},
    "Default": {"CAT1": 0.030, "CAT2": 0.062, "FAT": 0.026, "NONE": 0.014},
}


class StockoutEstimator:
    def __init__(
        self,
        model_path: Optional[Path] = None,
        books_path: Optional[Path] = None,
        calendar: Optional[AcademicCalendar] = None,
        inventory_mgr: Optional[LiveInventoryManager] = None,
    ):
        self.model_path = model_path or MODEL_PATH
        self.books_path = books_path or BOOKS_PATH
        self.calendar = calendar or AcademicCalendar()
        self.inventory_mgr = inventory_mgr or LiveInventoryManager()

        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Model artifact not found at {self.model_path}. Train model first."
            )

        # Load model weights and metadata
        saved = np.load(self.model_path, allow_pickle=True)
        self.coefficients = saved["coefficients"]
        self.feature_names = list(saved["feature_names"])
        self.numeric_features = list(saved["numeric_features"])
        self.departments = list(saved["departments"])
        self.exam_types = list(saved["exam_types"])  # ["CAT1", "CAT2", "FAT"]
        self.historical_mean = float(saved["historical_mean"])
        self.train_cutoff_date = str(saved["cutoff_date"])

        # Cache catalog DataFrame
        self.books_df = pd.read_csv(self.books_path)
        self._books_dict = self.books_df.set_index("book_id").to_dict(orient="index")

    def get_book_info(self, book_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve book record (prefers live operational store)."""
        bid = str(book_id).upper()
        live_book = self.inventory_mgr.get_book(bid)
        if live_book:
            return live_book
        return self._books_dict.get(bid)

    def predict_regression_demand(
        self,
        department: str,
        total_copies: int,
        checkouts_1d: float = 0.0,
        checkouts_3d: float = 0.0,
        checkouts_7d: float = 0.0,
        returns_7d: float = 0.0,
        demand_growth: Optional[float] = None,
        days_to_first_exam: int = 14,
        exam_type: str = "CAT2",
    ) -> Dict[str, float]:
        """
        Academic baseline: Evaluates Multiple Linear Regression model from scratch.
        Strictly preserves exact training feature order and one-hot encodings.
        """
        if demand_growth is None:
            demand_growth = (checkouts_3d / 3.0) - (checkouts_7d / 7.0)

        x_vals = [1.0]  # Intercept
        x_vals.extend([
            float(total_copies),
            float(max(0.0, checkouts_1d)),
            float(max(0.0, checkouts_3d)),
            float(max(0.0, checkouts_7d)),
            float(max(0.0, returns_7d)),
            float(demand_growth),
            float(max(0, days_to_first_exam)),
        ])

        for dept in self.departments:
            x_vals.append(1.0 if department == dept else 0.0)

        for etype in self.exam_types:
            x_vals.append(1.0 if exam_type == etype else 0.0)

        x_vec = np.array(x_vals, dtype=np.float64)
        raw_pred = float(x_vec @ self.coefficients)
        bounded_pred = max(0.0, raw_pred)

        return {
            "raw_prediction": raw_pred,
            "bounded_prediction": bounded_pred,
            "historical_baseline_mean": self.historical_mean,
        }

    # Backward compatibility alias
    predict_demand = predict_regression_demand

    def compute_dynamic_demand_forecast(
        self,
        department: str,
        total_copies: int,
        c1d: float,
        c3d: float,
        c7d: float,
        r7d: float,
        days_to_exam: int,
        model_exam_type: str,
        ml_demand: float,
    ) -> Dict[str, Any]:
        """
        Responsibly improves the demand forecast:
        Combines:
        1. Academic ML Regression baseline (ML shrinkage baseline).
        2. Recent observed checkout velocity (weighted recent run-rate).
        3. Historical exam surge rate for the department.
        """
        # 1. Recent observed velocity (annualized weekly rate)
        if c7d > 0 or c1d > 0:
            # Recent velocity puts strong weight on yesterday/today, medium on 3d, baseline on 7d
            v_recent = (0.5 * (c1d * 7.0)) + (0.3 * ((c3d / 3.0) * 7.0)) + (0.2 * c7d)
        else:
            v_recent = 0.0

        # 2. Historical Exam Baseline
        dept_rates = DEPARTMENT_EXAM_BASELINES.get(department, DEPARTMENT_EXAM_BASELINES["Default"])
        hist_exam_rate = dept_rates.get(model_exam_type, 0.02)

        # Proximity surge multiplier (e.g. 1.5x if within 7 days, 1.2x if within 14 days)
        if days_to_exam <= 7:
            exam_multiplier = 1.6 if model_exam_type == "CAT2" else 1.3
        elif days_to_exam <= 14:
            exam_multiplier = 1.3 if model_exam_type == "CAT2" else 1.15
        else:
            exam_multiplier = 1.0

        d_exam_surge = hist_exam_rate * exam_multiplier

        # 3. Composite Dynamic Demand Forecast
        if c7d > 0 or c1d > 0:
            # Active movement: dynamic forecast responds directly to observed checkouts
            dynamic_forecast = max(
                ml_demand,
                (0.65 * v_recent) + (0.35 * d_exam_surge)
            )
            forecast_origin = "Live Movement Velocity + Exam Baseline"
        else:
            # Dormant book: fallback to max of ML prediction and historical exam baseline
            dynamic_forecast = max(ml_demand, d_exam_surge)
            forecast_origin = "Historical Exam Season Baseline + ML Prior"

        return {
            "ml_regression_baseline": round(ml_demand, 4),
            "recent_velocity_run_rate": round(v_recent, 4),
            "historical_exam_baseline": round(d_exam_surge, 4),
            "operational_forecast_7d": round(dynamic_forecast, 4),
            "daily_burn_rate": round(dynamic_forecast / 7.0, 4),
            "forecast_origin": forecast_origin,
        }

    def project_daily_inventory(
        self,
        current_available: int,
        copies_on_loan: int,
        daily_burn_rate: float,
        recent_returns_7d: float,
        horizon_days: int = 14,
        start_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """
        Projects inventory day-by-day over the forecast horizon:
        projected_inventory[d] = projected_inventory[d-1] - expected_checkouts + expected_returns
        Identifies the earliest calendar date when copies reach 0.
        """
        ref_d = start_date or self.calendar.get_current_date()

        # Conservative expected returns: only count reliable returns if copies are on loan
        if copies_on_loan > 0 and recent_returns_7d > 0:
            daily_return_rate = min(copies_on_loan / 14.0, (recent_returns_7d / 7.0) * 0.5)
        else:
            daily_return_rate = 0.0

        trajectory = []
        inv = float(current_available)
        stockout_day: Optional[int] = None
        stockout_date_str: Optional[str] = None

        if current_available <= 0:
            stockout_day = 0
            stockout_date_str = ref_d.strftime("%Y-%m-%d")

        for d in range(1, horizon_days + 1):
            target_date = ref_d + timedelta(days=d)
            # Net daily movement
            inv = max(0.0, inv - daily_burn_rate + daily_return_rate)
            trajectory.append({
                "day_offset": d,
                "date": target_date.strftime("%Y-%m-%d"),
                "projected_available": round(inv, 2),
            })

            if inv <= 0.0 and stockout_day is None:
                stockout_day = d
                stockout_date_str = target_date.strftime("%Y-%m-%d")

        return {
            "forecast_horizon_days": horizon_days,
            "starting_available": current_available,
            "daily_burn_rate": daily_burn_rate,
            "daily_expected_returns": round(daily_return_rate, 4),
            "stockout_day": stockout_day,
            "stockout_date": stockout_date_str,
            "trajectory": trajectory,
        }

    # -------------------------------------------------------------
    # Main Inference API: Recalculates from Fresh Data
    # -------------------------------------------------------------
    def estimate_stockout(
        self,
        book_id: str,
        available_copies: Optional[int] = None,
        as_of_date: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Generates dynamic availability and stockout prediction based on
        live operational inventory and current transaction movement.
        """
        book_id = str(book_id).upper()
        now_dt = self.calendar.get_current_datetime()
        prediction_timestamp = now_dt.isoformat()
        ref_date = datetime.strptime(as_of_date, "%Y-%m-%d").date() if as_of_date else now_dt.date()

        # 1. Retrieve Authoritative Book Inventory State
        book = self.get_book_info(book_id)
        if not book:
            return {
                "error": f"Book ID '{book_id}' not found in library catalog.",
                "status": "NOT_FOUND",
            }

        total_copies = int(book["total_copies"])
        dept = str(book["department"])

        # Override or use authoritative available copies
        if available_copies is not None:
            curr_avail = max(0, min(total_copies, int(available_copies)))
        else:
            curr_avail = int(book.get("available_copies", 0))

        checked_out_copies = total_copies - curr_avail

        # 2. Retrieve Fresh Live Movement Aggregates (1d, 3d, 7d checkouts/returns)
        mv = self.inventory_mgr.get_live_movement_aggregates(book_id, as_of_datetime=now_dt)
        c1d = mv["checkouts_last_1d"]
        c3d = mv["checkouts_last_3d"]
        c7d = mv["checkouts_last_7d"]
        r7d = mv["returns_last_7d"]
        growth = mv["demand_growth"]
        last_tx_ts = mv["latest_transaction_timestamp"] or book.get("last_transaction_timestamp")

        # 3. Retrieve Authoritative Academic Calendar Countdown
        exam_info = self.calendar.get_next_applicable_exam(as_of_date=ref_date)
        exam_status = exam_info["status"]

        if exam_status == "IN_PROGRESS":
            days_to_exam = 0
            model_exam_type = exam_info["exam_type_model"]
            is_rush_active = True
            exam_surge_note = f"{exam_info['exam_type']} examination is active in library right now. Peak borrowing surge."
        elif exam_status == "UPCOMING":
            days_to_exam = exam_info["days_remaining"]
            model_exam_type = exam_info["exam_type_model"]
            is_rush_active = bool(days_to_exam <= 14)
            exam_surge_note = (
                f"CAT-2 in {days_to_exam} days. CAT-2 historically accounts for over 54% of exam checkouts."
                if model_exam_type == "CAT2"
                else f"Upcoming {exam_info['exam_type']} in {days_to_exam} days."
            )
        else:
            days_to_exam = 60
            model_exam_type = "CAT1"
            is_rush_active = False
            exam_surge_note = "No upcoming exams currently scheduled. Standard non-exam baseline circulation active."

        # 4. Academic Multiple Linear Regression Inference
        reg_dict = self.predict_regression_demand(
            department=dept,
            total_copies=total_copies,
            checkouts_1d=c1d,
            checkouts_3d=c3d,
            checkouts_7d=c7d,
            returns_7d=r7d,
            demand_growth=growth,
            days_to_first_exam=days_to_exam,
            exam_type=model_exam_type,
        )
        ml_demand = reg_dict["bounded_prediction"]

        # 5. Dynamic Operational Demand Forecast
        dyn_forecast = self.compute_dynamic_demand_forecast(
            department=dept,
            total_copies=total_copies,
            c1d=c1d,
            c3d=c3d,
            c7d=c7d,
            r7d=r7d,
            days_to_exam=days_to_exam,
            model_exam_type=model_exam_type,
            ml_demand=ml_demand,
        )
        op_demand_7d = dyn_forecast["operational_forecast_7d"]
        daily_burn_rate = dyn_forecast["daily_burn_rate"]

        # 6. Daily Trajectory and Stockout Date Projection (14-day horizon)
        proj = self.project_daily_inventory(
            current_available=curr_avail,
            copies_on_loan=checked_out_copies,
            daily_burn_rate=daily_burn_rate,
            recent_returns_7d=r7d,
            horizon_days=14,
            start_date=ref_date,
        )

        stockout_day = proj["stockout_day"]
        stockout_date_str = proj["stockout_date"]

        # 7. Multi-Tier Risk Level & Plain-English Explanations
        risk_level: str
        warning_msg: str
        confidence: str
        explanation: str

        if curr_avail == 0:
            risk_level = "CRITICAL"
            warning_msg = "Book is currently completely out of stock (0 copies available)."
            confidence = "HIGH"
            explanation = "Deterministic inventory count confirms 0 copies available right now."

        elif stockout_day is not None and stockout_day <= 14:
            risk_level = "HIGH"
            warning_msg = (
                f"High risk of shortage: projected burn rate of {daily_burn_rate:.2f} copies/day "
                f"exhausts inventory on {stockout_date_str} (in {stockout_day} days)."
            )
            confidence = "HIGH" if mv["has_recent_movement"] else "MEDIUM"
            explanation = (
                f"Active movement detected ({int(c7d)} checkouts in last 7d, {int(c1d)} in last 24h). "
                f"Projected to stockout within the supported 14-day horizon."
            )

        elif curr_avail <= 2 or op_demand_7d >= (0.6 * curr_avail):
            risk_level = "MODERATE"
            warning_msg = (
                f"Moderate shortage risk: available inventory is low ({curr_avail} copies remaining). "
                f"No stockout date reached within supported 14-day forecast horizon."
            )
            confidence = "MEDIUM"
            explanation = (
                f"Inventory buffer is thin ({curr_avail} copies). Projected 7-day demand is {op_demand_7d:.2f} checkouts. "
                f"If checkouts surge near {exam_info.get('exam_type') or 'exams'}, availability will tighten."
            )

        else:
            risk_level = "LOW"
            warning_msg = (
                f"Sufficient inventory available ({curr_avail} copies). "
                f"No stockout date available within the supported forecast horizon."
            )
            confidence = "HIGH" if curr_avail >= 5 else "MEDIUM"
            explanation = (
                f"Current available inventory ({curr_avail} copies) substantially exceeds projected 7-day demand "
                f"({op_demand_7d:.2f} checkouts). Stockout is unlikely."
            )

        # Activity change narrative
        if mv["has_recent_movement"]:
            activity_impact_note = (
                f"Recent movement ({int(c1d)} checkouts in last 24h, {int(c7d)} in last 7d) "
                f"increased weekly velocity to {dyn_forecast['recent_velocity_run_rate']:.2f} checkouts/week."
            )
        else:
            activity_impact_note = (
                "No checkouts recorded in the last 7 calendar days. Forecast is anchored to baseline exam season rate."
            )

        return {
            "book_id": book_id,
            "title": str(book["title"]),
            "author": str(book["author"]),
            "department": dept,
            "inventory": {
                "total_copies": total_copies,
                "available_copies": curr_avail,
                "checked_out_copies": checked_out_copies,
                "borrowing_policy": "First Come, First Served (FCFS)",
            },
            "recent_movement": {
                "checkouts_last_1d": c1d,
                "checkouts_last_3d": c3d,
                "checkouts_last_7d": c7d,
                "returns_last_7d": r7d,
                "demand_growth": growth,
                "has_recent_movement": mv["has_recent_movement"],
                "activity_impact_note": activity_impact_note,
            },
            "demand_forecast": {
                "predicted_next_7d_demand": round(op_demand_7d, 3),
                "daily_burn_rate": round(daily_burn_rate, 3),
                "ml_regression_baseline": round(ml_demand, 4),
                "forecast_origin": dyn_forecast["forecast_origin"],
                "historical_training_mean": round(self.historical_mean, 4),
            },
            "ml_prediction": {
                "predicted_next_7d_checkouts": round(op_demand_7d, 3),
                "daily_burn_rate": round(daily_burn_rate, 3),
                "methodology": dyn_forecast["forecast_origin"],
                "ml_regression_baseline": round(ml_demand, 4),
            },
            "stockout_assessment": {
                "risk_level": risk_level,
                "forecast_horizon_days": proj["forecast_horizon_days"],
                "estimated_days_to_stockout": stockout_day,
                "estimated_stockout_date": stockout_date_str,
                "warning_message": warning_msg,
                "confidence": confidence,
                "explanation": explanation,
            },
            "academic_calendar": {
                "status": exam_info["status"],
                "exam_type": exam_info.get("exam_type"),
                "start_date": exam_info.get("start_date"),
                "end_date": exam_info.get("end_date"),
                "days_remaining": exam_info.get("days_remaining"),
                "countdown_label": exam_info["label"],
                "is_exam_rush_period": is_rush_active,
                "historical_cycle_note": exam_surge_note,
                "timezone": exam_info["timezone"],
                "as_of_date": exam_info["as_of_date"],
            },
            "metadata": {
                "prediction_timestamp": prediction_timestamp,
                "latest_transaction_timestamp": last_tx_ts,
                "environment_mode": "LIVE_OPERATIONAL_DEMO",
            },
            "as_of_date": ref_date.strftime("%Y-%m-%d"),
        }
