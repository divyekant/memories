"""
Tests for cloud_sync.py - CloudSync S3 integration
Uses unittest.mock to avoid real S3 calls
"""

import sys
import unittest
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# Mock boto3 and botocore before importing cloud_sync
mock_boto3 = MagicMock()
mock_botocore = MagicMock()
mock_botocore.exceptions = MagicMock()
mock_botocore.exceptions.ClientError = Exception
mock_botocore.exceptions.NoCredentialsError = Exception

sys.modules['boto3'] = mock_boto3
sys.modules['botocore'] = mock_botocore
sys.modules['botocore.exceptions'] = mock_botocore.exceptions

import cloud_sync
from cloud_sync import CloudSync

# Force BOTO3_AVAILABLE to True for testing
cloud_sync.BOTO3_AVAILABLE = True


class TestCloudSyncInit(unittest.TestCase):
    """Test CloudSync initialization and config"""

    def test_from_env_disabled(self):
        """When CLOUD_SYNC_ENABLED=false, returns None"""
        with patch.dict(os.environ, {"CLOUD_SYNC_ENABLED": "false"}, clear=True):
            sync = CloudSync.from_env()
            self.assertIsNone(sync)

    def test_from_env_missing_bucket(self):
        """When bucket is missing, returns None"""
        with patch.dict(
            os.environ, {"CLOUD_SYNC_ENABLED": "true"}, clear=True
        ):
            sync = CloudSync.from_env()
            self.assertIsNone(sync)

    def test_from_env_success(self):
        """When properly configured, returns CloudSync instance"""
        env = {
            "CLOUD_SYNC_ENABLED": "true",
            "CLOUD_SYNC_BUCKET": "test-bucket",
            "CLOUD_SYNC_REGION": "us-west-2",
            "CLOUD_SYNC_ACCESS_KEY": "AKIA123",
            "CLOUD_SYNC_SECRET_KEY": "secret123",
            "CLOUD_SYNC_PREFIX": "test/",
        }
        with patch.dict(os.environ, env, clear=True):
            sync = CloudSync.from_env()
            self.assertIsNotNone(sync)
            self.assertEqual(sync.bucket, "test-bucket")
            self.assertEqual(sync.prefix, "test/")

    def test_from_env_default_values(self):
        """Default region and prefix are applied"""
        env = {
            "CLOUD_SYNC_ENABLED": "true",
            "CLOUD_SYNC_BUCKET": "test-bucket",
        }
        with patch.dict(os.environ, env, clear=True):
            sync = CloudSync.from_env()
            self.assertEqual(sync.prefix, "memories/")


class TestCloudSyncOperations(unittest.TestCase):
    """Test CloudSync S3 operations with mocked boto3"""

    def setUp(self):
        """Create CloudSync with mocked S3 client"""
        self.mock_s3 = MagicMock()
        # Use the pre-mocked boto3
        import boto3
        boto3.client = MagicMock(return_value=self.mock_s3)
        
        self.sync = CloudSync(
            bucket="test-bucket",
            region="us-east-1",
            prefix="test/",
            access_key="AKIA123",
            secret_key="secret123",
        )

    def test_list_remote_snapshots_empty(self):
        """list_remote_snapshots returns empty list when no objects"""
        # Mock list_objects_v2 with no CommonPrefixes
        self.mock_s3.list_objects_v2.return_value = {}
        snapshots = self.sync.list_remote_snapshots()
        self.assertEqual(snapshots, [])

    def test_list_remote_snapshots_with_files(self):
        """list_remote_snapshots returns sorted backup names"""
        # Mock list_objects_v2 with CommonPrefixes (folder structure)
        self.mock_s3.list_objects_v2.return_value = {
            "CommonPrefixes": [
                {"Prefix": "test/backup_20260213_120000/"},
                {"Prefix": "test/backup_20260214_120000/"},
            ]
        }
        
        snapshots = self.sync.list_remote_snapshots()
        # Should return sorted dicts with backup info (most recent first)
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0]["name"], "backup_20260214_120000")
        self.assertEqual(snapshots[1]["name"], "backup_20260213_120000")

    def test_get_latest_snapshot(self):
        """get_latest_snapshot returns most recent backup"""
        # Mock list_objects_v2 with CommonPrefixes
        self.mock_s3.list_objects_v2.return_value = {
            "CommonPrefixes": [
                {"Prefix": "test/backup_20260213_120000/"},
                {"Prefix": "test/backup_20260214_120000/"},
            ]
        }
        
        latest = self.sync.get_latest_snapshot()
        self.assertEqual(latest, "backup_20260214_120000")

    def test_get_latest_snapshot_empty(self):
        """get_latest_snapshot returns None when no backups"""
        self.mock_s3.list_objects_v2.return_value = {}
        latest = self.sync.get_latest_snapshot()
        self.assertIsNone(latest)

    def test_upload_backup(self):
        """upload_backup uploads all files in backup directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            backup_path = Path(tmpdir) / "backup_20260214_120000"
            backup_path.mkdir()
            (backup_path / "index.faiss").write_text("faiss data")
            (backup_path / "metadata.json").write_text("meta data")
            (backup_path / "config.json").write_text("config data")

            self.sync.upload_backup(backup_path)

            # Verify upload_file was called for each file
            self.assertEqual(self.mock_s3.upload_file.call_count, 3)
            calls = self.mock_s3.upload_file.call_args_list
            
            # Extract the remote keys from the calls (args are positional)
            # upload_file(Filename, Bucket, Key)
            uploaded_keys = [call.args[2] if len(call.args) > 2 else call.kwargs.get("Key") for call in calls]
            
            # Keys should be in folder structure: prefix/backup_name/filename
            expected_keys = [
                "test/backup_20260214_120000/index.faiss",
                "test/backup_20260214_120000/metadata.json",
                "test/backup_20260214_120000/config.json",
            ]
            self.assertEqual(sorted(uploaded_keys), sorted(expected_keys))

    def test_download_backup(self):
        """download_backup downloads all files for a backup"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dest_dir = Path(tmpdir)

            # Mock list_objects_v2 to return backup files in folder structure
            self.mock_s3.list_objects_v2.return_value = {
                "Contents": [
                    {"Key": "test/backup_20260214_120000/index.faiss"},
                    {"Key": "test/backup_20260214_120000/metadata.json"},
                ]
            }

            result = self.sync.download_backup("backup_20260214_120000", dest_dir)

            # Verify download_file was called for each file
            self.assertEqual(self.mock_s3.download_file.call_count, 2)
            self.assertEqual(result["files_downloaded"], 2)
            self.assertEqual(result["backup_name"], "backup_20260214_120000")


if __name__ == "__main__":
    unittest.main()
