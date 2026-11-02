"""
Library Book Availability Prediction HTTP API & Web Server.

Standard library implementation (no Flask/FastAPI required) supporting:
- GET    /api/health
- GET    /api/books?q=...&department=...&limit=...
- GET    /api/books/<book_id>
- GET    /api/calendar/upcoming
- GET    /api/calendar
- GET    /api/predict/<book_id>
- POST   /api/predict
- GET    /api/admin/calendar           (Admin protected: X-Admin-Key)
- POST   /api/admin/calendar          (Admin protected: X-Admin-Key)
- PUT    /api/admin/calendar/<id>     (Admin protected: X-Admin-Key)
- DELETE /api/admin/calendar/<id>     (Admin protected: X-Admin-Key)
- Static frontend hosting at /
"""

import json
import os
import sys
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
try:
    from http.server import ThreadingHTTPServer
except ImportError:
    ThreadingHTTPServer = HTTPServer
from pathlib import Path
from typing import Dict, Any, Optional

# Ensure project root in python path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.stockout_estimator import StockoutEstimator
from ml.live_inventory_manager import InventoryExhaustedError, InvalidReturnError

STATIC_DIR = Path(__file__).resolve().parent / "static"
estimator = StockoutEstimator()


class LibraryApiHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def _send_json(self, status_code: int, data: Any):
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Key, Authorization")
        self.end_headers()
        self.wfile.write(payload)

    def _check_admin_auth(self) -> bool:
        """Check administrator authentication via header."""
        admin_key = self.headers.get("X-Admin-Key") or self.headers.get("Authorization")
        return estimator.calendar.verify_admin_key(admin_key)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Key, Authorization")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 0. Admin Portal page handler
        if path in ["/admin", "/admin/"]:
            admin_file = STATIC_DIR / "admin.html"
            if admin_file.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                content = admin_file.read_bytes()
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

        # 1. Health endpoint
        if path in ["/api/health", "/health"]:
            next_exam = estimator.calendar.get_next_applicable_exam()
            cloud_provider = os.environ.get("CLOUD_PROVIDER", "DigitalOcean")
            region = os.environ.get("DO_REGION", os.environ.get("REGION", "blr1"))
            self._send_json(200, {
                "status": "HEALTHY",
                "service": "Library Book Availability Prediction API",
                "cloud_provider": cloud_provider,
                "environment": {
                    "provider": cloud_provider,
                    "region": region,
                    "runtime": "DigitalOcean Production",
                },
                "model_cutoff": estimator.train_cutoff_date,
                "historical_mean": round(estimator.historical_mean, 6),
                "total_catalog_books": len(estimator.books_df),
                "borrowing_policy": "First Come, First Served (FCFS)",
                "academic_calendar": {
                    "next_exam": next_exam["label"],
                    "status": next_exam["status"],
                    "timezone": next_exam["timezone"],
                },
            })
            return

        # 2. Public Upcoming Exam endpoint
        if path in ["/api/calendar/upcoming", "/calendar/upcoming"]:
            exam_info = estimator.calendar.get_next_applicable_exam()
            self._send_json(200, exam_info)
            return

        # 3. Public Calendar List endpoint
        if path in ["/api/calendar", "/calendar"]:
            entries = estimator.calendar.list_entries(status="scheduled")
            self._send_json(200, {
                "total_scheduled": len(entries),
                "entries": entries,
                "timezone": estimator.calendar.tz_name,
                "current_date": estimator.calendar.get_current_date().strftime("%Y-%m-%d"),
            })
            return

        # 3b. Department categories endpoint
        if path in ["/api/departments", "/departments"]:
            depts = sorted(estimator.books_df["department"].dropna().unique().tolist())
            self._send_json(200, {"departments": depts})
            return

        # 4. Protected Admin: List all calendar entries (including completed/cancelled)
        if path in ["/api/admin/calendar", "/admin/calendar"]:
            if not self._check_admin_auth():
                self._send_json(401, {
                    "error": "Unauthorized. Valid administrator key required.",
                    "hint": "Provide header 'X-Admin-Key: <key>' or 'Authorization: Bearer <key>'",
                })
                return
            entries = estimator.calendar.list_entries(status=None)
            self._send_json(200, {
                "total_entries": len(entries),
                "entries": entries,
                "timezone": estimator.calendar.tz_name,
                "current_date": estimator.calendar.get_current_date().strftime("%Y-%m-%d"),
            })
            return

        # 4b. Protected Admin: Institutional circulation statistics
        if path in ["/api/admin/stats", "/admin/stats"]:
            if not self._check_admin_auth():
                self._send_json(401, {
                    "error": "Unauthorized. Valid administrator key required.",
                    "hint": "Provide header 'X-Admin-Key: <key>' or 'Authorization: Bearer <key>'",
                })
                return

            all_books = list(estimator.inventory_mgr._inventory.values())
            total_titles = len(all_books)
            total_copies = sum(b.get("total_copies", 0) for b in all_books)
            available_copies = sum(b.get("available_copies", 0) for b in all_books)
            on_loan = total_copies - available_copies
            critical_titles = [b for b in all_books if b.get("available_copies", 0) == 0]
            low_stock_titles = [b for b in all_books if 0 < b.get("available_copies", 0) <= 2]

            self._send_json(200, {
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
            return

        # 4c. Protected Admin: Transaction audit log
        if path in ["/api/admin/transactions", "/admin/transactions", "/api/transactions"]:
            tx_limit = int(query.get("limit", [50])[0])
            txs = estimator.inventory_mgr._transactions[-tx_limit:][::-1]
            self._send_json(200, {
                "total_transactions": len(estimator.inventory_mgr._transactions),
                "returned_count": len(txs),
                "transactions": txs,
            })
            return

        # 4d. Student Loans endpoint: /api/loans?student_id=...&status=active
        if path in ["/api/loans", "/loans", "/api/student/loans"]:
            student_id = query.get("student_id", [None])[0]
            status_filter = query.get("status", ["active"])[0].lower()
            if student_id:
                if status_filter == "all":
                    loans = estimator.inventory_mgr.get_student_loan_history(student_id)
                else:
                    loans = estimator.inventory_mgr.get_active_loans(student_id)
            else:
                loans = estimator.inventory_mgr.get_active_loans()

            self._send_json(200, {
                "student_id": student_id,
                "status_filter": status_filter,
                "count": len(loans),
                "loans": loans,
            })
            return

        # 5. Catalog search endpoint with pagination
        if path in ["/api/books", "/books"]:
            q = query.get("q", [""])[0].strip().lower()
            dept = query.get("department", [""])[0].strip()
            limit = max(1, min(100, int(query.get("limit", [20])[0])))
            offset = max(0, int(query.get("offset", [0])[0]))

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
            self._send_json(200, {
                "total_matches": total_matches,
                "offset": offset,
                "limit": limit,
                "returned_count": len(results),
                "books": results,
            })
            return

        # 6. Single book detail: /api/books/<book_id>
        if path.startswith("/api/books/"):
            book_id = path.rstrip("/").split("/")[-1].upper()
            info = estimator.get_book_info(book_id)
            if not info:
                self._send_json(404, {"error": f"Book '{book_id}' not found in catalog."})
                return
            self._send_json(200, {
                "book_id": book_id,
                "title": str(info["title"]),
                "author": str(info["author"]),
                "department": str(info["department"]),
                "total_copies": int(info["total_copies"]),
                "available_copies": int(info["available_copies"]),
                "borrowing_policy": "First Come, First Served (FCFS)",
            })
            return

        # 6b. Live operational inventory & movement aggregates: /api/inventory/<book_id>
        if path.startswith("/api/inventory/"):
            book_id = path.rstrip("/").split("/")[-1].upper()
            inv = estimator.inventory_mgr.get_inventory(book_id)
            if not inv:
                self._send_json(404, {"error": f"Book '{book_id}' not found in live inventory."})
                return
            mv = estimator.inventory_mgr.get_live_movement_aggregates(book_id)
            self._send_json(200, {
                "book_id": book_id,
                "inventory": inv,
                "recent_movement": mv,
            })
            return

        # 7. Availability prediction: /api/predict/<book_id> (Countdown automatically determined)
        if path.startswith("/api/predict/"):
            book_id = path.rstrip("/").split("/")[-1].upper()
            avail_str = query.get("available_copies", [None])[0]
            avail = int(avail_str) if avail_str is not None else None
            c7d = float(query.get("checkouts_last_7d", [0.0])[0])

            result = estimator.estimate_stockout(
                book_id=book_id,
                available_copies=avail,
                checkouts_7d=c7d,
            )
            if "error" in result:
                self._send_json(404, result)
            else:
                self._send_json(200, result)
            return

        # Fallback to static files
        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_len = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_len).decode("utf-8")
        try:
            body = json.loads(post_body) if post_body else {}
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON payload."})
            return

        # Protected Admin: Create new calendar entry
        if path in ["/api/admin/calendar", "/admin/calendar"]:
            if not self._check_admin_auth():
                self._send_json(401, {"error": "Unauthorized. Valid administrator key required."})
                return
            try:
                created = estimator.calendar.create_entry(body)
                self._send_json(201, {
                    "message": "Academic calendar entry created successfully.",
                    "entry": created,
                })
            except ValueError as val_err:
                self._send_json(400, {"error": str(val_err)})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        # Student Prediction Endpoint
        if path in ["/api/predict", "/predict"]:
            book_id = body.get("book_id")
            if not book_id:
                self._send_json(400, {"error": "Missing required 'book_id' parameter."})
                return

            avail = body.get("available_copies")
            c7d = float(body.get("checkouts_last_7d", 0.0))

            result = estimator.estimate_stockout(
                book_id=str(book_id).upper(),
                available_copies=avail,
                checkouts_7d=c7d,
            )
            if "error" in result:
                self._send_json(404, result)
            else:
                self._send_json(200, result)
            return

        # Live Transaction: Record Checkout / Borrow Book
        if path in ["/api/transactions/checkout", "/transactions/checkout", "/api/loans/borrow", "/loans/borrow"]:
            book_id = body.get("book_id")
            if not book_id:
                self._send_json(400, {"error": "Missing required 'book_id' parameter."})
                return
            idempotency_key = body.get("idempotency_key")
            student_id = body.get("student_id")
            notes = body.get("notes", "")
            try:
                tx_record = estimator.inventory_mgr.record_checkout(
                    book_id=str(book_id).upper(),
                    idempotency_key=idempotency_key,
                    student_id=student_id,
                    notes=notes,
                )
                self._send_json(201, {
                    "message": "Checkout recorded successfully.",
                    "data": tx_record,
                })
            except InventoryExhaustedError as ie_err:
                self._send_json(409, {
                    "error": str(ie_err),
                    "status": "EXHAUSTED",
                })
            except KeyError as k_err:
                self._send_json(404, {"error": str(k_err)})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        # Live Transaction: Record Return / Return Book
        if path in ["/api/transactions/return", "/transactions/return", "/api/loans/return", "/loans/return"]:
            book_id = body.get("book_id")
            loan_id = body.get("loan_id")
            if not book_id and not loan_id:
                self._send_json(400, {"error": "Missing required 'book_id' or 'loan_id' parameter."})
                return
            idempotency_key = body.get("idempotency_key")
            student_id = body.get("student_id")
            notes = body.get("notes", "")
            try:
                tx_record = estimator.inventory_mgr.record_return(
                    book_id=str(book_id).upper() if book_id else None,
                    loan_id=loan_id,
                    idempotency_key=idempotency_key,
                    student_id=student_id,
                    notes=notes,
                )
                self._send_json(200, {
                    "message": "Return recorded successfully.",
                    "data": tx_record,
                })
            except PermissionError as p_err:
                self._send_json(403, {
                    "error": str(p_err),
                    "status": "FORBIDDEN",
                })
            except InvalidReturnError as ir_err:
                self._send_json(400, {
                    "error": str(ir_err),
                    "status": "INVALID_RETURN",
                })
            except KeyError as k_err:
                self._send_json(404, {"error": str(k_err)})
            except ValueError as v_err:
                self._send_json(400, {"error": str(v_err)})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        self._send_json(404, {"error": "Endpoint not found."})

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Protected Admin: Update/correct existing calendar entry
        if path.startswith("/api/admin/calendar/") or path.startswith("/admin/calendar/"):
            if not self._check_admin_auth():
                self._send_json(401, {"error": "Unauthorized. Valid administrator key required."})
                return

            calendar_id = path.rstrip("/").split("/")[-1]
            content_len = int(self.headers.get("Content-Length", 0))
            body_raw = self.rfile.read(content_len).decode("utf-8")
            try:
                body = json.loads(body_raw) if body_raw else {}
            except json.JSONDecodeError:
                self._send_json(400, {"error": "Invalid JSON payload."})
                return

            try:
                updated = estimator.calendar.update_entry(calendar_id, body)
                self._send_json(200, {
                    "message": f"Calendar entry '{calendar_id}' updated successfully.",
                    "entry": updated,
                })
            except KeyError as k_err:
                self._send_json(404, {"error": str(k_err)})
            except ValueError as v_err:
                self._send_json(400, {"error": str(v_err)})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        self._send_json(404, {"error": "Endpoint not found."})

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Protected Admin: Deactivate/cancel existing calendar entry
        if path.startswith("/api/admin/calendar/") or path.startswith("/admin/calendar/"):
            if not self._check_admin_auth():
                self._send_json(401, {"error": "Unauthorized. Valid administrator key required."})
                return

            calendar_id = path.rstrip("/").split("/")[-1]
            try:
                deactivated = estimator.calendar.deactivate_entry(calendar_id, reason="Admin deactivated via API")
                self._send_json(200, {
                    "message": f"Calendar entry '{calendar_id}' deactivated successfully.",
                    "entry": deactivated,
                })
            except KeyError as k_err:
                self._send_json(404, {"error": str(k_err)})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        self._send_json(404, {"error": "Endpoint not found."})


def run_server(host: Optional[str] = None, port: int = 8000):
    if host is None:
        host = os.environ.get("HOST", "0.0.0.0")
    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, LibraryApiHandler)
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    print("=" * 60)
    print(f"LIBRARY PREDICTION API & WEB APP RUNNING (DigitalOcean / Cloud Ready)")
    print(f"Listen Address:    {host}:{port}")
    print(f"URL:               http://{display_host}:{port}/")
    print(f"API Health:        http://{display_host}:{port}/api/health")
    print(f"Academic Calendar: http://{display_host}:{port}/api/calendar/upcoming")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        httpd.server_close()


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8000))
    run_server(host=host, port=port)
