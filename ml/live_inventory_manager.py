"""
Live Library Movement and Authoritative Operational Inventory Manager.

Responsibilities:
- Authoritative operational store for current book availability and transaction events.
- Processes live CHECKOUT and RETURN transactions with concurrency and boundary protection.
- Enforces strict inventory boundaries: checkouts cannot decrement below 0; returns cannot exceed total_copies.
- Enforces idempotency to prevent duplicate transaction counting.
- Dynamically computes rolling movement features (1d, 3d, 7d checkouts, 7d returns, demand growth)
  based on Asia/Kolkata calendar day boundaries.
- Seamlessly interfaces with local JSON storage or DynamoDB in AWS mode.
"""

import json
import os
import threading
import uuid
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
import zoneinfo
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
BOOKS_RAW_PATH = ROOT / "data" / "raw" / "books.csv"
LIVE_INVENTORY_PATH = ROOT / "data" / "live_inventory.json"
LIVE_TRANSACTIONS_PATH = ROOT / "data" / "live_transactions.json"
LIVE_LOANS_PATH = ROOT / "data" / "live_loans.json"
DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_LOAN_DAYS = 14


from decimal import Decimal

try:
    import boto3
    from boto3.dynamodb.conditions import Key, Attr
    from botocore.exceptions import ClientError
    HAS_BOTO3 = True
except ImportError:
    boto3 = None
    Key = None
    Attr = None
    ClientError = None
    HAS_BOTO3 = False


def _to_plain_types(val: Any) -> Any:
    """Recursively converts DynamoDB Decimal values to int or float."""
    if isinstance(val, Decimal):
        return int(val) if val % 1 == 0 else float(val)
    elif isinstance(val, dict):
        return {k: _to_plain_types(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_to_plain_types(v) for v in val]
    return val


def _serialize_to_dynamodb_val(val: Any) -> Dict[str, Any]:
    """Serialize a Python value into DynamoDB AttributeValue format."""
    if val is None:
        return {"NULL": True}
    elif isinstance(val, bool):
        return {"BOOL": val}
    elif isinstance(val, (int, float, Decimal)):
        return {"N": str(val)}
    elif isinstance(val, str):
        return {"S": val}
    elif isinstance(val, list):
        return {"L": [_serialize_to_dynamodb_val(v) for v in val]}
    elif isinstance(val, dict):
        return {"M": {k: _serialize_to_dynamodb_val(v) for k, v in val.items()}}
    else:
        return {"S": str(val)}


def _serialize_to_dynamodb_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize a Python dict into a DynamoDB item dict."""
    return {k: _serialize_to_dynamodb_val(v) for k, v in item.items()}


def _deserialize_dynamodb_val(val: Any) -> Any:
    """Deserialize a DynamoDB AttributeValue dict into a Python value."""
    if not isinstance(val, dict):
        return _to_plain_types(val)
    if "S" in val:
        return val["S"]
    if "N" in val:
        n_str = val["N"]
        return int(n_str) if "." not in n_str else float(n_str)
    if "BOOL" in val:
        return val["BOOL"]
    if "NULL" in val:
        return None
    if "L" in val:
        return [_deserialize_dynamodb_val(v) for v in val["L"]]
    if "M" in val:
        return {k: _deserialize_dynamodb_val(v) for k, v in val["M"].items()}
    return _to_plain_types(val)


def _deserialize_dynamodb_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Deserialize a DynamoDB item dict into a plain Python dict."""
    return {k: _deserialize_dynamodb_val(v) for k, v in item.items()}


class InventoryExhaustedError(Exception):
    """Raised when attempting to checkout a book with zero available copies."""
    pass


class InvalidReturnError(Exception):
    """Raised when attempting to return a book whose copies are already fully in library."""
    pass


class LiveInventoryManager:
    _instance = None
    _lock = threading.Lock()

    def __init__(
        self,
        inventory_file: Optional[Path] = None,
        transactions_file: Optional[Path] = None,
        books_raw_path: Optional[Path] = None,
        loans_file: Optional[Path] = None,
        tz_name: str = DEFAULT_TIMEZONE,
        dynamodb_table_name: Optional[str] = None,
        dynamodb_tx_table_name: Optional[str] = None,
        dynamodb_loans_table_name: Optional[str] = None,
        dynamodb_resource: Optional[Any] = None,
        dynamodb_client: Optional[Any] = None,
    ):
        self.inventory_file = inventory_file or LIVE_INVENTORY_PATH
        self.transactions_file = transactions_file or LIVE_TRANSACTIONS_PATH
        self.books_raw_path = books_raw_path or BOOKS_RAW_PATH
        self.loans_file = loans_file or (Path(self.inventory_file).parent / "live_loans.json" if self.inventory_file else LIVE_LOANS_PATH)
        self.tz_name = tz_name
        self.tz = self._load_timezone(tz_name)

        # Thread safety lock for concurrent local modifications
        self._op_lock = threading.Lock()

        # In-memory stores
        self._inventory: Dict[str, Dict[str, Any]] = {}
        self._transactions: List[Dict[str, Any]] = []
        self._loans: Dict[str, Dict[str, Any]] = {}
        self._idempotency_cache: Dict[str, Dict[str, Any]] = {}

        self.dynamodb_table_name = dynamodb_table_name or os.environ.get("DYNAMODB_TABLE_NAME")
        self.dynamodb_tx_table_name = dynamodb_tx_table_name or os.environ.get("DYNAMODB_TRANSACTIONS_TABLE")
        self.dynamodb_loans_table_name = dynamodb_loans_table_name or os.environ.get("DYNAMODB_LOANS_TABLE")
        self._dynamodb_resource = dynamodb_resource
        self._dynamodb_client = dynamodb_client

        self._catalog_table = None
        self._tx_table = None
        self._loans_table = None

        if (HAS_BOTO3 or self._dynamodb_resource or self._dynamodb_client) and (
            self.dynamodb_table_name or self.dynamodb_tx_table_name or self.dynamodb_loans_table_name
        ):
            try:
                res = self._dynamodb_resource or (
                    boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
                    if (HAS_BOTO3 and boto3) else None
                )
                if not self._dynamodb_client:
                    if HAS_BOTO3 and boto3:
                        self._dynamodb_client = boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
                    elif hasattr(res, "meta") and hasattr(res.meta, "client"):
                        self._dynamodb_client = res.meta.client
                    elif hasattr(res, "client"):
                        self._dynamodb_client = res.client
                    elif hasattr(res, "transact_write_items"):
                        self._dynamodb_client = res

                if res:
                    if self.dynamodb_table_name:
                        self._catalog_table = res.Table(self.dynamodb_table_name)
                    if self.dynamodb_tx_table_name:
                        self._tx_table = res.Table(self.dynamodb_tx_table_name)
                    if self.dynamodb_loans_table_name:
                        self._loans_table = res.Table(self.dynamodb_loans_table_name)
            except Exception as e:
                print(f"[LiveInventoryManager] Warning: DynamoDB init error: {e}")
                if os.environ.get("APP_MODE") == "AWS":
                    raise

        self._load()

    def _load_timezone(self, tz_name: str):
        try:
            return zoneinfo.ZoneInfo(tz_name)
        except Exception:
            return timezone(timedelta(hours=5, minutes=30))

    def get_current_datetime(self) -> datetime:
        return datetime.now(self.tz)

    def _load(self):
        """Initialize operational inventory, transaction logs, and loans."""
        # 1. Load or seed inventory
        if self._catalog_table:
            try:
                resp = self._catalog_table.scan()
                items = resp.get("Items", [])
                if items:
                    for item in items:
                        plain = _to_plain_types(item)
                        self._inventory[plain["book_id"]] = plain
                elif self.books_raw_path and self.books_raw_path.exists():
                    self._seed_inventory()
            except Exception as e:
                print(f"[LiveInventoryManager] Warning: DynamoDB catalog scan failed: {e}")
                if os.environ.get("APP_MODE") == "AWS":
                    raise

        if not self._catalog_table and not self._inventory and self.inventory_file and self.inventory_file.exists():
            try:
                with open(self.inventory_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._inventory = {b["book_id"]: b for b in data}
            except Exception as e:
                print(f"[LiveInventoryManager] Warning: failed to read {self.inventory_file}: {e}")
                self._seed_inventory()
        elif not self._inventory and not self._catalog_table:
            self._seed_inventory()

        # 2. Load or seed transactions
        if self._tx_table:
            try:
                resp = self._tx_table.scan()
                for tx in resp.get("Items", []):
                    plain_tx = _to_plain_types(tx)
                    self._transactions.append(plain_tx)
                    if "idempotency_key" in plain_tx and plain_tx["idempotency_key"]:
                        self._idempotency_cache[plain_tx["idempotency_key"]] = plain_tx
            except Exception as e:
                print(f"[LiveInventoryManager] Warning: DynamoDB tx scan failed: {e}")
                if os.environ.get("APP_MODE") == "AWS":
                    raise

        if not self._tx_table and not self._transactions and self.transactions_file and self.transactions_file.exists():
            try:
                with open(self.transactions_file, "r", encoding="utf-8") as f:
                    self._transactions = json.load(f)
                    for tx in self._transactions:
                        if "idempotency_key" in tx and tx["idempotency_key"]:
                            self._idempotency_cache[tx["idempotency_key"]] = tx
            except Exception as e:
                print(f"[LiveInventoryManager] Warning: failed to read {self.transactions_file}: {e}")
                self._transactions = []
        elif not self._transactions:
            self._transactions = []
            if not self._tx_table and self.transactions_file:
                self._save_transactions()

        # 3. Load or seed loans
        if self._loans_table:
            try:
                resp = self._loans_table.scan()
                for l in resp.get("Items", []):
                    plain_l = _to_plain_types(l)
                    self._loans[plain_l["loan_id"]] = plain_l
            except Exception as e:
                print(f"[LiveInventoryManager] Warning: DynamoDB loans scan failed: {e}")
                if os.environ.get("APP_MODE") == "AWS":
                    raise

        if not self._loans_table and not self._loans and self.loans_file and self.loans_file.exists():
            try:
                with open(self.loans_file, "r", encoding="utf-8") as f:
                    loans_data = json.load(f)
                    self._loans = {l["loan_id"]: l for l in loans_data}
            except Exception as e:
                print(f"[LiveInventoryManager] Warning: failed to read {self.loans_file}: {e}")
                self._loans = {}
        elif not self._loans:
            self._loans = {}
            if not self._loans_table and self.loans_file:
                self._save_loans()

    def _seed_inventory(self):
        """Seed inventory from books.csv catalog."""
        if not self.books_raw_path or not self.books_raw_path.exists():
            return

        df = pd.read_csv(self.books_raw_path)
        now_iso = self.get_current_datetime().isoformat()
        self._inventory = {}
        for _, row in df.iterrows():
            bid = str(row["book_id"]).upper()
            total = int(row["total_copies"])
            avail = int(row["available_copies"])
            on_loan = total - avail
            item_data = {
                "book_id": bid,
                "title": str(row["title"]),
                "author": str(row["author"]),
                "department": str(row["department"]),
                "edition": str(row.get("edition", "1")),
                "total_copies": total,
                "available_copies": avail,
                "checked_out_copies": on_loan,
                "last_transaction_timestamp": None,
                "updated_at": now_iso,
            }
            self._inventory[bid] = item_data
            if self._catalog_table:
                try:
                    self._catalog_table.put_item(Item=dict(item_data))
                except Exception:
                    pass
        self._save_inventory()

    def _save_inventory(self):
        if self._catalog_table:
            return
        if not self.inventory_file:
            return
        self.inventory_file.parent.mkdir(parents=True, exist_ok=True)
        items = list(self._inventory.values())
        with open(self.inventory_file, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)

    def _save_transactions(self):
        if self._tx_table:
            return
        if not self.transactions_file:
            return
        self.transactions_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.transactions_file, "w", encoding="utf-8") as f:
            json.dump(self._transactions, f, indent=2)

    def _save_loans(self):
        if self._loans_table:
            return
        if not self.loans_file:
            return
        self.loans_file.parent.mkdir(parents=True, exist_ok=True)
        items = list(self._loans.values())
        with open(self.loans_file, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)

    def get_book(self, book_id: str) -> Optional[Dict[str, Any]]:
        bid = str(book_id).upper()
        if self._catalog_table:
            try:
                resp = self._catalog_table.get_item(Key={"book_id": bid})
                item = resp.get("Item")
                if item:
                    plain = _to_plain_types(item)
                    self._inventory[bid] = plain
                    return plain
                return None
            except Exception as e:
                print(f"[LiveInventoryManager] DynamoDB get_book error: {e}")
                if os.environ.get("APP_MODE") == "AWS":
                    raise
                return self._inventory.get(bid)
        return self._inventory.get(bid)

    # Alias for API & testing convenience
    get_inventory = get_book

    # -------------------------------------------------------------
    # Live Movement Transaction Processing
    # -------------------------------------------------------------
    def record_checkout(
        self,
        book_id: str,
        idempotency_key: Optional[str] = None,
        custom_timestamp: Optional[str] = None,
        notes: Optional[str] = None,
        student_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Record a student checkout:
        - Atomic boundary validation: available_copies must be > 0.
        - Prevents duplicate execution if idempotency_key is reused.
        - Issues a 14-day academic loan associated with the student borrower.
        - Updates inventory and appends event to audit log via atomic DynamoDB TransactWriteItems.
        """
        book_id = str(book_id).upper()

        with self._op_lock:
            # Idempotency check
            if idempotency_key and idempotency_key in self._idempotency_cache:
                cached_tx = self._idempotency_cache[idempotency_key]
                cached_loan = self.get_loan(cached_tx.get("loan_id")) if cached_tx.get("loan_id") else None
                current_book = self.get_book(book_id)
                return {
                    "is_duplicate": True,
                    "cached": True,
                    "action": cached_tx.get("transaction_type", "CHECKOUT"),
                    "transaction_id": cached_tx.get("transaction_id"),
                    "loan_id": cached_tx.get("loan_id"),
                    "timestamp": cached_tx.get("timestamp"),
                    "due_date": cached_tx.get("due_date"),
                    "available_copies_after": current_book.get("available_copies") if current_book else None,
                    "transaction": cached_tx,
                    "loan": cached_loan,
                    "inventory": current_book,
                }

            tx_time = custom_timestamp or self.get_current_datetime().isoformat()
            tx_id = f"TX-CO-{uuid.uuid4().hex[:8].upper()}"
            loan_id = f"LN-{uuid.uuid4().hex[:8].upper()}"

            try:
                tx_dt = datetime.fromisoformat(tx_time)
                if tx_dt.tzinfo is None:
                    tx_dt = tx_dt.replace(tzinfo=self.tz)
            except Exception:
                tx_dt = self.get_current_datetime()
            due_dt = tx_dt + timedelta(days=DEFAULT_LOAN_DAYS)
            due_date_iso = due_dt.isoformat()

            # Pre-retrieve existing book metadata
            existing_book = self.get_book(book_id)
            if not existing_book:
                raise KeyError(f"Book ID '{book_id}' does not exist in library inventory.")
            if int(existing_book.get("available_copies", 0)) <= 0:
                raise InventoryExhaustedError(
                    f"Cannot checkout book '{book_id}' ({existing_book.get('title', '')}): All {existing_book.get('total_copies', 0)} copies are currently on loan."
                )

            avail_after = int(existing_book["available_copies"]) - 1
            checked_out_after = int(existing_book.get("checked_out_copies", 0)) + 1

            updated_book = dict(existing_book)
            updated_book["available_copies"] = avail_after
            updated_book["checked_out_copies"] = checked_out_after
            updated_book["last_transaction_timestamp"] = tx_time
            updated_book["updated_at"] = tx_time

            tx_record = {
                "transaction_id": tx_id,
                "loan_id": loan_id,
                "book_id": book_id,
                "transaction_type": "CHECKOUT",
                "timestamp": tx_time,
                "due_date": due_date_iso,
                "idempotency_key": idempotency_key,
                "student_id": student_id,
                "available_after": avail_after,
                "total_copies": int(existing_book["total_copies"]),
                "notes": notes or "Live student checkout",
            }

            loan_record = {
                "loan_id": loan_id,
                "book_id": book_id,
                "title": str(existing_book.get("title", "")),
                "author": str(existing_book.get("author", "")),
                "department": str(existing_book.get("department", "")),
                "edition": str(existing_book.get("edition", "1st Edition")),
                "student_id": student_id,
                "checkout_date": tx_time,
                "due_date": due_date_iso,
                "status": "ACTIVE",
                "returned_date": None,
                "checkout_tx_id": tx_id,
                "return_tx_id": None,
            }

            # DynamoDB TransactWriteItems execution
            if self._dynamodb_client and self.dynamodb_table_name:
                transact_items = [
                    {
                        "Update": {
                            "TableName": self.dynamodb_table_name,
                            "Key": {"book_id": {"S": book_id}},
                            "UpdateExpression": "SET available_copies = available_copies - :one, checked_out_copies = checked_out_copies + :one, last_transaction_timestamp = :tx_time, updated_at = :tx_time",
                            "ConditionExpression": "attribute_exists(book_id) AND available_copies > :zero",
                            "ExpressionAttributeValues": {
                                ":one": {"N": "1"},
                                ":zero": {"N": "0"},
                                ":tx_time": {"S": tx_time},
                            },
                        }
                    }
                ]
                if self.dynamodb_loans_table_name:
                    transact_items.append({
                        "Put": {
                            "TableName": self.dynamodb_loans_table_name,
                            "Item": _serialize_to_dynamodb_item(loan_record),
                            "ConditionExpression": "attribute_not_exists(loan_id)",
                        }
                    })
                if self.dynamodb_tx_table_name:
                    transact_items.append({
                        "Put": {
                            "TableName": self.dynamodb_tx_table_name,
                            "Item": _serialize_to_dynamodb_item(tx_record),
                        }
                    })

                try:
                    self._dynamodb_client.transact_write_items(TransactItems=transact_items)
                except Exception as exc:
                    err_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
                    cancellation_reasons = getattr(exc, "response", {}).get("CancellationReasons", [])
                    if "TransactionCanceledException" in str(exc) or err_code == "TransactionCanceledException":
                        if cancellation_reasons and cancellation_reasons[0].get("Code") == "ConditionalCheckFailed":
                            fresh_book = self.get_book(book_id)
                            if not fresh_book:
                                raise KeyError(f"Book ID '{book_id}' does not exist in library inventory.")
                            raise InventoryExhaustedError(
                                f"Cannot checkout book '{book_id}' ({fresh_book.get('title', '')}): All {fresh_book.get('total_copies', 0)} copies are currently on loan."
                            )
                        fresh_book = self.get_book(book_id)
                        if not fresh_book:
                            raise KeyError(f"Book ID '{book_id}' does not exist in library inventory.")
                        if int(fresh_book.get("available_copies", 0)) <= 0:
                            raise InventoryExhaustedError(
                                f"Cannot checkout book '{book_id}' ({fresh_book.get('title', '')}): All {fresh_book.get('total_copies', 0)} copies are currently on loan."
                            )
                        raise RuntimeError(f"DynamoDB checkout transaction failed: {exc}")
                    elif "ConditionalCheckFailed" in str(exc) or err_code == "ConditionalCheckFailedException":
                        fresh_book = self.get_book(book_id)
                        if not fresh_book:
                            raise KeyError(f"Book ID '{book_id}' does not exist in library inventory.")
                        raise InventoryExhaustedError(
                            f"Cannot checkout book '{book_id}' ({fresh_book.get('title', '')}): All {fresh_book.get('total_copies', 0)} copies are currently on loan."
                        )
                    else:
                        raise exc
            elif self._catalog_table:
                # Fallback if no transact client is present
                try:
                    resp = self._catalog_table.update_item(
                        Key={"book_id": book_id},
                        UpdateExpression="SET available_copies = available_copies - :one, checked_out_copies = checked_out_copies + :one, last_transaction_timestamp = :tx_time, updated_at = :tx_time",
                        ConditionExpression="attribute_exists(book_id) AND available_copies > :zero",
                        ExpressionAttributeValues={
                            ":one": 1,
                            ":zero": 0,
                            ":tx_time": tx_time,
                        },
                        ReturnValues="ALL_NEW",
                    )
                except Exception as exc:
                    err_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
                    if "ConditionalCheckFailed" in str(exc) or err_code == "ConditionalCheckFailedException":
                        raise InventoryExhaustedError(
                            f"Cannot checkout book '{book_id}' ({existing_book.get('title', '')}): All {existing_book.get('total_copies', 0)} copies are currently on loan."
                        )
                    raise
                if self._loans_table:
                    self._loans_table.put_item(Item=dict(loan_record))
                if self._tx_table:
                    self._tx_table.put_item(Item=dict(tx_record))

            self._loans[loan_id] = loan_record
            self._transactions.append(tx_record)
            self._inventory[book_id] = updated_book
            if idempotency_key:
                self._idempotency_cache[idempotency_key] = tx_record

            self._save_inventory()
            self._save_transactions()
            self._save_loans()

            return {
                "is_duplicate": False,
                "cached": False,
                "action": "CHECKOUT",
                "transaction_id": tx_id,
                "loan_id": loan_id,
                "timestamp": tx_time,
                "due_date": due_date_iso,
                "available_copies_after": avail_after,
                "transaction": tx_record,
                "loan": dict(loan_record),
                "inventory": dict(updated_book),
            }

    def record_return(
        self,
        book_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        custom_timestamp: Optional[str] = None,
        notes: Optional[str] = None,
        student_id: Optional[str] = None,
        loan_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Record a student book return:
        - Validates loan identity, ownership, and active status if loan_id or student_id provided.
        - Atomic boundary validation: available_copies cannot exceed total_copies.
        - Prevents duplicate execution if idempotency_key is reused or loan already returned.
        - Updates inventory, marks loan returned, and appends event to audit log via atomic DynamoDB TransactWriteItems.
        """
        with self._op_lock:
            # Idempotency check
            if idempotency_key and idempotency_key in self._idempotency_cache:
                cached_tx = self._idempotency_cache[idempotency_key]
                bid = cached_tx.get("book_id")
                cached_loan = self.get_loan(cached_tx.get("loan_id")) if cached_tx.get("loan_id") else None
                current_book = self.get_book(bid) if bid else None
                return {
                    "is_duplicate": True,
                    "cached": True,
                    "action": cached_tx.get("transaction_type", "RETURN"),
                    "transaction_id": cached_tx.get("transaction_id"),
                    "loan_id": cached_tx.get("loan_id"),
                    "timestamp": cached_tx.get("timestamp"),
                    "available_copies_after": current_book.get("available_copies") if current_book else None,
                    "transaction": cached_tx,
                    "loan": cached_loan,
                    "inventory": current_book,
                }

            target_loan = None

            # Case A: Explicit loan_id provided
            if loan_id:
                loan_id_clean = str(loan_id).strip()
                target_loan = self.get_loan(loan_id_clean)
                if not target_loan:
                    raise KeyError(f"Loan record '{loan_id}' not found.")

                if target_loan.get("status") == "RETURNED":
                    raise InvalidReturnError(
                        f"Cannot return loan '{loan_id}': This loan has already been returned on {target_loan.get('returned_date')}."
                    )

                if student_id and target_loan.get("student_id") and target_loan.get("student_id") != student_id:
                    raise PermissionError(
                        f"Unauthorized: Loan '{loan_id}' belongs to student '{target_loan.get('student_id')}', not '{student_id}'."
                    )

                resolved_book_id = target_loan["book_id"]
                if book_id and str(book_id).upper() != resolved_book_id:
                    raise ValueError(f"Book ID '{book_id}' does not match loan record '{resolved_book_id}'.")
                book_id = resolved_book_id

            # Case B: loan_id NOT provided, but student_id and book_id provided
            elif student_id and book_id:
                book_id = str(book_id).upper()
                active_loans = self.get_active_loans(student_id)
                matching = next(
                    (l for l in active_loans if l.get("book_id") == book_id and l.get("status") == "ACTIVE"),
                    None
                )
                if matching:
                    target_loan = matching
                else:
                    all_active = [l for l in self.get_active_loans() if l.get("book_id") == book_id and l.get("status") == "ACTIVE"]
                    if all_active:
                        raise PermissionError(
                            f"Borrower '{student_id}' does not have an active loan for book '{book_id}'."
                        )
            elif book_id:
                book_id = str(book_id).upper()
                active_for_book = [l for l in self.get_active_loans() if l.get("book_id") == book_id and l.get("status") == "ACTIVE"]
                if active_for_book:
                    active_for_book.sort(key=lambda x: str(x.get("checkout_date", "")))
                    target_loan = active_for_book[0]
            else:
                raise ValueError("Either 'loan_id' or 'book_id' must be specified for a return.")

            book_id = str(book_id).upper()
            tx_time = custom_timestamp or self.get_current_datetime().isoformat()
            tx_id = f"TX-RET-{uuid.uuid4().hex[:8].upper()}"

            existing_book = self.get_book(book_id)
            if not existing_book:
                raise KeyError(f"Book ID '{book_id}' does not exist in library inventory.")
            if int(existing_book.get("available_copies", 0)) >= int(existing_book.get("total_copies", 0)):
                raise InvalidReturnError(
                    f"Cannot return book '{book_id}': All {existing_book['total_copies']} copies are already present in inventory."
                )

            avail_after = int(existing_book["available_copies"]) + 1
            checked_out_after = max(0, int(existing_book.get("checked_out_copies", 0)) - 1)

            updated_book = dict(existing_book)
            updated_book["available_copies"] = avail_after
            updated_book["checked_out_copies"] = checked_out_after
            updated_book["last_transaction_timestamp"] = tx_time
            updated_book["updated_at"] = tx_time

            tx_record = {
                "transaction_id": tx_id,
                "loan_id": target_loan["loan_id"] if target_loan else None,
                "book_id": book_id,
                "transaction_type": "RETURN",
                "timestamp": tx_time,
                "idempotency_key": idempotency_key,
                "student_id": student_id or (target_loan.get("student_id") if target_loan else None),
                "available_after": avail_after,
                "total_copies": int(existing_book["total_copies"]),
                "notes": notes or "Live student return",
            }

            updated_loan = None
            if target_loan:
                updated_loan = dict(target_loan)
                updated_loan["status"] = "RETURNED"
                updated_loan["returned_date"] = tx_time
                updated_loan["return_tx_id"] = tx_id

            if self._dynamodb_client and self.dynamodb_table_name:
                transact_items = [
                    {
                        "Update": {
                            "TableName": self.dynamodb_table_name,
                            "Key": {"book_id": {"S": book_id}},
                            "UpdateExpression": "SET available_copies = available_copies + :one, checked_out_copies = checked_out_copies - :one, last_transaction_timestamp = :tx_time, updated_at = :tx_time",
                            "ConditionExpression": "attribute_exists(book_id) AND available_copies < total_copies",
                            "ExpressionAttributeValues": {
                                ":one": {"N": "1"},
                                ":tx_time": {"S": tx_time},
                            },
                        }
                    }
                ]
                if target_loan and self.dynamodb_loans_table_name:
                    transact_items.append({
                        "Update": {
                            "TableName": self.dynamodb_loans_table_name,
                            "Key": {"loan_id": {"S": str(target_loan["loan_id"]).strip()}},
                            "UpdateExpression": "SET #st = :ret, returned_date = :tx_time, return_tx_id = :tx_id",
                            "ConditionExpression": "attribute_exists(loan_id) AND #st = :active",
                            "ExpressionAttributeNames": {
                                "#st": "status",
                            },
                            "ExpressionAttributeValues": {
                                ":ret": {"S": "RETURNED"},
                                ":active": {"S": "ACTIVE"},
                                ":tx_time": {"S": tx_time},
                                ":tx_id": {"S": tx_id},
                            },
                        }
                    })
                if self.dynamodb_tx_table_name:
                    transact_items.append({
                        "Put": {
                            "TableName": self.dynamodb_tx_table_name,
                            "Item": _serialize_to_dynamodb_item(tx_record),
                        }
                    })

                try:
                    self._dynamodb_client.transact_write_items(TransactItems=transact_items)
                except Exception as exc:
                    err_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
                    cancellation_reasons = getattr(exc, "response", {}).get("CancellationReasons", [])
                    if "TransactionCanceledException" in str(exc) or err_code == "TransactionCanceledException":
                        if len(cancellation_reasons) > 1 and cancellation_reasons[1].get("Code") == "ConditionalCheckFailed":
                            raise InvalidReturnError(
                                f"Cannot return loan '{target_loan['loan_id']}': This loan has already been returned or is not active."
                            )
                        if len(cancellation_reasons) > 0 and cancellation_reasons[0].get("Code") == "ConditionalCheckFailed":
                            fresh_book = self.get_book(book_id)
                            if not fresh_book:
                                raise KeyError(f"Book ID '{book_id}' does not exist in library inventory.")
                            raise InvalidReturnError(
                                f"Cannot return book '{book_id}': All {fresh_book.get('total_copies', 0)} copies are already present in inventory."
                            )
                        if target_loan:
                            l_check = self.get_loan(target_loan["loan_id"])
                            if l_check and l_check.get("status") == "RETURNED":
                                raise InvalidReturnError(
                                    f"Cannot return loan '{target_loan['loan_id']}': This loan has already been returned on {l_check.get('returned_date')}."
                                )
                        fresh_book = self.get_book(book_id)
                        if not fresh_book:
                            raise KeyError(f"Book ID '{book_id}' does not exist in library inventory.")
                        if int(fresh_book.get("available_copies", 0)) >= int(fresh_book.get("total_copies", 0)):
                            raise InvalidReturnError(
                                f"Cannot return book '{book_id}': All {fresh_book.get('total_copies', 0)} copies are already present in inventory."
                            )
                        raise RuntimeError(f"DynamoDB return transaction failed: {exc}")
                    elif "ConditionalCheckFailed" in str(exc) or err_code == "ConditionalCheckFailedException":
                        fresh_book = self.get_book(book_id)
                        if not fresh_book:
                            raise KeyError(f"Book ID '{book_id}' does not exist in library inventory.")
                        raise InvalidReturnError(
                            f"Cannot return book '{book_id}': All {fresh_book.get('total_copies', 0)} copies are already present in inventory."
                        )
                    else:
                        raise
            elif self._catalog_table:
                # Fallback if no transact client is present
                try:
                    resp = self._catalog_table.update_item(
                        Key={"book_id": book_id},
                        UpdateExpression="SET available_copies = available_copies + :one, checked_out_copies = checked_out_copies - :one, last_transaction_timestamp = :tx_time, updated_at = :tx_time",
                        ConditionExpression="attribute_exists(book_id) AND available_copies < total_copies",
                        ExpressionAttributeValues={
                            ":one": 1,
                            ":tx_time": tx_time,
                        },
                        ReturnValues="ALL_NEW",
                    )
                except Exception as exc:
                    err_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
                    if "ConditionalCheckFailed" in str(exc) or err_code == "ConditionalCheckFailedException":
                        raise InvalidReturnError(
                            f"Cannot return book '{book_id}': All {existing_book.get('total_copies', 0)} copies are already present in inventory."
                        )
                    raise
                if updated_loan and self._loans_table:
                    self._loans_table.put_item(Item=dict(updated_loan))
                if self._tx_table:
                    self._tx_table.put_item(Item=dict(tx_record))

            if updated_loan:
                self._loans[updated_loan["loan_id"]] = updated_loan
            self._transactions.append(tx_record)
            self._inventory[book_id] = updated_book
            if idempotency_key:
                self._idempotency_cache[idempotency_key] = tx_record

            self._save_inventory()
            self._save_transactions()
            self._save_loans()

            return {
                "is_duplicate": False,
                "cached": False,
                "action": "RETURN",
                "transaction_id": tx_id,
                "loan_id": target_loan["loan_id"] if target_loan else None,
                "timestamp": tx_time,
                "available_copies_after": avail_after,
                "transaction": tx_record,
                "loan": dict(updated_loan) if updated_loan else None,
                "inventory": dict(updated_book),
            }

    def get_loan(self, loan_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific loan record by ID."""
        loan_id_clean = str(loan_id).strip()
        if self._loans_table:
            try:
                resp = self._loans_table.get_item(Key={"loan_id": loan_id_clean})
                item = resp.get("Item")
                if item:
                    plain = _to_plain_types(item)
                    self._loans[loan_id_clean] = plain
                    return plain
                return None
            except Exception as e:
                print(f"[LiveInventoryManager] DynamoDB get_loan error: {e}")
                if os.environ.get("APP_MODE") == "AWS":
                    raise
                return self._loans.get(loan_id_clean)
        return self._loans.get(loan_id_clean)

    def get_active_loans(self, student_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve all currently active loans, optionally filtered by student_id."""
        now_dt = self.get_current_datetime()
        source_loans = []

        if self._loans_table:
            try:
                if student_id and Key is not None:
                    try:
                        resp = self._loans_table.query(
                            IndexName="StudentIndex",
                            KeyConditionExpression=Key("student_id").eq(student_id),
                        )
                        source_loans = _to_plain_types(resp.get("Items", []))
                    except Exception:
                        resp = self._loans_table.scan()
                        source_loans = _to_plain_types(resp.get("Items", []))
                else:
                    resp = self._loans_table.scan()
                    source_loans = _to_plain_types(resp.get("Items", []))
            except Exception as e:
                print(f"[LiveInventoryManager] DynamoDB get_active_loans error: {e}")
                if os.environ.get("APP_MODE") == "AWS":
                    raise
                source_loans = list(self._loans.values())
        else:
            source_loans = list(self._loans.values())

        res = []
        for l in source_loans:
            if l.get("status") == "ACTIVE":
                if student_id and l.get("student_id") != student_id:
                    continue
                copy_l = dict(l)
                try:
                    due_dt = datetime.fromisoformat(l["due_date"])
                    if due_dt.tzinfo is None:
                        due_dt = due_dt.replace(tzinfo=self.tz)
                    days_rem = (due_dt.date() - now_dt.date()).days
                    copy_l["days_remaining"] = days_rem
                    copy_l["is_overdue"] = days_rem < 0
                except Exception:
                    copy_l["days_remaining"] = 14
                    copy_l["is_overdue"] = False
                res.append(copy_l)
        res.sort(key=lambda x: str(x.get("checkout_date", "")), reverse=True)
        return res

    def get_student_loan_history(self, student_id: str) -> List[Dict[str, Any]]:
        """Retrieve complete loan history (active and returned) for a student."""
        now_dt = self.get_current_datetime()
        source_loans = []

        if self._loans_table:
            try:
                if Key is not None:
                    try:
                        resp = self._loans_table.query(
                            IndexName="StudentIndex",
                            KeyConditionExpression=Key("student_id").eq(student_id),
                        )
                        source_loans = _to_plain_types(resp.get("Items", []))
                    except Exception:
                        resp = self._loans_table.scan()
                        source_loans = _to_plain_types(resp.get("Items", []))
                else:
                    resp = self._loans_table.scan()
                    source_loans = _to_plain_types(resp.get("Items", []))
            except Exception as e:
                print(f"[LiveInventoryManager] DynamoDB get_student_loan_history error: {e}")
                if os.environ.get("APP_MODE") == "AWS":
                    raise
                source_loans = list(self._loans.values())
        else:
            source_loans = list(self._loans.values())

        res = []
        for l in source_loans:
            if l.get("student_id") == student_id:
                copy_l = dict(l)
                try:
                    due_dt = datetime.fromisoformat(l["due_date"])
                    if due_dt.tzinfo is None:
                        due_dt = due_dt.replace(tzinfo=self.tz)
                    days_rem = (due_dt.date() - now_dt.date()).days
                    copy_l["days_remaining"] = days_rem
                    copy_l["is_overdue"] = (l.get("status") == "ACTIVE" and days_rem < 0)
                except Exception:
                    copy_l["days_remaining"] = 14
                    copy_l["is_overdue"] = False
                res.append(copy_l)
        res.sort(key=lambda x: str(x.get("checkout_date", "")), reverse=True)
        return res

    # -------------------------------------------------------------
    # Live Movement Feature Calculation (1d, 3d, 7d Aggregates)
    # -------------------------------------------------------------
    def get_live_movement_aggregates(
        self,
        book_id: str,
        as_of_datetime: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Calculate rolling checkout and return counts over 1, 3, and 7 calendar days
        relative to as_of_datetime in Asia/Kolkata.
        """
        book_id = str(book_id).upper()
        ref_dt = as_of_datetime or self.get_current_datetime()
        if ref_dt.tzinfo is None:
            ref_dt = ref_dt.replace(tzinfo=self.tz)

        # Time windows based on calendar days
        t_now = ref_dt
        t_1d_cutoff = t_now - timedelta(days=1)
        t_3d_cutoff = t_now - timedelta(days=3)
        t_7d_cutoff = t_now - timedelta(days=7)

        c1d = 0.0
        c3d = 0.0
        c7d = 0.0
        r7d = 0.0
        latest_tx_time = None

        if self._tx_table:
            try:
                if Key is not None:
                    resp = self._tx_table.query(
                        KeyConditionExpression=Key("book_id").eq(book_id)
                    )
                    book_txs = _to_plain_types(resp.get("Items", []))
                else:
                    resp = self._tx_table.scan()
                    all_txs = _to_plain_types(resp.get("Items", []))
                    book_txs = [t for t in all_txs if t.get("book_id") == book_id]
            except Exception as e:
                print(f"[LiveInventoryManager] DynamoDB get_live_movement_aggregates error: {e}")
                if os.environ.get("APP_MODE") == "AWS":
                    raise
                book_txs = [t for t in self._transactions if t.get("book_id") == book_id]
        else:
            book_txs = [t for t in self._transactions if t.get("book_id") == book_id]

        for tx in book_txs:
            try:
                tx_dt = datetime.fromisoformat(tx["timestamp"])
                if tx_dt.tzinfo is None:
                    tx_dt = tx_dt.replace(tzinfo=self.tz)
            except Exception:
                continue

            # Only consider transactions up to as_of_datetime (strict no-future leakage)
            if tx_dt > t_now:
                continue

            if latest_tx_time is None or tx_dt > latest_tx_time:
                latest_tx_time = tx_dt

            ttype = tx.get("transaction_type")
            if ttype == "CHECKOUT":
                if tx_dt >= t_7d_cutoff:
                    c7d += 1.0
                if tx_dt >= t_3d_cutoff:
                    c3d += 1.0
                if tx_dt >= t_1d_cutoff:
                    c1d += 1.0
            elif ttype == "RETURN":
                if tx_dt >= t_7d_cutoff:
                    r7d += 1.0

        growth = (c3d / 3.0) - (c7d / 7.0)

        return {
            "book_id": book_id,
            "checkouts_last_1d": c1d,
            "checkouts_last_3d": c3d,
            "checkouts_last_7d": c7d,
            "returns_last_7d": r7d,
            "demand_growth": round(growth, 4),
            "as_of_timestamp": ref_dt.isoformat(),
            "latest_transaction_timestamp": latest_tx_time.isoformat() if latest_tx_time else None,
            "total_recorded_events": len(book_txs),
            "has_recent_movement": bool(c7d > 0 or r7d > 0),
        }
