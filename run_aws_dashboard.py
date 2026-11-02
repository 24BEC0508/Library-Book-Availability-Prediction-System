"""
Local Dashboard & AWS Lambda Proxy Server.

Serves the local frontend dashboard (app/static/) WITHOUT modifying any files in app/,
and proxies all /api/* requests directly to the live AWS Lambda HTTP API Gateway:
https://oph7h7s5ej.execute-api.ap-south-1.amazonaws.com
"""

import sys
import os
import json
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "app" / "static"
AWS_API_GATEWAY_URL = os.environ.get(
    "AWS_API_GATEWAY_URL",
    "https://oph7h7s5ej.execute-api.ap-south-1.amazonaws.com"
).rstrip("/")

class AwsDashboardProxyHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def _proxy_to_aws(self, method: str):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = parsed.query

        # Map /api/xyz or /xyz to the AWS API Gateway endpoint
        aws_path = path
        target_url = f"{AWS_API_GATEWAY_URL}{aws_path}"
        if query:
            target_url += f"?{query}"

        # Read request body if present
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        # Prepare headers
        req_headers = {}
        for header in ["Content-Type", "X-Admin-Key", "Authorization"]:
            if header in self.headers:
                req_headers[header] = self.headers[header]
            elif header.lower() in self.headers:
                req_headers[header] = self.headers[header.lower()]

        print(f"[AWS PROXY] {method} {path} -> {target_url}")

        req = urllib.request.Request(
            target_url,
            data=body,
            headers=req_headers,
            method=method
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_data = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(resp_data)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Key, Authorization")
                self.end_headers()
                self.wfile.write(resp_data)
        except urllib.error.HTTPError as http_err:
            err_data = http_err.read()
            self.send_response(http_err.code)
            self.send_header("Content-Type", http_err.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(err_data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(err_data)
        except Exception as exc:
            err_payload = json.dumps({"error": f"Proxy error contacting AWS Lambda: {exc}"}).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(err_payload)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Key, Authorization")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Route /admin to admin.html
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

        # Proxy API requests to AWS Lambda
        if path.startswith("/api/") or path in ["/health", "/departments", "/calendar/upcoming", "/calendar", "/loans", "/search"]:
            self._proxy_to_aws("GET")
            return

        # Serve static frontend files
        super().do_GET()

    def do_POST(self):
        self._proxy_to_aws("POST")

    def do_PUT(self):
        self._proxy_to_aws("PUT")

    def do_DELETE(self):
        self._proxy_to_aws("DELETE")


def verify_aws_connection():
    """Verify live connectivity to AWS Lambda."""
    health_url = f"{AWS_API_GATEWAY_URL}/health"
    print(f"\n[1/2] Testing AWS Lambda Health: {health_url}")
    try:
        req = urllib.request.Request(health_url, headers={"User-Agent": "LibraryProxy/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f" [OK] AWS Lambda is {data.get('status', 'UNKNOWN')} in region {data.get('environment', {}).get('aws_region', 'N/A')}")
            print(f" [OK] Next Academic Exam: {data.get('academic_calendar', {}).get('next_exam', 'N/A')}")
            print(f" [OK] Catalog Size: {data.get('total_catalog_books', 0):,} books")
            return True
    except Exception as exc:
        print(f" [ERROR] Could not connect to AWS Lambda: {exc}")
        return False


def run_server(port: int = 8000):
    if not verify_aws_connection():
        print("\n[WARNING] Proceeding, but AWS Lambda endpoint did not respond to health check.")

    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, AwsDashboardProxyHandler)
    print("\n" + "=" * 65)
    print("APEX UNIVERSITY LIBRARY - AWS SERVERLESS DASHBOARD BRIDGE")
    print("=" * 65)
    print(f"Local Student Portal: http://127.0.0.1:{port}/")
    print(f"Local Admin Portal:   http://127.0.0.1:{port}/admin")
    print(f"Live AWS Backend:     {AWS_API_GATEWAY_URL}")
    print(f"API Mode:             All /api/* calls proxied directly to AWS Lambda & DynamoDB")
    print("=" * 65)
    print("Press Ctrl+C to stop the server.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        httpd.server_close()


if __name__ == "__main__":
    if "--test" in sys.argv:
        success = verify_aws_connection()
        sys.exit(0 if success else 1)

    port = int(os.environ.get("PORT", 8000))
    run_server(port=port)
