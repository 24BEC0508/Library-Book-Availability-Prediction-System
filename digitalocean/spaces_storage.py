"""
DigitalOcean Spaces Object Storage Integration.

DigitalOcean Spaces provides S3-compatible object storage.
This utility handles uploading, downloading, and snapshotting ML models and live database
state (live_inventory.json, academic_calendar.json, live_loans.json) to/from DO Spaces.

Required Environment Variables:
- DO_SPACES_KEY: Access key for DigitalOcean Spaces
- DO_SPACES_SECRET: Secret key for DigitalOcean Spaces
- DO_SPACES_REGION: Datacenter region (e.g. 'blr1', 'nyc3', 'ams3', 'sgp1')
- DO_SPACES_BUCKET: Spaces bucket name (e.g. 'library-prediction-spaces')
"""

import os
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent

try:
    import boto3
    from botocore.client import Config
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False


class DigitalOceanSpacesManager:
    def __init__(
        self,
        bucket: Optional[str] = None,
        region: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
    ):
        if not HAS_BOTO3:
            raise ImportError("boto3 is required for DigitalOcean Spaces integration. Run: pip install boto3")

        self.bucket = bucket or os.environ.get("DO_SPACES_BUCKET", "library-ml-storage")
        self.region = region or os.environ.get("DO_SPACES_REGION", "blr1")
        self.access_key = access_key or os.environ.get("DO_SPACES_KEY")
        self.secret_key = secret_key or os.environ.get("DO_SPACES_SECRET")
        self.endpoint_url = f"https://{self.region}.digitaloceanspaces.com"

        if not self.access_key or not self.secret_key:
            raise ValueError("DO_SPACES_KEY and DO_SPACES_SECRET environment variables must be set.")

        session = boto3.session.Session()
        self.client = session.client(
            "s3",
            region_name=self.region,
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=Config(s3={"addressing_style": "virtual"}),
        )

    def upload_file(self, local_path: Path, remote_key: str) -> bool:
        """Upload a local file to DigitalOcean Spaces."""
        try:
            print(f"[DO Spaces] Uploading {local_path} -> s3://{self.bucket}/{remote_key}")
            self.client.upload_file(str(local_path), self.bucket, remote_key)
            print(f"[DO Spaces] Upload successful: {remote_key}")
            return True
        except Exception as e:
            print(f"[DO Spaces] Upload failed: {e}")
            return False

    def download_file(self, remote_key: str, local_path: Path) -> bool:
        """Download an object from DigitalOcean Spaces to local disk."""
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"[DO Spaces] Downloading s3://{self.bucket}/{remote_key} -> {local_path}")
            self.client.download_file(self.bucket, remote_key, str(local_path))
            print(f"[DO Spaces] Download successful.")
            return True
        except Exception as e:
            print(f"[DO Spaces] Download failed: {e}")
            return False

    def backup_operational_data(self) -> None:
        """Backup current operational inventory and calendar JSON files to Spaces."""
        files = [
            (ROOT / "data" / "academic_calendar.json", "backups/academic_calendar.json"),
            (ROOT / "data" / "live_inventory.json", "backups/live_inventory.json"),
            (ROOT / "data" / "live_loans.json", "backups/live_loans.json"),
            (ROOT / "data" / "live_transactions.json", "backups/live_transactions.json"),
            (ROOT / "model" / "regression_model.npz", "model/regression_model.npz"),
        ]
        for local_file, remote_key in files:
            if local_file.exists():
                self.upload_file(local_file, remote_key)


if __name__ == "__main__":
    if not HAS_BOTO3:
        print("[ERROR] boto3 is not installed. Install via: pip install boto3")
        sys.exit(1)

    print("DigitalOcean Spaces Backup Utility")
    if not os.environ.get("DO_SPACES_KEY"):
        print("Please set DO_SPACES_KEY and DO_SPACES_SECRET to run backup.")
        sys.exit(1)

    mgr = DigitalOceanSpacesManager()
    mgr.backup_operational_data()
