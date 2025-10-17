"""
✅ TASK 5: Unit tests for Redis message store

Tests cover:
1. Redis basic operations (add/get/remove/count)
2. User isolation (multi-user scenarios)
3. Serialization (pending/google_cloud_file/local_file)
4. Integration (multi-pod scenarios)
"""

import pytest
import threading

from mirix.agent.redis_message_store import (
    add_message_to_redis,
    get_messages_from_redis,
    remove_messages_from_redis,
    get_message_count_from_redis,
    get_redis_client,
)


@pytest.fixture
def clean_redis():
    """Cleanup fixture to ensure test independence."""
    client = get_redis_client()
    
    # Clean up test data before test
    for key in client.scan_iter("mirix:temp_messages:test*"):
        client.delete(key)
    
    yield
    
    # Clean up test data after test
    for key in client.scan_iter("mirix:temp_messages:test*"):
        client.delete(key)


class TestRedisBasicOperations:
    """Test basic Redis operations (add/get/remove/count)."""
    
    def test_add_and_get_messages(self, clean_redis):
        """Test adding and retrieving messages."""
        add_message_to_redis("test_user1", "2024-01-01 10:00", {"message": "test1"})
        messages = get_messages_from_redis("test_user1")
        
        assert len(messages) == 1
        assert messages[0][0] == "2024-01-01 10:00"
        assert messages[0][1]["message"] == "test1"
    
    def test_remove_messages(self, clean_redis):
        """Test removing messages from the head."""
        # Add 5 messages
        for i in range(5):
            add_message_to_redis(
                "test_user1",
                f"2024-01-01 10:00:0{i}",
                {"message": f"test{i}"},
            )
        
        # Remove first 2 messages
        remove_messages_from_redis("test_user1", 2)
        messages = get_messages_from_redis("test_user1")
        
        # Should have 3 messages left (test2, test3, test4)
        assert len(messages) == 3
        assert messages[0][1]["message"] == "test2"
        assert messages[1][1]["message"] == "test3"
        assert messages[2][1]["message"] == "test4"
    
    def test_message_count(self, clean_redis):
        """Test message count."""
        for i in range(3):
            add_message_to_redis(
                "test_user1",
                f"2024-01-01 10:00:0{i}",
                {"message": f"test{i}"},
            )
        
        count = get_message_count_from_redis("test_user1")
        assert count == 3
    
    def test_message_order(self, clean_redis):
        """Test FIFO order (First In First Out)."""
        for i in range(3):
            add_message_to_redis(
                "test_user1",
                f"2024-01-01 10:00:0{i}",
                {"message": f"test{i}"},
            )
        
        messages = get_messages_from_redis("test_user1")
        message_order = [msg[1]["message"] for msg in messages]
        
        # Should maintain insertion order
        assert message_order == ["test0", "test1", "test2"]
    
    def test_get_messages_with_limit(self, clean_redis):
        """Test getting messages with limit."""
        # Add 5 messages
        for i in range(5):
            add_message_to_redis(
                "test_user1",
                f"2024-01-01 10:00:0{i}",
                {"message": f"test{i}"},
            )
        
        # Get only first 3 messages
        messages = get_messages_from_redis("test_user1", limit=3)
        assert len(messages) == 3
        assert [msg[1]["message"] for msg in messages] == ["test0", "test1", "test2"]


class TestUserIsolation:
    """Test user isolation (different users' messages should not interfere)."""
    
    def test_user_isolation(self, clean_redis):
        """Test that different users' messages are completely isolated."""
        add_message_to_redis("test_user1", "2024-01-01 10:00", {"message": "user1_msg"})
        add_message_to_redis("test_user2", "2024-01-01 10:00", {"message": "user2_msg"})
        
        user1_msgs = get_messages_from_redis("test_user1")
        user2_msgs = get_messages_from_redis("test_user2")
        
        # Each user should have exactly 1 message
        assert len(user1_msgs) == 1
        assert len(user2_msgs) == 1
        
        # Messages should be isolated
        assert user1_msgs[0][1]["message"] == "user1_msg"
        assert user2_msgs[0][1]["message"] == "user2_msg"
    
    def test_multiple_users_concurrent(self, clean_redis):
        """Test concurrent message additions by multiple users."""
        
        def add_messages(user_id, count):
            for i in range(count):
                add_message_to_redis(
                    user_id,
                    f"2024-01-01 10:00:0{i}",
                    {"message": f"{user_id}_msg{i}"},
                )
        
        # Create 3 threads for 3 users
        threads = [
            threading.Thread(target=add_messages, args=(f"test_user{i}", 10))
            for i in range(3)
        ]
        
        # Start all threads
        for t in threads:
            t.start()
        
        # Wait for all threads to complete
        for t in threads:
            t.join()
        
        # Verify each user has exactly 10 messages
        for i in range(3):
            count = get_message_count_from_redis(f"test_user{i}")
            assert count == 10
    
    def test_user_removal_isolation(self, clean_redis):
        """Test that removing one user's messages doesn't affect others."""
        # Add messages for two users
        for i in range(3):
            add_message_to_redis("test_user1", f"2024-01-01 10:00:0{i}", {"message": f"user1_{i}"})
            add_message_to_redis("test_user2", f"2024-01-01 10:00:0{i}", {"message": f"user2_{i}"})
        
        # Remove all messages from user1
        remove_messages_from_redis("test_user1", 3)
        
        # User1 should have 0 messages, user2 should still have 3
        assert get_message_count_from_redis("test_user1") == 0
        assert get_message_count_from_redis("test_user2") == 3


class TestSerialization:
    """Test serialization of different message types."""
    
    def test_serialize_pending_upload(self, clean_redis):
        """Test serialization of pending upload placeholders."""
        message_data = {
            "image_uris": [
                {
                    "upload_uuid": "uuid123",
                    "filename": "test.jpg",
                    "pending": True,
                }
            ],
            "message": "test",
        }
        
        add_message_to_redis("test_user1", "2024-01-01 10:00", message_data)
        messages = get_messages_from_redis("test_user1")
        
        # Verify pending placeholder is correctly serialized/deserialized
        image_uri = messages[0][1]["image_uris"][0]
        assert image_uri["pending"] is True
        assert image_uri["upload_uuid"] == "uuid123"
        assert image_uri["filename"] == "test.jpg"
    
    def test_serialize_local_file(self, clean_redis):
        """Test serialization of local file paths."""
        message_data = {
            "image_uris": ["/tmp/test.jpg", "/tmp/test2.png"],
            "message": "test",
        }
        
        add_message_to_redis("test_user1", "2024-01-01 10:00", message_data)
        messages = get_messages_from_redis("test_user1")
        
        # Verify local file paths are correctly serialized/deserialized
        image_uris = messages[0][1]["image_uris"]
        assert image_uris[0] == "/tmp/test.jpg"
        assert image_uris[1] == "/tmp/test2.png"
    
    def test_serialize_google_cloud_file(self, clean_redis):
        """Test serialization of Google Cloud File objects (URI + name)."""
        # Mock Google Cloud File object
        class MockGoogleCloudFile:
            def __init__(self, uri, name):
                self.uri = uri
                self.name = name
        
        google_file = MockGoogleCloudFile("gs://bucket/file.jpg", "file_123.jpg")
        
        message_data = {
            "image_uris": [google_file],
            "message": "test",
        }
        
        add_message_to_redis("test_user1", "2024-01-01 10:00", message_data)
        messages = get_messages_from_redis("test_user1")
        
        # Verify Google Cloud File is serialized as URI dict
        image_uri = messages[0][1]["image_uris"][0]
        assert image_uri["type"] == "google_cloud_file"
        assert image_uri["uri"] == "gs://bucket/file.jpg"
        assert image_uri["name"] == "file_123.jpg"
    
    def test_serialize_mixed_image_types(self, clean_redis):
        """Test serialization of mixed image types."""
        class MockGoogleCloudFile:
            def __init__(self, uri, name):
                self.uri = uri
                self.name = name
        
        message_data = {
            "image_uris": [
                "/tmp/local.jpg",  # Local file
                MockGoogleCloudFile("gs://bucket/cloud.jpg", "cloud_123.jpg"),  # Google Cloud
                {"upload_uuid": "uuid456", "filename": "pending.jpg", "pending": True},  # Pending
            ],
            "message": "test",
        }
        
        add_message_to_redis("test_user1", "2024-01-01 10:00", message_data)
        messages = get_messages_from_redis("test_user1")
        
        image_uris = messages[0][1]["image_uris"]
        
        # Verify local file
        assert image_uris[0] == "/tmp/local.jpg"
        
        # Verify Google Cloud file
        assert image_uris[1]["type"] == "google_cloud_file"
        assert image_uris[1]["uri"] == "gs://bucket/cloud.jpg"
        
        # Verify pending placeholder
        assert image_uris[2]["pending"] is True
        assert image_uris[2]["upload_uuid"] == "uuid456"
    
    def test_serialize_audio_segments(self, clean_redis):
        """Test serialization of audio segments (only count is stored)."""
        message_data = {
            "audio_segments": ["segment1", "segment2", "segment3"],  # Mock segments
            "message": "test",
        }
        
        add_message_to_redis("test_user1", "2024-01-01 10:00", message_data)
        messages = get_messages_from_redis("test_user1")
        
        # Audio data is not stored, should return None
        assert messages[0][1]["audio_segments"] is None
    
    def test_serialize_full_message(self, clean_redis):
        """Test serialization of a complete message with all fields."""
        message_data = {
            "image_uris": ["/tmp/test.jpg"],
            "sources": ["source1", "source2"],
            "audio_segments": ["audio1"],
            "message": "Complete test message",
        }
        
        add_message_to_redis("test_user1", "2024-01-01 10:00:00", message_data)
        messages = get_messages_from_redis("test_user1")
        
        retrieved = messages[0]
        assert retrieved[0] == "2024-01-01 10:00:00"  # Timestamp
        assert retrieved[1]["image_uris"] == ["/tmp/test.jpg"]
        assert retrieved[1]["sources"] == ["source1", "source2"]
        assert retrieved[1]["audio_segments"] is None  # Not stored
        assert retrieved[1]["message"] == "Complete test message"


class TestIntegration:
    """Integration tests for multi-pod scenarios."""
    
    def test_multi_pod_scenario(self, clean_redis):
        """Test multi-pod scenario: Pod 1 writes, Pod 2 reads."""
        # Pod 1 adds a message
        add_message_to_redis("test_user1", "2024-01-01 10:00", {"message": "from_pod1"})
        
        # Pod 2 reads the message (simulated)
        messages = get_messages_from_redis("test_user1")
        
        assert len(messages) == 1
        assert messages[0][1]["message"] == "from_pod1"
    
    def test_multi_pod_concurrent_writes(self, clean_redis):
        """Test multiple pods writing concurrently for the same user."""
        
        def pod_writes(pod_id, user_id, count):
            for i in range(count):
                add_message_to_redis(
                    user_id,
                    f"2024-01-01 10:00:{i}",
                    {"message": f"pod{pod_id}_msg{i}"},
                )
        
        # Simulate 3 pods writing to the same user
        threads = [
            threading.Thread(target=pod_writes, args=(pod_id, "test_user1", 5))
            for pod_id in range(3)
        ]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Should have 15 messages total (3 pods × 5 messages)
        count = get_message_count_from_redis("test_user1")
        assert count == 15
    
    def test_multi_pod_read_after_write(self, clean_redis):
        """Test that Pod 2 can immediately read what Pod 1 wrote."""
        # Pod 1 writes multiple messages
        for i in range(5):
            add_message_to_redis("test_user1", f"2024-01-01 10:00:0{i}", {"message": f"msg{i}"})
        
        # Pod 2 reads all messages
        messages = get_messages_from_redis("test_user1")
        
        # Should get all 5 messages in order
        assert len(messages) == 5
        assert [msg[1]["message"] for msg in messages] == [f"msg{i}" for i in range(5)]
    
    def test_multi_pod_remove_coordination(self, clean_redis):
        """Test that one pod's removal is visible to other pods."""
        # Pod 1 adds 10 messages
        for i in range(10):
            add_message_to_redis("test_user1", f"2024-01-01 10:00:0{i}", {"message": f"msg{i}"})
        
        # Pod 2 processes first 5 messages and removes them
        remove_messages_from_redis("test_user1", 5)
        
        # Pod 3 reads remaining messages
        messages = get_messages_from_redis("test_user1")
        
        # Should have 5 messages left (msg5-msg9)
        assert len(messages) == 5
        assert messages[0][1]["message"] == "msg5"
        assert messages[4][1]["message"] == "msg9"


class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_get_messages_empty_user(self, clean_redis):
        """Test getting messages for a user with no messages."""
        messages = get_messages_from_redis("test_user_nonexistent")
        assert messages == []
    
    def test_count_empty_user(self, clean_redis):
        """Test count for a user with no messages."""
        count = get_message_count_from_redis("test_user_nonexistent")
        assert count == 0
    
    def test_remove_from_empty_list(self, clean_redis):
        """Test removing messages from an empty list (should not error)."""
        # Should not raise an error
        remove_messages_from_redis("test_user_nonexistent", 5)
        count = get_message_count_from_redis("test_user_nonexistent")
        assert count == 0
    
    def test_remove_more_than_exists(self, clean_redis):
        """Test removing more messages than exist."""
        # Add 3 messages
        for i in range(3):
            add_message_to_redis("test_user1", f"2024-01-01 10:00:0{i}", {"message": f"msg{i}"})
        
        # Try to remove 10 messages (more than exist)
        remove_messages_from_redis("test_user1", 10)
        
        # All messages should be removed
        count = get_message_count_from_redis("test_user1")
        assert count == 0
    
    def test_null_values_in_message(self, clean_redis):
        """Test handling of null values in message data."""
        message_data = {
            "image_uris": None,
            "sources": None,
            "audio_segments": None,
            "message": "test",
        }
        
        add_message_to_redis("test_user1", "2024-01-01 10:00", message_data)
        messages = get_messages_from_redis("test_user1")
        
        # Should handle None values gracefully
        assert messages[0][1]["image_uris"] is None
        assert messages[0][1]["sources"] is None
        assert messages[0][1]["audio_segments"] is None
        assert messages[0][1]["message"] == "test"

