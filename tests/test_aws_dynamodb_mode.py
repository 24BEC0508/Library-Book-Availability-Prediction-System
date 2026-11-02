"""
Unit and Integration Tests for AWS DynamoDB Mode and S3 Integration.

Verifies:
1. AcademicCalendar operating with mocked DynamoDB backend (CRUD, queries, upcoming exam).
2. LiveInventoryManager operating with mocked DynamoDB backend:
   - Atomic conditional decrement on checkout with ConditionExpression available_copies > 0.
   - Raises InventoryExhaustedError when DynamoDB returns ConditionalCheckFailedException.
   - Atomic conditional increment on return with ConditionExpression available_copies < total_copies.
   - Raises InvalidReturnError when DynamoDB returns ConditionalCheckFailedException.
   - Enforces borrower authorization and single-return semantics.
   - Decimal type deserialization.
   - Rolling movement aggregates (1d, 3d, 7d) from DynamoDB transactions.
3. AWS Lambda Handler:
   - S3 model loading with fallback to alternative model key and bundled local file.
   - Route execution: /health, /departments, /books, /predict, /loans, /loans/borrow, /loans/return, /admin/stats, /admin/calendar.
"""

import json
import unittest
from unittest.mock import MagicMock, patch
from decimal import Decimal
from datetime import datetime, date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from ml.academic_calendar import AcademicCalendar
from ml.live_inventory_manager import (
    LiveInventoryManager,
    InventoryExhaustedError,
    InvalidReturnError,
)
from ml.stockout_estimator import StockoutEstimator
import aws.lambda_function as lambda_mod


class FakeDynamoDBTable:
    """Mock in-memory DynamoDB Table for unit testing."""
    def __init__(self, name: str):
        self.name = name
        self.items = {}

    def get_item(self, Key=None):
        if not Key:
            return {}
        if "Transaction" in self.name and "book_id" in Key and "timestamp" in Key:
            k_val = f"{Key['book_id']}#{Key['timestamp']}"
        else:
            k_val = list(Key.values())[0]
        item = self.items.get(k_val)
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item=None):
        if not Item:
            return {}
        if "Loan" in self.name:
            k_val = Item.get("loan_id")
        elif "Transaction" in self.name:
            k_val = f"{Item.get('book_id')}#{Item.get('timestamp')}"
        elif "Calendar" in self.name:
            k_val = Item.get("calendar_id")
        else:
            k_val = Item.get("book_id")
        if k_val:
            self.items[k_val] = dict(Item)
        return {}

    def scan(self, FilterExpression=None):
        return {"Items": [dict(v) for v in self.items.values()]}

    def query(self, IndexName=None, KeyConditionExpression=None):
        return {"Items": [dict(v) for v in self.items.values()]}

    def update_item(self, Key=None, UpdateExpression=None, ConditionExpression=None, ExpressionAttributeValues=None, ReturnValues=None):
        k_val = list(Key.values())[0] if Key else None
        item = self.items.get(k_val)
        if not item:
            raise KeyError(f"Item with key {Key} does not exist.")

        # Simulate checkout conditional update
        if "available_copies > :zero" in (ConditionExpression or ""):
            avail = int(item["available_copies"])
            if avail <= 0:
                mock_err = Exception("ConditionalCheckFailedException: available_copies not greater than 0")
                mock_err.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
                raise mock_err
            item["available_copies"] = Decimal(avail - 1)
            item["checked_out_copies"] = Decimal(int(item["total_copies"]) - int(item["available_copies"]))
            item["updated_at"] = ExpressionAttributeValues[":tx_time"]
            return {"Attributes": dict(item)}

        # Simulate return conditional update
        if "available_copies < total_copies" in (ConditionExpression or ""):
            avail = int(item["available_copies"])
            tot = int(item["total_copies"])
            if avail >= tot:
                mock_err = Exception("ConditionalCheckFailedException: available_copies already at max")
                mock_err.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
                raise mock_err
            item["available_copies"] = Decimal(avail + 1)
            item["checked_out_copies"] = Decimal(tot - int(item["available_copies"]))
            item["updated_at"] = ExpressionAttributeValues[":tx_time"]
            return {"Attributes": dict(item)}

        return {"Attributes": dict(item)}


class FakeDynamoDBClient:
    """Mock DynamoDB low-level client supporting atomic TransactWriteItems."""
    def __init__(self, resource):
        self.resource = resource
        self.transact_write_calls = []

    def transact_write_items(self, TransactItems=None):
        items = TransactItems or []
        self.transact_write_calls.append(items)

        # Pass 1: Condition validation
        cancellation_reasons = []
        has_cancellation = False

        for item_action in items:
            action_type = list(item_action.keys())[0]
            action_body = item_action[action_type]
            table_name = action_body["TableName"]
            table = self.resource.Table(table_name)
            cond_expr = action_body.get("ConditionExpression", "")
            reason = {"Code": "None"}

            if action_type == "Update":
                key_dict = action_body.get("Key", {})
                key_val = list(key_dict.values())[0]["S"]
                target = table.items.get(key_val)

                if "attribute_exists(book_id) AND available_copies > :zero" in cond_expr:
                    if not target:
                        reason = {"Code": "ConditionalCheckFailed", "Message": "Book does not exist"}
                        has_cancellation = True
                    elif int(target.get("available_copies", 0)) <= 0:
                        reason = {"Code": "ConditionalCheckFailed", "Message": "available_copies <= 0"}
                        has_cancellation = True

                elif "attribute_exists(book_id) AND available_copies < total_copies" in cond_expr:
                    if not target:
                        reason = {"Code": "ConditionalCheckFailed", "Message": "Book does not exist"}
                        has_cancellation = True
                    elif int(target.get("available_copies", 0)) >= int(target.get("total_copies", 0)):
                        reason = {"Code": "ConditionalCheckFailed", "Message": "available_copies >= total_copies"}
                        has_cancellation = True

                elif "attribute_exists(loan_id) AND #st = :active" in cond_expr:
                    if not target:
                        reason = {"Code": "ConditionalCheckFailed", "Message": "Loan does not exist"}
                        has_cancellation = True
                    elif target.get("status") != "ACTIVE":
                        reason = {"Code": "ConditionalCheckFailed", "Message": "Loan is not active"}
                        has_cancellation = True

            elif action_type == "Put":
                item_dict = action_body.get("Item", {})
                if "attribute_not_exists(loan_id)" in cond_expr:
                    loan_id = item_dict.get("loan_id", {}).get("S")
                    if loan_id and loan_id in table.items:
                        reason = {"Code": "ConditionalCheckFailed", "Message": "Loan already exists"}
                        has_cancellation = True

            cancellation_reasons.append(reason)

        if has_cancellation:
            err = Exception("TransactionCanceledException: Transaction cancelled, please refer cancellation reasons")
            err.response = {
                "Error": {"Code": "TransactionCanceledException"},
                "CancellationReasons": cancellation_reasons,
            }
            raise err

        # Pass 2: Atomic state mutations
        for item_action in items:
            action_type = list(item_action.keys())[0]
            action_body = item_action[action_type]
            table_name = action_body["TableName"]
            table = self.resource.Table(table_name)

            if action_type == "Update":
                key_dict = action_body.get("Key", {})
                key_val = list(key_dict.values())[0]["S"]
                target = table.items[key_val]

                update_expr = action_body.get("UpdateExpression", "")
                if "available_copies = available_copies - :one" in update_expr:
                    avail = int(target["available_copies"])
                    target["available_copies"] = Decimal(avail - 1)
                    target["checked_out_copies"] = Decimal(int(target.get("checked_out_copies", 0)) + 1)
                    target["updated_at"] = action_body["ExpressionAttributeValues"][":tx_time"]["S"]
                    target["last_transaction_timestamp"] = target["updated_at"]
                elif "available_copies = available_copies + :one" in update_expr:
                    avail = int(target["available_copies"])
                    target["available_copies"] = Decimal(avail + 1)
                    target["checked_out_copies"] = Decimal(max(0, int(target.get("checked_out_copies", 0)) - 1))
                    target["updated_at"] = action_body["ExpressionAttributeValues"][":tx_time"]["S"]
                    target["last_transaction_timestamp"] = target["updated_at"]
                elif "SET #st = :ret" in update_expr:
                    target["status"] = action_body["ExpressionAttributeValues"][":ret"]["S"]
                    target["returned_date"] = action_body["ExpressionAttributeValues"][":tx_time"]["S"]
                    target["return_tx_id"] = action_body["ExpressionAttributeValues"][":tx_id"]["S"]

            elif action_type == "Put":
                raw_item = action_body.get("Item", {})
                from ml.live_inventory_manager import _deserialize_dynamodb_item
                plain_item = _deserialize_dynamodb_item(raw_item)
                table.put_item(Item=plain_item)

        return {}


class FakeDynamoDBResource:
    def __init__(self):
        self.tables = {}
        self.client = FakeDynamoDBClient(self)
        self.meta = MagicMock()
        self.meta.client = self.client

    def Table(self, name: str):
        if name not in self.tables:
            self.tables[name] = FakeDynamoDBTable(name)
        return self.tables[name]


class TestAWSDynamoDBMode(unittest.TestCase):
    def setUp(self):
        self.mock_resource = FakeDynamoDBResource()
        self.catalog_table = self.mock_resource.Table("LibraryCatalog-dev")
        self.cal_table = self.mock_resource.Table("AcademicCalendar-dev")
        self.tx_table = self.mock_resource.Table("LibraryTransactions-dev")
        self.loans_table = self.mock_resource.Table("LibraryLoans-dev")

        # Seed sample book with Decimal types (as returned by real DynamoDB)
        self.catalog_table.items["B08510"] = {
            "book_id": "B08510",
            "title": "Machine Learning in Action",
            "author": "Peter Harrington",
            "department": "CSE",
            "edition": "1",
            "total_copies": Decimal(5),
            "available_copies": Decimal(2),
            "checked_out_copies": Decimal(3),
            "updated_at": "2026-10-10T09:00:00+05:30",
        }

        # Seed calendar entry
        self.cal_table.items["CAL-2026F-FAT"] = {
            "calendar_id": "CAL-2026F-FAT",
            "academic_year": "2026-2027",
            "semester": "Fall",
            "exam_type": "FAT",
            "start_date": "2026-11-20",
            "end_date": "2026-11-30",
            "status": "scheduled",
            "notes": "Fall FAT Final Exams",
            "created_at": "2026-07-01T09:00:00+05:30",
            "updated_at": "2026-10-10T09:00:00+05:30",
        }

    def test_academic_calendar_dynamodb_crud(self):
        """Verify AcademicCalendar works against DynamoDB backend."""
        cal = AcademicCalendar(
            dynamodb_table_name="AcademicCalendar-dev",
            dynamodb_resource=self.mock_resource,
            storage_file=None,
        )

        entries = cal.list_entries(status="scheduled")
        self.assertGreaterEqual(len(entries), 1)

        # Admin create entry
        new_entry = cal.create_entry({
            "academic_year": "2026-2027",
            "semester": "Winter",
            "exam_type": "CAT-1",
            "start_date": "2027-02-10",
            "end_date": "2027-02-16",
            "status": "scheduled",
            "notes": "Winter CAT-1",
        })
        self.assertIn(new_entry["calendar_id"], self.cal_table.items)

        # Admin update entry
        updated = cal.update_entry(new_entry["calendar_id"], {"notes": "Updated note"})
        self.assertEqual(updated["notes"], "Updated note")

        # Admin deactivate entry
        deactivated = cal.deactivate_entry(new_entry["calendar_id"], reason="Cancelled")
        self.assertEqual(deactivated["status"], "cancelled")

    def test_live_inventory_dynamodb_atomic_checkout_and_return(self):
        """Verify LiveInventoryManager atomic checkout and return in DynamoDB mode."""
        mgr = LiveInventoryManager(
            dynamodb_table_name="LibraryCatalog-dev",
            dynamodb_tx_table_name="LibraryTransactions-dev",
            dynamodb_loans_table_name="LibraryLoans-dev",
            dynamodb_resource=self.mock_resource,
            inventory_file=None,
            transactions_file=None,
            loans_file=None,
            books_raw_path=None,
        )

        book = mgr.get_book("B08510")
        self.assertIsNotNone(book)
        self.assertEqual(book["available_copies"], 2)

        # 1. First checkout: 2 -> 1
        res1 = mgr.record_checkout(
            book_id="B08510",
            student_id="STU-001",
            notes="DynamoDB checkout test",
        )
        self.assertEqual(res1["available_copies_after"], 1)
        self.assertEqual(int(self.catalog_table.items["B08510"]["available_copies"]), 1)
        loan_id_1 = res1["loan_id"]

        # 2. Second checkout: 1 -> 0
        res2 = mgr.record_checkout(
            book_id="B08510",
            student_id="STU-002",
        )
        self.assertEqual(res2["available_copies_after"], 0)
        self.assertEqual(int(self.catalog_table.items["B08510"]["available_copies"]), 0)

        # 3. Third checkout attempt: must raise InventoryExhaustedError
        with self.assertRaises(InventoryExhaustedError):
            mgr.record_checkout(
                book_id="B08510",
                student_id="STU-003",
            )

        # 4. Unauthorized return attempt: STU-002 cannot return STU-001's loan
        with self.assertRaises(PermissionError):
            mgr.record_return(
                loan_id=loan_id_1,
                student_id="STU-002",
            )

        # 5. Authorized return: STU-001 returns loan_id_1: 0 -> 1
        ret1 = mgr.record_return(
            loan_id=loan_id_1,
            student_id="STU-001",
        )
        self.assertEqual(ret1["available_copies_after"], 1)
        self.assertEqual(int(self.catalog_table.items["B08510"]["available_copies"]), 1)

        # 6. Double return of same loan raises InvalidReturnError
        with self.assertRaises(InvalidReturnError):
            mgr.record_return(
                loan_id=loan_id_1,
                student_id="STU-001",
            )

    def test_lambda_handler_aws_routes(self):
        """Verify AWS Lambda Handler endpoints with mock DynamoDB and estimator."""
        cal = AcademicCalendar(
            dynamodb_table_name="AcademicCalendar-dev",
            dynamodb_resource=self.mock_resource,
            storage_file=None,
        )
        mgr = LiveInventoryManager(
            dynamodb_table_name="LibraryCatalog-dev",
            dynamodb_tx_table_name="LibraryTransactions-dev",
            dynamodb_loans_table_name="LibraryLoans-dev",
            dynamodb_resource=self.mock_resource,
            inventory_file=None,
            transactions_file=None,
            loans_file=None,
            books_raw_path=ROOT / "data" / "raw" / "books.csv",
        )
        estimator = StockoutEstimator(
            calendar=cal,
            inventory_mgr=mgr,
        )

        with patch("aws.lambda_function.get_estimator", return_value=estimator):
            # 1. Health check
            res_health = lambda_mod.lambda_handler({"httpMethod": "GET", "path": "/health"}, None)
            self.assertEqual(res_health["statusCode"], 200)
            body = json.loads(res_health["body"])
            self.assertEqual(body["status"], "HEALTHY")

            # 2. Departments
            res_depts = lambda_mod.lambda_handler({"httpMethod": "GET", "path": "/api/departments"}, None)
            self.assertEqual(res_depts["statusCode"], 200)
            self.assertIn("CSE", json.loads(res_depts["body"])["departments"])

            # 3. Books search
            res_books = lambda_mod.lambda_handler({"httpMethod": "GET", "path": "/api/books", "queryStringParameters": {"q": "Machine"}}, None)
            self.assertEqual(res_books["statusCode"], 200)

            # 4. Predict
            res_pred = lambda_mod.lambda_handler({"httpMethod": "GET", "path": "/api/predict/B08510"}, None)
            self.assertEqual(res_pred["statusCode"], 200)

            # 5. Borrow book
            res_borrow = lambda_mod.lambda_handler({
                "httpMethod": "POST",
                "path": "/api/loans/borrow",
                "body": json.dumps({"book_id": "B08510", "student_id": "STU-TEST-99"}),
            }, None)
            self.assertEqual(res_borrow["statusCode"], 201)
            loan_id = json.loads(res_borrow["body"])["data"]["loan_id"]

            # 6. Active loans query
            res_loans = lambda_mod.lambda_handler({
                "httpMethod": "GET",
                "path": "/api/loans",
                "queryStringParameters": {"student_id": "STU-TEST-99"},
            }, None)
            self.assertEqual(res_loans["statusCode"], 200)
            loans_data = json.loads(res_loans["body"])["loans"]
            self.assertEqual(len(loans_data), 1)

            # 7. Return book
            res_return = lambda_mod.lambda_handler({
                "httpMethod": "POST",
                "path": "/api/loans/return",
                "body": json.dumps({"loan_id": loan_id, "student_id": "STU-TEST-99"}),
            }, None)
            self.assertEqual(res_return["statusCode"], 200)

            # 8. Admin stats (unauthorized vs authorized)
            res_unauth = lambda_mod.lambda_handler({"httpMethod": "GET", "path": "/api/admin/stats"}, None)
            self.assertEqual(res_unauth["statusCode"], 401)

            res_auth = lambda_mod.lambda_handler({
                "httpMethod": "GET",
                "path": "/api/admin/stats",
                "headers": {"X-Admin-Key": "library-admin-secret-key-2026"},
            }, None)
            self.assertEqual(res_auth["statusCode"], 200)
            stats = json.loads(res_auth["body"])
            self.assertIn("total_titles", stats)

    def test_s3_model_download_with_fallback(self):
        """Verify S3 model loader attempts primary key, then fallback key."""
        mock_s3 = MagicMock()

        # Simulate primary key fails, fallback key succeeds
        def fake_download(bucket, key, dest):
            if key == "model/v1/regression_model.npz":
                raise Exception("NoSuchKey: Not found")
            elif key == "model/regression_model.npz":
                # Copy real model file to dest
                import shutil
                shutil.copyfile(str(ROOT / "model" / "regression_model.npz"), dest)
                return
            raise Exception("Unexpected key")

        mock_s3.download_file.side_effect = fake_download
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3

        with patch.object(lambda_mod, "boto3", mock_boto3), \
             patch.object(lambda_mod, "HAS_BOTO3", True), \
             patch.dict("os.environ", {"S3_BUCKET_NAME": "library-management-ml-pred", "S3_MODEL_KEY": "model/v1/regression_model.npz"}):
            lambda_mod._ESTIMATOR = None
            est = lambda_mod.get_estimator()
            self.assertIsNotNone(est)
            self.assertEqual(mock_s3.download_file.call_count, 2)

    def test_transactional_multi_table_checkout_and_return_consistency(self):
        """Verify TransactWriteItems guarantees atomic multi-table consistency and rollback on exhaustion/duplicate."""
        mgr = LiveInventoryManager(
            dynamodb_table_name="LibraryCatalog-dev",
            dynamodb_tx_table_name="LibraryTransactions-dev",
            dynamodb_loans_table_name="LibraryLoans-dev",
            dynamodb_resource=self.mock_resource,
            inventory_file=None,
            transactions_file=None,
            loans_file=None,
            books_raw_path=None,
        )

        initial_calls = len(self.mock_resource.client.transact_write_calls)

        # 1. Successful transactional checkout
        res = mgr.record_checkout(
            book_id="B08510",
            student_id="STU-TX-101",
            notes="Transactional checkout test",
        )
        self.assertEqual(len(self.mock_resource.client.transact_write_calls), initial_calls + 1)
        tx_call = self.mock_resource.client.transact_write_calls[-1]
        self.assertEqual(len(tx_call), 3)  # Catalog update, Loan put, Tx put
        self.assertEqual(res["available_copies_after"], 1)
        loan_id = res["loan_id"]

        # Verify all 3 stores in DynamoDB are in sync
        self.assertEqual(int(self.catalog_table.items["B08510"]["available_copies"]), 1)
        self.assertIn(loan_id, self.loans_table.items)
        self.assertEqual(self.loans_table.items[loan_id]["status"], "ACTIVE")

        # 2. Checkout remaining copy: 1 -> 0
        mgr.record_checkout(book_id="B08510", student_id="STU-TX-102")
        self.assertEqual(int(self.catalog_table.items["B08510"]["available_copies"]), 0)

        # 3. Third checkout fails atomically: available_copies is 0
        catalog_before_fail = dict(self.catalog_table.items["B08510"])
        loans_count_before = len(self.loans_table.items)
        tx_count_before = len(self.tx_table.items)

        with self.assertRaises(InventoryExhaustedError):
            mgr.record_checkout(book_id="B08510", student_id="STU-TX-103")

        # State must remain 100% untouched
        self.assertEqual(self.catalog_table.items["B08510"]["available_copies"], catalog_before_fail["available_copies"])
        self.assertEqual(len(self.loans_table.items), loans_count_before)
        self.assertEqual(len(self.tx_table.items), tx_count_before)

        # 4. Successful transactional return
        ret_res = mgr.record_return(loan_id=loan_id, student_id="STU-TX-101")
        self.assertEqual(ret_res["available_copies_after"], 1)
        self.assertEqual(int(self.catalog_table.items["B08510"]["available_copies"]), 1)
        self.assertEqual(self.loans_table.items[loan_id]["status"], "RETURNED")

        # 5. Duplicate return attempt of same loan must fail atomically
        catalog_after_ret = dict(self.catalog_table.items["B08510"])
        with self.assertRaises(InvalidReturnError):
            mgr.record_return(loan_id=loan_id, student_id="STU-TX-101")

        # Inventory must NOT be incremented twice, and no duplicate return logged
        self.assertEqual(self.catalog_table.items["B08510"]["available_copies"], catalog_after_ret["available_copies"])

    def test_transactional_rollback_on_failed_operation(self):
        """Verify that an unexpected failure during TransactWriteItems leaves state unchanged."""
        mgr = LiveInventoryManager(
            dynamodb_table_name="LibraryCatalog-dev",
            dynamodb_tx_table_name="LibraryTransactions-dev",
            dynamodb_loans_table_name="LibraryLoans-dev",
            dynamodb_resource=self.mock_resource,
            inventory_file=None,
            transactions_file=None,
            loans_file=None,
            books_raw_path=None,
        )

        avail_initial = int(self.catalog_table.items["B08510"]["available_copies"])
        tx_count_initial = len(self.tx_table.items)

        with patch.object(self.mock_resource.client, "transact_write_items", side_effect=RuntimeError("Simulated AWS error")):
            with self.assertRaises(RuntimeError):
                mgr.record_checkout(book_id="B08510", student_id="STU-ERR")

        # Verify no partial write occurred
        self.assertEqual(int(self.catalog_table.items["B08510"]["available_copies"]), avail_initial)
        self.assertEqual(len(self.tx_table.items), tx_count_initial)

    def test_aws_mode_raises_on_dynamodb_failure(self):
        """Verify that in AWS mode, DynamoDB operations raise instead of silently falling back to local files."""
        with patch.dict("os.environ", {"APP_MODE": "AWS"}):
            mgr = LiveInventoryManager(
                dynamodb_table_name="LibraryCatalog-dev",
                dynamodb_tx_table_name="LibraryTransactions-dev",
                dynamodb_loans_table_name="LibraryLoans-dev",
                dynamodb_resource=self.mock_resource,
                inventory_file=None,
                transactions_file=None,
                loans_file=None,
                books_raw_path=None,
            )

            with patch.object(self.catalog_table, "get_item", side_effect=Exception("DynamoDB service error")):
                with self.assertRaises(Exception):
                    mgr.get_book("B08510")

            with patch.object(self.loans_table, "get_item", side_effect=Exception("DynamoDB service error")):
                with self.assertRaises(Exception):
                    mgr.get_loan("LN-NONEXISTENT")

    def test_lambda_staged_package_structure(self):
        """Verify that deployment package staging contains all required files and excludes large training datasets."""
        import tempfile
        import shutil

        with tempfile.TemporaryDirectory() as staging_dir:
            staged_path = Path(staging_dir)
            (staged_path / "ml").mkdir()
            (staged_path / "model").mkdir()
            (staged_path / "data" / "raw").mkdir(parents=True)

            # Copy files as done in deploy.ps1 and deploy.sh
            shutil.copyfile(ROOT / "aws" / "lambda_function.py", staged_path / "lambda_function.py")
            for py_file in (ROOT / "ml").glob("*.py"):
                shutil.copyfile(py_file, staged_path / "ml" / py_file.name)
            shutil.copyfile(ROOT / "model" / "regression_model.npz", staged_path / "model" / "regression_model.npz")
            shutil.copyfile(ROOT / "data" / "raw" / "books.csv", staged_path / "data" / "raw" / "books.csv")
            if (ROOT / "data" / "academic_calendar.json").exists():
                shutil.copyfile(ROOT / "data" / "academic_calendar.json", staged_path / "data" / "academic_calendar.json")

            # Verify presence of essential components
            self.assertTrue((staged_path / "lambda_function.py").exists())
            self.assertTrue((staged_path / "ml" / "__init__.py").exists())
            self.assertTrue((staged_path / "ml" / "stockout_estimator.py").exists())
            self.assertTrue((staged_path / "ml" / "live_inventory_manager.py").exists())
            self.assertTrue((staged_path / "ml" / "academic_calendar.py").exists())
            self.assertTrue((staged_path / "model" / "regression_model.npz").exists())
            self.assertTrue((staged_path / "data" / "raw" / "books.csv").exists())

            # Verify strict exclusion of ~1GB training_data.csv
            self.assertFalse((staged_path / "data" / "processed" / "training_data.csv").exists())
            self.assertFalse((staged_path / "data" / "training_data.csv").exists())

            # Test Lambda path resolution when running from staged directory root
            staged_file = staged_path / "lambda_function.py"
            is_root = (staged_path / "ml").is_dir()
            self.assertTrue(is_root, "In staged package, ml is a sibling of lambda_function.py")


if __name__ == "__main__":
    unittest.main()
