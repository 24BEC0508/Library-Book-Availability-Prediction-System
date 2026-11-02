"""
AWS Lambda Handler for Library Book Availability & Stockout Prediction.

Architecture: API Gateway (HTTP API v2) -> AWS Lambda -> DynamoDB & S3
Supported Endpoints:
- GET    /health                     -> Health check & system metadata
- GET    /departments                -> Unique academic departments
- GET    /books                      -> Catalog search & pagination (q, department, limit, offset)
- GET    /books/{book_id}            -> Book inventory details
- GET    /inventory/{book_id}        -> Live operational inventory & rolling movement
- GET    /calendar/upcoming          -> Public: next applicable exam & automatic countdown
- GET    /calendar                   -> Public: scheduled academic calendar list
- POST   /predict                    -> ML Demand prediction with automatic exam countdown
- GET    /predict/{book_id}          -> ML Demand prediction with automatic exam countdown
- GET    /loans                      -> Student loan history & active loans (student_id, status)
- POST   /loans/borrow               -> Borrow book / Record checkout (atomic conditional decrement)
- POST   /loans/return               -> Return book / Record return (atomic conditional increment)
- GET    /admin/stats                -> Institutional circulation metrics (admin protected)
- GET    /admin/transactions         -> Institutional transaction audit trail
- GET    /admin/calendar             -> Admin: all calendar entries (protected)
- POST   /admin/calendar             -> Admin: create calendar entry (protected)
- PUT    /admin/calendar/{id}        -> Admin: update/correct entry (protected)
- DELETE /admin/calendar/{id}        -> Admin: deactivate entry (protected)
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional

CURRENT_DIR = Path(__file__).resolve().parent
# When deployed at the root of the Lambda package, 'ml' is a sibling directory.
# When running locally from aws/ (AWS-Project/aws/lambda_function.py), 'ml' is in parent directory.
if (CURRENT_DIR / "ml").is_dir():
    PROJECT_ROOT = CURRENT_DIR
else:
    PROJECT_ROOT = CURRENT_DIR.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    boto3 = None
    HAS_BOTO3 = False

from ml.stockout_estimator import StockoutEstimator
from ml.academic_calendar import AcademicCalendar
from ml.live_inventory_manager import InventoryExhaustedError, InvalidReturnError

_ESTIMATOR: Optional[StockoutEstimator] = None


def get_estimator() -> StockoutEstimator:
    """Lazy loader for cached StockoutEstimator instance with S3 model artifact support."""
    global _ESTIMATOR
    if _ESTIMATOR is None:
        model_path = Path(os.environ.get("MODEL_LOCAL_PATH", PROJECT_ROOT / "model" / "regression_model.npz"))
        books_path = Path(os.environ.get("BOOKS_LOCAL_PATH", PROJECT_ROOT / "data" / "raw" / "books.csv"))

        s3_bucket = os.environ.get("S3_BUCKET_NAME", "library-management-ml-pred")
        s3_model_key = os.environ.get("S3_MODEL_KEY", "model/regression_model.npz")
        fallback_s3_key = "model/v1/regression_model.npz" if s3_model_key == "model/regression_model.npz" else "model/regression_model.npz"

        if s3_bucket and HAS_BOTO3:
            s3 = boto3.client("s3")
            temp_dir = Path("/tmp") if Path("/tmp").exists() else Path(tempfile.gettempdir())
            temp_path = temp_dir / "regression_model.npz"
            downloaded = False
            last_err = None
            for key in [s3_model_key, fallback_s3_key]:
                try:
                    s3.download_file(s3_bucket, key, str(temp_path))
                    model_path = temp_path
                    downloaded = True
                    print(f"[Lambda] Loaded model from s3://{s3_bucket}/{key}")
                    break
                except Exception as e:
                    last_err = e
                    print(f"[Lambda] S3 download attempt for s3://{s3_bucket}/{key} failed: {e}")
            if not downloaded:
                if os.environ.get("APP_MODE") == "AWS":
                    raise RuntimeError(f"Failed to load model from S3 bucket '{s3_bucket}': {last_err}")
                print(f"[Lambda] Using local model artifact at {model_path}")

        _ESTIMATOR = StockoutEstimator(model_path=model_path, books_path=books_path)
    return _ESTIMATOR


STATIC_DIR = CURRENT_DIR / "static"
if not STATIC_DIR.exists():
    STATIC_DIR = PROJECT_ROOT / "app" / "static"


def make_file_response(content: str, content_type: str) -> Dict[str, Any]:
    """Helper for formatted static asset response with CORS."""
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": content_type,
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=300",
        },
        "body": content,
    }


def make_response(status_code: int, body: Any) -> Dict[str, Any]:
    """Helper for formatted API Gateway JSON response with CORS."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-Admin-Key, Authorization",
        },
        "body": json.dumps(body, indent=2),
    }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Main AWS Lambda Entrypoint."""
    try:
        http_method = (
            event.get("httpMethod")
            or event.get("requestContext", {}).get("http", {}).get("method", "GET")
        ).upper()

        raw_path = event.get("path") or event.get("rawPath") or "/"
        query_params = event.get("queryStringParameters") or {}
        headers = event.get("headers") or {}

        # Handle CORS preflight
        if http_method == "OPTIONS":
            return make_response(200, {"message": "CORS preflight successful"})

        # -------------------------------------------------------------
        # Route: Static Cloud Frontend Hosting (Student & Admin Portals)
        # -------------------------------------------------------------
        if http_method == "GET":
            if raw_path in ["/", "/index.html"]:
                idx_file = STATIC_DIR / "index.html"
                if idx_file.exists():
                    return make_file_response(idx_file.read_text(encoding="utf-8"), "text/html; charset=utf-8")

            if raw_path in ["/admin", "/admin/", "/admin.html"]:
                adm_file = STATIC_DIR / "admin.html"
                if adm_file.exists():
                    return make_file_response(adm_file.read_text(encoding="utf-8"), "text/html; charset=utf-8")

            if raw_path.startswith("/css/"):
                css_file = STATIC_DIR / raw_path.lstrip("/")
                if css_file.exists() and css_file.suffix == ".css":
                    return make_file_response(css_file.read_text(encoding="utf-8"), "text/css; charset=utf-8")

            if raw_path.startswith("/js/"):
                js_file = STATIC_DIR / raw_path.lstrip("/")
                if js_file.exists() and js_file.suffix == ".js":
                    return make_file_response(js_file.read_text(encoding="utf-8"), "application/javascript; charset=utf-8")

        estimator = get_estimator()
        admin_key = (
            headers.get("x-admin-key")
            or headers.get("X-Admin-Key")
            or headers.get("authorization")
            or headers.get("Authorization")
        )
        is_admin = estimator.calendar.verify_admin_key(admin_key)

        # -------------------------------------------------------------
        # Route: Health check
        # -------------------------------------------------------------
        if raw_path in ["/health", "/api/health"]:
            next_exam = estimator.calendar.get_next_applicable_exam()
            return make_response(200, {
                "status": "HEALTHY",
                "service": "Library Book Availability Prediction API",
                "model": {
                    "methodology": "Multiple Linear Regression (NumPy OLS)",
                    "training_cutoff": estimator.train_cutoff_date,
                    "historical_baseline_mean": round(estimator.historical_mean, 6),
                    "feature_count": len(estimator.feature_names),
                },
                "total_catalog_books": len(estimator.books_df),
                "borrowing_policy": "First Come, First Served (FCFS)",
                "academic_calendar": {
                    "next_exam": next_exam["label"],
                    "status": next_exam["status"],
                    "timezone": next_exam["timezone"],
                },
                "environment": {
                    "aws_region": os.environ.get("AWS_REGION", "local"),
                    "boto3_available": HAS_BOTO3,
                    "app_env": os.environ.get("APP_ENV", "dev"),
                },
            })

        # -------------------------------------------------------------
        # Route: Academic Department Categories
        # -------------------------------------------------------------
        if raw_path in ["/departments", "/api/departments"]:
            depts = sorted(estimator.books_df["department"].dropna().unique().tolist())
            return make_response(200, {"departments": depts})

        # -------------------------------------------------------------
        # Route: Public Upcoming Exam
        # -------------------------------------------------------------
        if raw_path in ["/calendar/upcoming", "/api/calendar/upcoming"]:
            exam_info = estimator.calendar.get_next_applicable_exam()
            return make_response(200, exam_info)

        # -------------------------------------------------------------
        # Route: Public Calendar Scheduled List
        # -------------------------------------------------------------
        if raw_path in ["/calendar", "/api/calendar"]:
            entries = estimator.calendar.list_entries(status="scheduled")
            return make_response(200, {
                "total_scheduled": len(entries),
                "entries": entries,
                "timezone": estimator.calendar.tz_name,
                "current_date": estimator.calendar.get_current_date().strftime("%Y-%m-%d"),
            })

        # -------------------------------------------------------------
        # Route: Admin Circulation Statistics
        # -------------------------------------------------------------
        if raw_path in ["/admin/stats", "/api/admin/stats"]:
            if not is_admin:
                return make_response(401, {
                    "error": "Unauthorized. Valid administrator key required.",
                    "hint": "Provide header 'X-Admin-Key: <key>' or 'Authorization: Bearer <key>'",
                })
            all_books = list(estimator.inventory_mgr._inventory.values())
            total_titles = len(all_books)
            total_copies = sum(b.get("total_copies", 0) for b in all_books)
            available_copies = sum(b.get("available_copies", 0) for b in all_books)
            on_loan = total_copies - available_copies
            critical_titles = [b for b in all_books if b.get("available_copies", 0) == 0]
            low_stock_titles = [b for b in all_books if 0 < b.get("available_copies", 0) <= 2]

            return make_response(200, {
                "total_titles": total_titles,
                "total_copies": total_copies,
                "available_copies": available_copies,
                "on_loan_copies": on_loan,
                "out_of_stock_count": len(critical_titles),
                "low_stock_count": len(low_stock_titles),
                "total_transactions": len(estimator.inventory_mgr._transactions),
                "recent_transactions": estimator.inventory_mgr._transactions[-15:][::-1],
                "critical_sample": critical_titles[:10],
                "low_stock_sample": low_stock_titles[:10],
            })

        # -------------------------------------------------------------
        # Route: Transaction Audit Log
        # -------------------------------------------------------------
        if raw_path in ["/admin/transactions", "/api/admin/transactions", "/transactions", "/api/transactions"]:
            tx_limit = int(query_params.get("limit", 50))
            txs = estimator.inventory_mgr._transactions[-tx_limit:][::-1]
            return make_response(200, {
                "total_transactions": len(estimator.inventory_mgr._transactions),
                "returned_count": len(txs),
                "transactions": txs,
            })

        # -------------------------------------------------------------
        # Route: Admin Calendar Management
        # -------------------------------------------------------------
        if (
            raw_path.startswith("/admin/calendar")
            or raw_path.startswith("/api/admin/calendar")
            or raw_path in ["/calendar/add", "/api/calendar/add", "/calendar/update", "/api/calendar/update"]
        ):
            if not is_admin:
                return make_response(401, {
                    "error": "Unauthorized. Valid administrator key required.",
                    "hint": "Provide header 'X-Admin-Key: <key>' or 'Authorization: Bearer <key>'",
                })

            # GET all entries
            if http_method == "GET":
                entries = estimator.calendar.list_entries(status=None)
                return make_response(200, {
                    "total_entries": len(entries),
                    "entries": entries,
                    "timezone": estimator.calendar.tz_name,
                    "current_date": estimator.calendar.get_current_date().strftime("%Y-%m-%d"),
                })

            # POST create entry: /admin/calendar or /calendar/add
            if http_method == "POST" and (
                raw_path in ["/admin/calendar", "/api/admin/calendar", "/calendar/add", "/api/calendar/add"]
            ):
                body_raw = event.get("body")
                body = json.loads(body_raw) if body_raw else {}
                try:
                    created = estimator.calendar.create_entry(body)
                    return make_response(201, {
                        "message": "Academic calendar entry created successfully.",
                        "entry": created,
                    })
                except ValueError as v_err:
                    return make_response(400, {"error": str(v_err)})

            # PUT or POST update entry: /admin/calendar/{id} or /calendar/update
            if (http_method == "PUT" and (raw_path.startswith("/admin/calendar/") or raw_path.startswith("/api/admin/calendar/"))) or (
                raw_path in ["/calendar/update", "/api/calendar/update"]
            ):
                body_raw = event.get("body")
                body = json.loads(body_raw) if body_raw else {}
                cid = body.get("calendar_id") or raw_path.rstrip("/").split("/")[-1]
                try:
                    updated = estimator.calendar.update_entry(cid, body)
                    return make_response(200, {
                        "message": f"Calendar entry '{cid}' updated successfully.",
                        "entry": updated,
                    })
                except KeyError as k_err:
                    return make_response(404, {"error": str(k_err)})
                except ValueError as v_err:
                    return make_response(400, {"error": str(v_err)})

            # DELETE deactivate entry: /admin/calendar/{id}
            if http_method == "DELETE":
                cid = raw_path.rstrip("/").split("/")[-1]
                try:
                    deactivated = estimator.calendar.deactivate_entry(cid, reason="Admin deactivated via Lambda API")
                    return make_response(200, {
                        "message": f"Calendar entry '{cid}' deactivated successfully.",
                        "entry": deactivated,
                    })
                except KeyError as k_err:
                    return make_response(404, {"error": str(k_err)})

        # -------------------------------------------------------------
        # Route: Student Loans & Active Loans
        # -------------------------------------------------------------
        if raw_path in ["/loans", "/api/loans", "/api/student/loans"]:
            student_id = query_params.get("student_id")
            status_filter = query_params.get("status", "active").lower()
            if student_id:
                if status_filter == "all":
                    loans = estimator.inventory_mgr.get_student_loan_history(student_id)
                else:
                    loans = estimator.inventory_mgr.get_active_loans(student_id)
            else:
                loans = estimator.inventory_mgr.get_active_loans()

            return make_response(200, {
                "student_id": student_id,
                "status_filter": status_filter,
                "count": len(loans),
                "loans": loans,
            })

        # -------------------------------------------------------------
        # Route: Borrow Book / Live Transaction Checkout
        # -------------------------------------------------------------
        if raw_path in ["/loans/borrow", "/api/loans/borrow", "/transactions/checkout", "/api/transactions/checkout"]:
            if http_method != "POST":
                return make_response(405, {"error": "Method Not Allowed"})

            body_raw = event.get("body")
            body = json.loads(body_raw) if body_raw else {}
            book_id = body.get("book_id")
            if not book_id:
                return make_response(400, {"error": "Missing required 'book_id' parameter."})
            try:
                tx_record = estimator.inventory_mgr.record_checkout(
                    book_id=str(book_id).upper(),
                    idempotency_key=body.get("idempotency_key"),
                    student_id=body.get("student_id"),
                    notes=body.get("notes", ""),
                )
                return make_response(201, {
                    "message": "Checkout recorded successfully.",
                    "data": tx_record,
                })
            except InventoryExhaustedError as ie_err:
                return make_response(409, {
                    "error": str(ie_err),
                    "status": "EXHAUSTED",
                })
            except KeyError as k_err:
                return make_response(404, {"error": str(k_err)})
            except Exception as exc:
                return make_response(500, {"error": str(exc)})

        # -------------------------------------------------------------
        # Route: Return Book / Live Transaction Return
        # -------------------------------------------------------------
        if raw_path in ["/loans/return", "/api/loans/return", "/transactions/return", "/api/transactions/return"]:
            if http_method != "POST":
                return make_response(405, {"error": "Method Not Allowed"})

            body_raw = event.get("body")
            body = json.loads(body_raw) if body_raw else {}
            book_id = body.get("book_id")
            loan_id = body.get("loan_id")
            if not book_id and not loan_id:
                return make_response(400, {"error": "Missing required 'book_id' or 'loan_id' parameter."})
            try:
                tx_record = estimator.inventory_mgr.record_return(
                    book_id=str(book_id).upper() if book_id else None,
                    loan_id=loan_id,
                    idempotency_key=body.get("idempotency_key"),
                    student_id=body.get("student_id"),
                    notes=body.get("notes", ""),
                )
                return make_response(200, {
                    "message": "Return recorded successfully.",
                    "data": tx_record,
                })
            except PermissionError as p_err:
                return make_response(403, {
                    "error": str(p_err),
                    "status": "FORBIDDEN",
                })
            except InvalidReturnError as ir_err:
                return make_response(400, {
                    "error": str(ir_err),
                    "status": "INVALID_RETURN",
                })
            except KeyError as k_err:
                return make_response(404, {"error": str(k_err)})
            except ValueError as v_err:
                return make_response(400, {"error": str(v_err)})
            except Exception as exc:
                return make_response(500, {"error": str(exc)})

        # -------------------------------------------------------------
        # Route: Search Catalog & Pagination
        # -------------------------------------------------------------
        if raw_path in ["/books", "/api/books", "/search", "/api/search"]:
            q = (query_params.get("q") or "").strip().lower()
            dept = (query_params.get("department") or "").strip()
            limit = min(100, max(1, int(query_params.get("limit", 20))))
            offset = max(0, int(query_params.get("offset", 0)))

            df = estimator.books_df
            if dept:
                df = df[df["department"].str.lower() == dept.lower()]
            if q:
                df = df[
                    df["title"].str.lower().str.contains(q, na=False)
                    | df["author"].str.lower().str.contains(q, na=False)
                    | df["book_id"].str.lower().str.contains(q, na=False)
                ]

            total_matches = len(df)
            sliced = df.iloc[offset : offset + limit]

            results = []
            for _, row in sliced.iterrows():
                bid = str(row["book_id"])
                live_info = estimator.inventory_mgr.get_book(bid)
                avail = int(live_info["available_copies"]) if live_info else int(row["available_copies"])
                tot = int(live_info["total_copies"]) if live_info else int(row["total_copies"])
                checked_out = tot - avail

                results.append({
                    "book_id": bid,
                    "title": str(row["title"]),
                    "author": str(row["author"]),
                    "department": str(row["department"]),
                    "edition": str(row.get("edition", "1st Edition")),
                    "total_copies": tot,
                    "available_copies": avail,
                    "checked_out_copies": checked_out,
                })

            return make_response(200, {
                "total_matches": total_matches,
                "offset": offset,
                "limit": limit,
                "returned_count": len(results),
                "books": results,
            })

        # -------------------------------------------------------------
        # Route: Single Book Detail
        # -------------------------------------------------------------
        if raw_path.startswith("/books/") or raw_path.startswith("/api/books/"):
            book_id = raw_path.rstrip("/").split("/")[-1].upper()
            info = estimator.get_book_info(book_id)
            if not info:
                return make_response(404, {"error": f"Book '{book_id}' not found."})
            return make_response(200, {
                "book_id": book_id,
                "title": str(info["title"]),
                "author": str(info["author"]),
                "department": str(info["department"]),
                "total_copies": int(info["total_copies"]),
                "available_copies": int(info["available_copies"]),
                "borrowing_policy": "First Come, First Served (FCFS)",
            })

        # -------------------------------------------------------------
        # Route: Predict Availability (POST or GET)
        # -------------------------------------------------------------
        if (
            raw_path in ["/predict", "/api/predict"]
            or raw_path.startswith("/predict/")
            or raw_path.startswith("/api/predict/")
        ):
            book_id = None
            avail_copies = None
            checkouts_7d = 0.0

            if "/predict/" in raw_path:
                book_id = raw_path.rstrip("/").split("/")[-1].upper()

            if http_method == "POST":
                body_raw = event.get("body")
                body = json.loads(body_raw) if body_raw else {}
                book_id = body.get("book_id") or book_id
                avail_copies = body.get("available_copies")
                checkouts_7d = float(body.get("checkouts_last_7d", checkouts_7d))
            else:
                book_id = query_params.get("book_id", book_id)
                if "available_copies" in query_params:
                    avail_copies = int(query_params["available_copies"])
                checkouts_7d = float(query_params.get("checkouts_last_7d", checkouts_7d))

            if not book_id:
                return make_response(400, {
                    "error": "Missing required parameter 'book_id'.",
                    "usage": "POST /predict with {'book_id': 'B08510'} or GET /predict/B08510"
                })

            assessment = estimator.estimate_stockout(
                book_id=str(book_id).upper(),
                available_copies=avail_copies,
                checkouts_7d=checkouts_7d,
            )

            if "error" in assessment:
                return make_response(404, assessment)

            return make_response(200, assessment)

        # -------------------------------------------------------------
        # Route: Live Operational Inventory & Movement Details
        # -------------------------------------------------------------
        if raw_path.startswith("/inventory/") or raw_path.startswith("/api/inventory/"):
            book_id = raw_path.rstrip("/").split("/")[-1].upper()
            inv = estimator.inventory_mgr.get_inventory(book_id)
            if not inv:
                return make_response(404, {"error": f"Book '{book_id}' not found in live inventory."})
            mv = estimator.inventory_mgr.get_live_movement_aggregates(book_id)
            return make_response(200, {
                "book_id": book_id,
                "inventory": inv,
                "recent_movement": mv,
            })

        # -------------------------------------------------------------
        # Default Fallback: 404
        # -------------------------------------------------------------
        return make_response(404, {
            "error": "Not Found",
            "path": raw_path,
            "method": http_method,
            "available_endpoints": [
                "GET    /health",
                "GET    /departments",
                "GET    /calendar/upcoming",
                "GET    /calendar",
                "GET    /books?q={term}&department={dept}&limit={limit}&offset={offset}",
                "GET    /books/{book_id}",
                "GET    /inventory/{book_id}",
                "POST   /predict",
                "GET    /predict/{book_id}",
                "GET    /loans?student_id={id}&status={active|all}",
                "POST   /loans/borrow",
                "POST   /loans/return",
                "GET    /admin/stats (Admin)",
                "GET    /admin/transactions (Admin)",
                "GET    /admin/calendar (Admin)",
                "POST   /admin/calendar (Admin)",
                "PUT    /admin/calendar/{id} (Admin)",
                "DELETE /admin/calendar/{id} (Admin)",
            ],
        })

    except Exception as exc:
        return make_response(500, {
            "error": "Internal Server Error",
            "message": str(exc),
        })
