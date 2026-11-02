"""
Authoritative Academic Calendar Management and Exam Countdown Engine.

Responsibilities:
- Stores authoritative university examination schedule.
- Provides administrative CRUD operations with date range validation and duplicate prevention.
- Automatically calculates days remaining until next scheduled exam relative to Asia/Kolkata timezone.
- Correctly identifies 'IN_PROGRESS' exams, 'UPCOMING' exams, and 'NO_EXAM_SCHEDULED' safe fallbacks.
- Interfaces with local JSON persistence in demo mode or DynamoDB when AWS credentials exist.
- Protects administrative endpoints against student modification.
"""

import os
import json
import uuid
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional
import zoneinfo

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CALENDAR_FILE = ROOT / "data" / "academic_calendar.json"
DEFAULT_TIMEZONE = "Asia/Kolkata"
ADMIN_API_KEY_ENV = "ADMIN_API_KEY"
DEFAULT_ADMIN_KEY = "library-admin-secret-key-2026"

VALID_EXAM_TYPES = ["CAT-1", "CAT-2", "FAT"]
VALID_SEMESTERS = ["Fall", "Winter", "Summer"]
VALID_STATUSES = ["scheduled", "completed", "cancelled"]

# Model feature mapping: 'CAT-1' -> 'CAT1', 'CAT-2' -> 'CAT2', 'FAT' -> 'FAT'
MODEL_EXAM_TYPE_MAP = {
    "CAT-1": "CAT1",
    "CAT-2": "CAT2",
    "FAT": "FAT",
    "CAT1": "CAT1",
    "CAT2": "CAT2",
}

INITIAL_SEED_ENTRIES = [
    {
        "calendar_id": "CAL-2026F-CAT1",
        "academic_year": "2026-2027",
        "semester": "Fall",
        "exam_type": "CAT-1",
        "start_date": "2026-08-08",
        "end_date": "2026-08-14",
        "status": "completed",
        "notes": "Continuous Assessment Test 1 (Fall 2026)",
        "created_at": "2026-07-01T09:00:00+05:30",
        "updated_at": "2026-08-15T09:00:00+05:30",
    },
    {
        "calendar_id": "CAL-2026F-CAT2",
        "academic_year": "2026-2027",
        "semester": "Fall",
        "exam_type": "CAT-2",
        "start_date": "2026-09-25",
        "end_date": "2026-10-01",
        "status": "completed",
        "notes": "Continuous Assessment Test 2 (Fall 2026) - Peak borrowing period",
        "created_at": "2026-07-01T09:00:00+05:30",
        "updated_at": "2026-10-02T09:00:00+05:30",
    },
    {
        "calendar_id": "CAL-2026F-FAT",
        "academic_year": "2026-2027",
        "semester": "Fall",
        "exam_type": "FAT",
        "start_date": "2026-11-20",
        "end_date": "2026-11-30",
        "status": "scheduled",
        "notes": "Final Assessment Test (Fall 2026)",
        "created_at": "2026-07-01T09:00:00+05:30",
        "updated_at": "2026-10-10T09:00:00+05:30",
    },
    {
        "calendar_id": "CAL-2027W-CAT1",
        "academic_year": "2026-2027",
        "semester": "Winter",
        "exam_type": "CAT-1",
        "start_date": "2027-01-26",
        "end_date": "2027-02-01",
        "status": "scheduled",
        "notes": "Continuous Assessment Test 1 (Winter 2027)",
        "created_at": "2026-10-01T09:00:00+05:30",
        "updated_at": "2026-10-01T09:00:00+05:30",
    },
    {
        "calendar_id": "CAL-2027W-CAT2",
        "academic_year": "2026-2027",
        "semester": "Winter",
        "exam_type": "CAT-2",
        "start_date": "2027-03-15",
        "end_date": "2027-03-21",
        "status": "scheduled",
        "notes": "Continuous Assessment Test 2 (Winter 2027)",
        "created_at": "2026-10-01T09:00:00+05:30",
        "updated_at": "2026-10-01T09:00:00+05:30",
    },
    {
        "calendar_id": "CAL-2027W-FAT",
        "academic_year": "2026-2027",
        "semester": "Winter",
        "exam_type": "FAT",
        "start_date": "2027-04-25",
        "end_date": "2027-05-05",
        "status": "scheduled",
        "notes": "Final Assessment Test (Winter 2027)",
        "created_at": "2026-10-01T09:00:00+05:30",
        "updated_at": "2026-10-01T09:00:00+05:30",
    },
]


try:
    import boto3
    from boto3.dynamodb.conditions import Key, Attr
    HAS_BOTO3 = True
except ImportError:
    boto3 = None
    Key = None
    Attr = None
    HAS_BOTO3 = False


class AcademicCalendar:
    def __init__(
        self,
        storage_file: Optional[Path] = None,
        tz_name: str = DEFAULT_TIMEZONE,
        dynamodb_table_name: Optional[str] = None,
        dynamodb_resource: Optional[Any] = None,
    ):
        self.storage_file = storage_file or DEFAULT_CALENDAR_FILE
        self.tz_name = tz_name
        self.tz = self._load_timezone(tz_name)
        self.dynamodb_table_name = dynamodb_table_name or os.environ.get("DYNAMODB_CALENDAR_TABLE")
        self._dynamodb_resource = dynamodb_resource
        self._dynamodb_table = None
        self._cache: Dict[str, Dict[str, Any]] = {}

        if self.dynamodb_table_name and (HAS_BOTO3 or self._dynamodb_resource):
            try:
                res = self._dynamodb_resource or boto3.resource(
                    "dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1")
                )
                self._dynamodb_table = res.Table(self.dynamodb_table_name)
            except Exception as e:
                print(f"[AcademicCalendar] Warning: Failed to connect to DynamoDB table '{self.dynamodb_table_name}': {e}")
                self._dynamodb_table = None

        self._load()

    def _load_timezone(self, tz_name: str):
        try:
            return zoneinfo.ZoneInfo(tz_name)
        except Exception:
            # Fallback to UTC+5:30 for Asia/Kolkata
            return timezone(timedelta(hours=5, minutes=30))

    def get_current_datetime(self) -> datetime:
        """Return current datetime localized to configured timezone."""
        return datetime.now(self.tz)

    def get_current_date(self) -> date:
        """Return current calendar date in configured timezone."""
        return self.get_current_datetime().date()

    def _load(self):
        """Load entries from DynamoDB or local file."""
        if self._dynamodb_table:
            try:
                resp = self._dynamodb_table.scan()
                items = resp.get("Items", [])
                if items:
                    self._cache = {item["calendar_id"]: item for item in items}
                    return
                else:
                    # Seed DynamoDB table if empty
                    for item in INITIAL_SEED_ENTRIES:
                        self._dynamodb_table.put_item(Item=dict(item))
                    self._cache = {item["calendar_id"]: dict(item) for item in INITIAL_SEED_ENTRIES}
                    return
            except Exception as e:
                print(f"[AcademicCalendar] Warning: DynamoDB initial load failed: {e}")
                if os.environ.get("APP_MODE") == "AWS":
                    raise

        if self.storage_file and self.storage_file.exists():
            try:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._cache = {item["calendar_id"]: item for item in data}
                    return
            except Exception as e:
                print(f"[AcademicCalendar] Warning: Failed to read {self.storage_file}: {e}")

        # Seed initial entries locally
        self._cache = {item["calendar_id"]: dict(item) for item in INITIAL_SEED_ENTRIES}
        if not self._dynamodb_table and self.storage_file:
            self._save()

    def _save(self):
        """Persist entries to local JSON file when running in local file mode."""
        if self._dynamodb_table:
            return
        if not self.storage_file:
            return
        self.storage_file.parent.mkdir(parents=True, exist_ok=True)
        entries_list = list(self._cache.values())
        # Sort by start_date ascending
        entries_list.sort(key=lambda x: x["start_date"])
        with open(self.storage_file, "w", encoding="utf-8") as f:
            json.dump(entries_list, f, indent=2)

    def verify_admin_key(self, provided_key: Optional[str]) -> bool:
        """Check administrator authentication key."""
        expected_key = os.environ.get(ADMIN_API_KEY_ENV, DEFAULT_ADMIN_KEY)
        if not provided_key:
            return False
        # Remove any 'Bearer ' prefix
        clean_key = provided_key.replace("Bearer ", "").strip()
        return clean_key == expected_key

    def validate_entry_payload(self, data: Dict[str, Any], is_update: bool = False, existing_id: Optional[str] = None) -> None:
        """Strict validation of dates, formats, and duplicate entries."""
        if not is_update:
            for req in ["academic_year", "semester", "exam_type", "start_date", "end_date"]:
                if req not in data or not str(data[req]).strip():
                    raise ValueError(f"Missing required field: '{req}'")

        exam_type = data.get("exam_type")
        if exam_type and exam_type not in VALID_EXAM_TYPES:
            raise ValueError(f"Invalid exam_type '{exam_type}'. Allowed values: {VALID_EXAM_TYPES}")

        semester = data.get("semester")
        if semester and semester not in VALID_SEMESTERS:
            raise ValueError(f"Invalid semester '{semester}'. Allowed values: {VALID_SEMESTERS}")

        status = data.get("status", "scheduled")
        if status and status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Allowed values: {VALID_STATUSES}")

        # Date validation
        start_date_str = data.get("start_date")
        end_date_str = data.get("end_date")

        start_d: Optional[date] = None
        end_d: Optional[date] = None

        if start_date_str:
            try:
                start_d = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError(f"Invalid start_date '{start_date_str}'. Expected format YYYY-MM-DD.")

        if end_date_str:
            try:
                end_d = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError(f"Invalid end_date '{end_date_str}'. Expected format YYYY-MM-DD.")

        if start_d and end_d and start_d > end_d:
            raise ValueError(f"Invalid date range: start_date ({start_d}) cannot be after end_date ({end_d}).")

        # Duplicate scheduled exam check
        academic_year = data.get("academic_year")
        if status == "scheduled" and academic_year and semester and exam_type:
            for cid, item in self._cache.items():
                if is_update and cid == existing_id:
                    continue
                if (
                    item.get("status") == "scheduled"
                    and item.get("academic_year") == academic_year
                    and item.get("semester") == semester
                    and item.get("exam_type") == exam_type
                ):
                    raise ValueError(
                        f"Duplicate exam entry conflict: '{exam_type}' for {academic_year} {semester} "
                        f"is already scheduled as entry '{cid}'."
                    )

    # -------------------------------------------------------------
    # Administrator CRUD Operations
    # -------------------------------------------------------------
    def list_entries(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all calendar entries, optionally filtered by status."""
        if self._dynamodb_table:
            try:
                if status:
                    try:
                        if Key is not None:
                            resp = self._dynamodb_table.query(
                                IndexName="StatusStartDateIndex",
                                KeyConditionExpression=Key("status").eq(status),
                            )
                            items = resp.get("Items", [])
                        else:
                            resp = self._dynamodb_table.scan()
                            items = [x for x in resp.get("Items", []) if x.get("status") == status]
                    except Exception:
                        resp = self._dynamodb_table.scan()
                        items = [x for x in resp.get("Items", []) if x.get("status") == status]
                else:
                    resp = self._dynamodb_table.scan()
                    items = resp.get("Items", [])

                for item in items:
                    self._cache[item["calendar_id"]] = item
                items.sort(key=lambda x: x.get("start_date", ""))
                return items
            except Exception as e:
                print(f"[AcademicCalendar] DynamoDB list_entries error: {e}")

        items = list(self._cache.values())
        if status:
            items = [x for x in items if x.get("status") == status]
        items.sort(key=lambda x: x["start_date"])
        return items

    def get_entry(self, calendar_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve single calendar entry by ID."""
        if self._dynamodb_table:
            try:
                resp = self._dynamodb_table.get_item(Key={"calendar_id": calendar_id})
                item = resp.get("Item")
                if item:
                    self._cache[calendar_id] = item
                    return item
            except Exception as e:
                print(f"[AcademicCalendar] DynamoDB get_entry error: {e}")
        return self._cache.get(calendar_id)

    def create_entry(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Admin: Create a new academic calendar entry."""
        self.validate_entry_payload(data, is_update=False)

        # Generate human-readable calendar ID or UUID
        year_short = data["academic_year"].split("-")[0]
        sem_code = data["semester"][0].upper()
        etype_code = data["exam_type"].replace("-", "").upper()
        cid = data.get("calendar_id") or f"CAL-{year_short}{sem_code}-{etype_code}"
        if cid in self._cache:
            cid = f"{cid}-{uuid.uuid4().hex[:6]}"

        now_iso = self.get_current_datetime().isoformat()
        entry = {
            "calendar_id": cid,
            "academic_year": str(data["academic_year"]).strip(),
            "semester": str(data["semester"]).strip(),
            "exam_type": str(data["exam_type"]).strip(),
            "start_date": str(data["start_date"]).strip(),
            "end_date": str(data["end_date"]).strip(),
            "status": str(data.get("status", "scheduled")).strip(),
            "notes": str(data.get("notes", "")).strip(),
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        if self._dynamodb_table:
            self._dynamodb_table.put_item(Item=entry)

        self._cache[cid] = entry
        self._save()
        return entry

    def update_entry(self, calendar_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Admin: Update/correct dates and status of an existing entry."""
        existing = self.get_entry(calendar_id)
        if not existing:
            raise KeyError(f"Calendar entry '{calendar_id}' not found.")

        existing = dict(existing)
        merged = {**existing, **data}
        self.validate_entry_payload(merged, is_update=True, existing_id=calendar_id)

        merged["updated_at"] = self.get_current_datetime().isoformat()

        if self._dynamodb_table:
            self._dynamodb_table.put_item(Item=merged)

        self._cache[calendar_id] = merged
        self._save()
        return merged

    def deactivate_entry(self, calendar_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        """Admin: Deactivate/cancel an exam entry."""
        existing = self.get_entry(calendar_id)
        if not existing:
            raise KeyError(f"Calendar entry '{calendar_id}' not found.")

        entry = dict(existing)
        entry["status"] = "cancelled"
        if reason:
            entry["notes"] = f"{entry.get('notes', '')} [Cancelled: {reason}]".strip()
        entry["updated_at"] = self.get_current_datetime().isoformat()

        if self._dynamodb_table:
            self._dynamodb_table.put_item(Item=entry)

        self._cache[calendar_id] = entry
        self._save()
        return entry

    # -------------------------------------------------------------
    # Automatic Countdown & Upcoming Exam Engine
    # -------------------------------------------------------------
    def get_next_applicable_exam(self, as_of_date: Optional[date] = None) -> Dict[str, Any]:
        """
        Determines the next applicable scheduled exam relative to as_of_date
        (defaults to current date in Asia/Kolkata).

        Returns:
            Dict containing:
            - status: 'UPCOMING' | 'IN_PROGRESS' | 'NO_EXAM_SCHEDULED'
            - days_remaining: int or None
            - exam_type: 'CAT-1' | 'CAT-2' | 'FAT'
            - exam_type_model: 'CAT1' | 'CAT2' | 'FAT'
            - start_date, end_date
            - label: Plain-English countdown status
        """
        today = as_of_date or self.get_current_date()

        # Find all scheduled exams where end_date >= today
        candidates = []
        entries_to_check = self.list_entries(status="scheduled") if self._dynamodb_table else self._cache.values()
        for item in entries_to_check:
            if item.get("status") != "scheduled":
                continue

            try:
                start_d = datetime.strptime(item["start_date"], "%Y-%m-%d").date()
                end_d = datetime.strptime(item["end_date"], "%Y-%m-%d").date()
            except Exception:
                continue

            if end_d >= today:
                candidates.append((start_d, end_d, item))

        if not candidates:
            return {
                "status": "NO_EXAM_SCHEDULED",
                "days_remaining": None,
                "exam_type": None,
                "exam_type_model": "CAT1",  # Neutral baseline
                "start_date": None,
                "end_date": None,
                "label": "No upcoming exam scheduled",
                "explanation": "No scheduled exams found on the academic calendar. Using standard non-exam baseline demand.",
                "as_of_date": today.strftime("%Y-%m-%d"),
                "timezone": self.tz_name,
                "entry": None,
            }

        # Sort candidates chronologically by start_date
        candidates.sort(key=lambda x: x[0])

        # Check if first candidate is already in progress
        first_start, first_end, first_item = candidates[0]

        if first_start <= today <= first_end:
            etype = first_item["exam_type"]
            etype_model = MODEL_EXAM_TYPE_MAP.get(etype, "CAT2")
            return {
                "status": "IN_PROGRESS",
                "days_remaining": 0,
                "exam_type": etype,
                "exam_type_model": etype_model,
                "start_date": first_item["start_date"],
                "end_date": first_item["end_date"],
                "label": f"{etype} in progress (ends {first_item['end_date']})",
                "explanation": f"{etype} examination is currently active. Library checkouts and return rushes are peak.",
                "as_of_date": today.strftime("%Y-%m-%d"),
                "timezone": self.tz_name,
                "entry": first_item,
            }

        # Otherwise, upcoming exam
        days_rem = (first_start - today).days
        etype = first_item["exam_type"]
        etype_model = MODEL_EXAM_TYPE_MAP.get(etype, "CAT2")

        return {
            "status": "UPCOMING",
            "days_remaining": days_rem,
            "exam_type": etype,
            "exam_type_model": etype_model,
            "start_date": first_item["start_date"],
            "end_date": first_item["end_date"],
            "label": f"{days_rem} days remaining until {etype} ({first_item['start_date']})",
            "explanation": f"Next scheduled exam is {etype} ({first_item['semester']} {first_item['academic_year']}) in {days_rem} calendar days.",
            "as_of_date": today.strftime("%Y-%m-%d"),
            "timezone": self.tz_name,
            "entry": first_item,
        }
