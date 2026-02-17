"""
Cloud Sync Module
S3-compatible backup sync for Memories
"""

import os
import logging
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime, timezone

logger = logging.getLogger("memories.cloud-sync")

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    logger.warning("boto3 not installed - cloud sync disabled")


class CloudSync:
    """S3-compatible cloud sync for memory backups"""

    def __init__(
        self,
        bucket: str,
        prefix: str = "memories/",
        region: str = "us-east-1",
        endpoint_url: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
    ):
        if not BOTO3_AVAILABLE:
            raise ImportError("boto3 is required for cloud sync. Install with: pip install boto3")

        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/"
        self.region = region
        self.endpoint_url = endpoint_url

        # Initialize S3 client
        session_kwargs = {}
        if access_key and secret_key:
            session_kwargs = {
                "aws_access_key_id": access_key,
                "aws_secret_access_key": secret_key,
            }

        self.s3 = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            **session_kwargs
        )

        logger.info(
            "CloudSync initialized: bucket=%s prefix=%s region=%s endpoint=%s",
            bucket, prefix, region, endpoint_url or "default"
        )

    def upload_backup(self, backup_path: Path) -> Dict[str, str]:
        """Upload a backup directory to S3"""
        if not backup_path.exists() or not backup_path.is_dir():
            raise FileNotFoundError(f"Backup path not found: {backup_path}")

        backup_name = backup_path.name
        s3_prefix = f"{self.prefix}{backup_name}/"

        uploaded_files = []
        try:
            for file_path in backup_path.glob("*"):
                if file_path.is_file():
                    s3_key = f"{s3_prefix}{file_path.name}"
                    logger.info("Uploading %s -> s3://%s/%s", file_path.name, self.bucket, s3_key)
                    self.s3.upload_file(str(file_path), self.bucket, s3_key)
                    uploaded_files.append(s3_key)

            return {
                "backup_name": backup_name,
                "s3_prefix": s3_prefix,
                "files_uploaded": len(uploaded_files),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except (ClientError, NoCredentialsError) as e:
            logger.exception("Upload failed")
            raise RuntimeError(f"S3 upload failed: {e}")

    def download_backup(self, backup_name: str, dest_dir: Path) -> Dict[str, str]:
        """Download a backup from S3 to local directory"""
        dest_dir.mkdir(parents=True, exist_ok=True)
        backup_dest = dest_dir / backup_name
        backup_dest.mkdir(exist_ok=True)

        s3_prefix = f"{self.prefix}{backup_name}/"

        try:
            # List objects with this prefix
            response = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=s3_prefix)
            if "Contents" not in response:
                raise FileNotFoundError(f"No backup found: {backup_name}")

            downloaded_files = []
            for obj in response["Contents"]:
                s3_key = obj["Key"]
                file_name = s3_key.split("/")[-1]
                if not file_name:  # Skip directory markers
                    continue

                local_path = backup_dest / file_name
                logger.info("Downloading s3://%s/%s -> %s", self.bucket, s3_key, local_path)
                self.s3.download_file(self.bucket, s3_key, str(local_path))
                downloaded_files.append(file_name)

            return {
                "backup_name": backup_name,
                "files_downloaded": len(downloaded_files),
                "local_path": str(backup_dest),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except (ClientError, NoCredentialsError) as e:
            logger.exception("Download failed")
            raise RuntimeError(f"S3 download failed: {e}")

    def list_remote_snapshots(self) -> List[Dict[str, str]]:
        """List all backup snapshots in S3"""
        try:
            response = self.s3.list_objects_v2(
                Bucket=self.bucket,
                Prefix=self.prefix,
                Delimiter="/"
            )

            snapshots = []
            if "CommonPrefixes" in response:
                for prefix in response["CommonPrefixes"]:
                    backup_name = prefix["Prefix"].rstrip("/").split("/")[-1]
                    if backup_name:
                        snapshots.append({
                            "name": backup_name,
                            "s3_prefix": prefix["Prefix"],
                        })

            return sorted(snapshots, key=lambda x: x["name"], reverse=True)
        except (ClientError, NoCredentialsError) as e:
            logger.exception("List snapshots failed")
            raise RuntimeError(f"S3 list failed: {e}")

    def get_latest_snapshot(self) -> Optional[str]:
        """Get the name of the most recent backup in S3"""
        snapshots = self.list_remote_snapshots()
        return snapshots[0]["name"] if snapshots else None

    @classmethod
    def from_env(cls) -> Optional["CloudSync"]:
        """Initialize CloudSync from environment variables"""
        enabled = os.getenv("CLOUD_SYNC_ENABLED", "false").lower() == "true"
        if not enabled:
            logger.info("Cloud sync disabled (CLOUD_SYNC_ENABLED != true)")
            return None

        if not BOTO3_AVAILABLE:
            logger.warning("Cloud sync enabled but boto3 not installed")
            return None

        bucket = os.getenv("CLOUD_SYNC_BUCKET")
        if not bucket:
            logger.error("CLOUD_SYNC_BUCKET not set - cloud sync disabled")
            return None

        return cls(
            bucket=bucket,
            prefix=os.getenv("CLOUD_SYNC_PREFIX", "memories/"),
            region=os.getenv("CLOUD_SYNC_REGION", "us-east-1"),
            endpoint_url=os.getenv("CLOUD_SYNC_ENDPOINT"),
            access_key=os.getenv("CLOUD_SYNC_ACCESS_KEY"),
            secret_key=os.getenv("CLOUD_SYNC_SECRET_KEY"),
        )
