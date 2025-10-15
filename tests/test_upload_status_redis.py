"""
✅ P0-2: Unit tests for upload status management in Redis

Tests verify that:
1. Upload status is correctly stored and retrieved from Redis
2. Cross-pod visibility works (any pod can check any upload)
3. Status transitions are properly tracked (pending -> completed/failed)
4. Cleanup removes Redis entries
5. Google Cloud file references are properly serialized/deserialized
"""

import pytest
import time
from unittest.mock import Mock
from mirix.agent.redis_message_store import (
    set_upload_status,
    get_upload_status,
    delete_upload_status,
    get_all_upload_statuses,
)


class TestUploadStatusBasic:
    """Test basic upload status CRUD operations."""
    
    def test_set_and_get_pending_status(self):
        """Test setting and getting pending upload status."""
        upload_id = "test_upload_pending_001"
        
        # Set pending status
        set_upload_status(
            upload_id,
            status='pending',
            result=None,
            filename='test_image.png'
        )
        
        # Get status
        status_info = get_upload_status(upload_id)
        
        assert status_info['status'] == 'pending'
        assert status_info['result'] is None
        assert status_info['filename'] == 'test_image.png'
        
        # Cleanup
        delete_upload_status(upload_id)
    
    def test_set_and_get_completed_status_with_file_ref(self):
        """Test completed status with Google Cloud file reference."""
        upload_id = "test_upload_completed_001"
        
        # Create mock Google Cloud file reference
        mock_file_ref = Mock()
        mock_file_ref.uri = "https://storage.googleapis.com/test-bucket/file123"
        mock_file_ref.name = "file123.png"
        mock_file_ref.create_time = "2025-01-01T00:00:00Z"
        
        # Set completed status
        set_upload_status(
            upload_id,
            status='completed',
            result=mock_file_ref,
            filename='test_image.png'
        )
        
        # Get status
        status_info = get_upload_status(upload_id)
        
        assert status_info['status'] == 'completed'
        assert status_info['result'] is not None
        assert status_info['result']['uri'] == mock_file_ref.uri
        assert status_info['result']['name'] == mock_file_ref.name
        assert status_info['result']['create_time'] == mock_file_ref.create_time
        
        # Cleanup
        delete_upload_status(upload_id)
    
    def test_set_and_get_failed_status(self):
        """Test failed upload status."""
        upload_id = "test_upload_failed_001"
        
        # Set failed status
        set_upload_status(
            upload_id,
            status='failed',
            result=None,
            filename='test_image.png'
        )
        
        # Get status
        status_info = get_upload_status(upload_id)
        
        assert status_info['status'] == 'failed'
        assert status_info['result'] is None
        
        # Cleanup
        delete_upload_status(upload_id)
    
    def test_get_nonexistent_upload(self):
        """Test getting status of non-existent upload returns unknown."""
        upload_id = "test_upload_nonexistent_999"
        
        status_info = get_upload_status(upload_id)
        
        assert status_info['status'] == 'unknown'
        assert status_info['result'] is None
    
    def test_delete_upload_status(self):
        """Test deleting upload status."""
        upload_id = "test_upload_delete_001"
        
        # Create status
        set_upload_status(upload_id, status='pending', filename='test.png')
        
        # Verify it exists
        status_info = get_upload_status(upload_id)
        assert status_info['status'] == 'pending'
        
        # Delete
        delete_upload_status(upload_id)
        
        # Verify it's gone
        status_info = get_upload_status(upload_id)
        assert status_info['status'] == 'unknown'
    
    def test_none_upload_id_validation(self):
        """Test that None upload_id raises ValueError."""
        with pytest.raises(ValueError, match="upload_id is required"):
            set_upload_status(None, status='pending')
        
        with pytest.raises(ValueError, match="upload_id is required"):
            get_upload_status(None)
        
        with pytest.raises(ValueError, match="upload_id is required"):
            delete_upload_status(None)


class TestUploadStatusTransitions:
    """Test status transition scenarios."""
    
    def test_pending_to_completed_transition(self):
        """Test normal upload flow: pending -> completed."""
        upload_id = "test_upload_transition_001"
        
        # Start as pending
        set_upload_status(upload_id, status='pending', filename='test.png')
        status = get_upload_status(upload_id)
        assert status['status'] == 'pending'
        
        # Update to completed
        mock_file_ref = Mock()
        mock_file_ref.uri = "https://test.com/file.png"
        mock_file_ref.name = "file.png"
        
        set_upload_status(
            upload_id,
            status='completed',
            result=mock_file_ref,
            filename='test.png'
        )
        
        status = get_upload_status(upload_id)
        assert status['status'] == 'completed'
        assert status['result']['uri'] == mock_file_ref.uri
        
        # Cleanup
        delete_upload_status(upload_id)
    
    def test_pending_to_failed_transition(self):
        """Test upload failure: pending -> failed."""
        upload_id = "test_upload_transition_002"
        
        # Start as pending
        set_upload_status(upload_id, status='pending', filename='test.png')
        
        # Update to failed
        set_upload_status(upload_id, status='failed', result=None, filename='test.png')
        
        status = get_upload_status(upload_id)
        assert status['status'] == 'failed'
        assert status['result'] is None
        
        # Cleanup
        delete_upload_status(upload_id)
    
    def test_status_overwrite(self):
        """Test that setting status overwrites previous value."""
        upload_id = "test_upload_overwrite_001"
        
        # Set initial status
        set_upload_status(upload_id, status='pending', filename='test.png')
        assert get_upload_status(upload_id)['status'] == 'pending'
        
        # Overwrite with different status
        set_upload_status(upload_id, status='failed', filename='test.png')
        assert get_upload_status(upload_id)['status'] == 'failed'
        
        # Cleanup
        delete_upload_status(upload_id)


class TestCrossPodVisibility:
    """Test cross-pod upload status visibility."""
    
    def test_pod_a_creates_pod_b_reads(self):
        """Simulate Pod-A creating upload, Pod-B reading status."""
        upload_id = "test_crosspod_001"
        
        # Pod-A: Create upload
        mock_file_ref = Mock()
        mock_file_ref.uri = "https://test.com/file.png"
        mock_file_ref.name = "file.png"
        
        set_upload_status(
            upload_id,
            status='completed',
            result=mock_file_ref,
            filename='test.png'
        )
        
        # Pod-B: Read status (same Redis, different process)
        status = get_upload_status(upload_id)
        
        assert status['status'] == 'completed'
        assert status['result']['uri'] == mock_file_ref.uri
        
        # Cleanup
        delete_upload_status(upload_id)
    
    def test_multiple_concurrent_uploads(self):
        """Test multiple uploads tracked simultaneously."""
        upload_ids = [f"test_concurrent_{i}" for i in range(5)]
        
        # Create multiple uploads
        for i, upload_id in enumerate(upload_ids):
            set_upload_status(
                upload_id,
                status='pending',
                filename=f'test_{i}.png'
            )
        
        # Verify all exist
        for upload_id in upload_ids:
            status = get_upload_status(upload_id)
            assert status['status'] == 'pending'
        
        # Update some to completed
        for i in [0, 2, 4]:
            mock_file_ref = Mock()
            mock_file_ref.uri = f"https://test.com/file_{i}.png"
            mock_file_ref.name = f"file_{i}.png"
            
            set_upload_status(
                upload_ids[i],
                status='completed',
                result=mock_file_ref,
                filename=f'test_{i}.png'
            )
        
        # Verify statuses
        assert get_upload_status(upload_ids[0])['status'] == 'completed'
        assert get_upload_status(upload_ids[1])['status'] == 'pending'
        assert get_upload_status(upload_ids[2])['status'] == 'completed'
        
        # Cleanup
        for upload_id in upload_ids:
            delete_upload_status(upload_id)


class TestUploadStatusTTL:
    """Test TTL (time-to-live) functionality."""
    
    def test_status_expires_after_ttl(self):
        """Test that status expires after specified TTL."""
        upload_id = "test_ttl_001"
        
        # Set status with 1 second TTL
        set_upload_status(
            upload_id,
            status='pending',
            filename='test.png',
            ttl=1
        )
        
        # Immediately should exist
        status = get_upload_status(upload_id)
        assert status['status'] == 'pending'
        
        # Wait for expiration
        time.sleep(2)
        
        # Should be gone
        status = get_upload_status(upload_id)
        assert status['status'] == 'unknown'
    
    def test_default_ttl(self):
        """Test default TTL is reasonable (not expired immediately)."""
        upload_id = "test_ttl_default_001"
        
        # Set with default TTL (3600 seconds / 1 hour)
        set_upload_status(
            upload_id,
            status='pending',
            filename='test.png'
        )
        
        # Should still exist after a few seconds
        time.sleep(2)
        status = get_upload_status(upload_id)
        assert status['status'] == 'pending'
        
        # Cleanup
        delete_upload_status(upload_id)


class TestGetAllUploadStatuses:
    """Test monitoring/debugging functionality."""
    
    def test_get_all_statuses(self):
        """Test getting all upload statuses."""
        upload_ids = [f"test_all_{i}" for i in range(3)]
        
        # Create multiple uploads
        for upload_id in upload_ids:
            set_upload_status(
                upload_id,
                status='pending',
                filename=f'{upload_id}.png'
            )
        
        # Get all statuses
        all_statuses = get_all_upload_statuses()
        
        # Verify our uploads are present
        for upload_id in upload_ids:
            assert upload_id in all_statuses
            assert all_statuses[upload_id]['status'] == 'pending'
        
        # Cleanup
        for upload_id in upload_ids:
            delete_upload_status(upload_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

