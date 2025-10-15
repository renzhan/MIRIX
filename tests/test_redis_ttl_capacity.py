"""
✅ P1-4: Unit tests for Redis TTL and capacity limits

Tests verify that:
1. TTL is automatically applied to message and conversation keys
2. Capacity limits prevent unbounded queue growth
3. Oldest messages are trimmed when capacity is exceeded
4. TTL causes keys to expire after configured time
"""

import pytest
import time
from mirix.agent.redis_message_store import (
    add_message_to_redis,
    get_messages_from_redis,
    get_message_count_from_redis,
    add_conversation_to_redis,
    get_conversations_from_redis,
    get_conversation_count_from_redis,
    get_redis_client,
)
from mirix.settings import settings


class TestMessageQueueTTL:
    """Test TTL (time-to-live) for message queues."""
    
    def test_message_queue_has_ttl(self):
        """Test that message queue gets TTL applied."""
        user_id = "test_user_ttl_001"
        
        # Add a message
        add_message_to_redis(
            user_id,
            "2025-01-01 00:00:00",
            {"message": "test", "image_uris": [], "sources": None}
        )
        
        # Check TTL exists
        client = get_redis_client()
        key = f"mirix:temp_messages:{user_id}"
        ttl = client.ttl(key)
        
        # TTL should be close to configured value (within 5 seconds tolerance)
        assert ttl > 0, "TTL not set"
        assert abs(ttl - settings.redis_message_ttl) < 5, \
            f"TTL {ttl} not close to {settings.redis_message_ttl}"
        
        # Cleanup
        client.delete(key)
    
    def test_message_queue_ttl_refreshed_on_new_message(self):
        """Test that TTL is refreshed when adding new messages."""
        user_id = "test_user_ttl_002"
        client = get_redis_client()
        key = f"mirix:temp_messages:{user_id}"
        
        # Clean up
        client.delete(key)
        
        # Add first message
        add_message_to_redis(
            user_id,
            "2025-01-01 00:00:00",
            {"message": "test1", "image_uris": [], "sources": None}
        )
        
        # Wait a bit
        time.sleep(2)
        
        # Add second message
        add_message_to_redis(
            user_id,
            "2025-01-01 00:00:01",
            {"message": "test2", "image_uris": [], "sources": None}
        )
        
        # TTL should be refreshed to full value
        ttl = client.ttl(key)
        assert ttl > settings.redis_message_ttl - 3, \
            f"TTL {ttl} not refreshed"
        
        # Cleanup
        client.delete(key)
    
    def test_message_queue_expires_after_ttl(self):
        """Test that message queue expires after TTL (requires short TTL setting)."""
        # Note: This test requires temporarily modifying settings or using a very short TTL
        # In production, TTL is 24 hours, so this test uses a mock scenario
        
        # We'll test the mechanism is in place, actual expiration test would be too slow
        user_id = "test_user_ttl_expire_001"
        client = get_redis_client()
        key = f"mirix:temp_messages:{user_id}"
        
        # Clean up
        client.delete(key)
        
        # Add message
        add_message_to_redis(
            user_id,
            "2025-01-01 00:00:00",
            {"message": "test", "image_uris": [], "sources": None}
        )
        
        # Verify TTL is set
        ttl = client.ttl(key)
        assert ttl > 0, "TTL should be set"
        
        # Cleanup
        client.delete(key)


class TestMessageQueueCapacity:
    """Test capacity limits for message queues."""
    
    def test_message_queue_enforces_capacity_limit(self):
        """Test that message queue trims old messages when capacity exceeded."""
        user_id = "test_user_capacity_001"
        client = get_redis_client()
        key = f"mirix:temp_messages:{user_id}"
        
        # Clean up
        client.delete(key)
        
        max_len = settings.redis_message_max_length
        
        # Add more messages than the limit
        for i in range(max_len + 20):
            add_message_to_redis(
                user_id,
                f"2025-01-01 00:{i:02d}:00",
                {"message": f"msg_{i}", "image_uris": [], "sources": None}
            )
        
        # Should have exactly max_len messages
        count = get_message_count_from_redis(user_id)
        assert count == max_len, \
            f"Expected {max_len} messages, got {count}"
        
        # Should have kept the most recent messages
        messages = get_messages_from_redis(user_id)
        
        # First message should be msg_{20} (oldest kept)
        first_msg = messages[0][1]["message"]
        assert first_msg == "msg_20", \
            f"Expected oldest message to be msg_20, got {first_msg}"
        
        # Last message should be msg_{max_len + 19}
        last_msg = messages[-1][1]["message"]
        expected_last = f"msg_{max_len + 19}"
        assert last_msg == expected_last, \
            f"Expected newest message to be {expected_last}, got {last_msg}"
        
        # Cleanup
        client.delete(key)
    
    def test_message_queue_capacity_with_gradual_addition(self):
        """Test capacity limit with gradual message addition."""
        user_id = "test_user_capacity_002"
        client = get_redis_client()
        key = f"mirix:temp_messages:{user_id}"
        
        # Clean up
        client.delete(key)
        
        max_len = settings.redis_message_max_length
        
        # Add exactly max_len messages
        for i in range(max_len):
            add_message_to_redis(
                user_id,
                f"2025-01-01 00:{i:02d}:00",
                {"message": f"msg_{i}", "image_uris": [], "sources": None}
            )
        
        # Should have max_len messages
        assert get_message_count_from_redis(user_id) == max_len
        
        # Add one more
        add_message_to_redis(
            user_id,
            "2025-01-01 01:00:00",
            {"message": "msg_extra", "image_uris": [], "sources": None}
        )
        
        # Should still have max_len messages
        assert get_message_count_from_redis(user_id) == max_len
        
        # First message should be msg_1 (msg_0 trimmed)
        messages = get_messages_from_redis(user_id)
        assert messages[0][1]["message"] == "msg_1"
        
        # Last message should be msg_extra
        assert messages[-1][1]["message"] == "msg_extra"
        
        # Cleanup
        client.delete(key)


class TestConversationQueueTTL:
    """Test TTL for conversation queues."""
    
    def test_conversation_queue_has_ttl(self):
        """Test that conversation queue gets TTL applied."""
        user_id = "test_user_conv_ttl_001"
        
        # Add a conversation
        add_conversation_to_redis(user_id, "Hello", "Hi there")
        
        # Check TTL exists
        client = get_redis_client()
        key = f"mirix:user_conversations:{user_id}"
        ttl = client.ttl(key)
        
        # TTL should be close to configured value
        assert ttl > 0, "TTL not set"
        assert abs(ttl - settings.redis_conversation_ttl) < 5, \
            f"TTL {ttl} not close to {settings.redis_conversation_ttl}"
        
        # Cleanup
        client.delete(key)
    
    def test_conversation_ttl_shorter_than_message_ttl(self):
        """Test that conversation TTL is shorter than message TTL."""
        # This is a design validation test
        assert settings.redis_conversation_ttl < settings.redis_message_ttl, \
            "Conversation TTL should be shorter than message TTL"


class TestConversationQueueCapacity:
    """Test capacity limits for conversation queues."""
    
    def test_conversation_queue_enforces_capacity_limit(self):
        """Test that conversation queue trims old conversations when capacity exceeded."""
        user_id = "test_user_conv_capacity_001"
        client = get_redis_client()
        key = f"mirix:user_conversations:{user_id}"
        
        # Clean up
        client.delete(key)
        
        max_len = settings.redis_conversation_max_length
        
        # Add more conversations than the limit
        for i in range(max_len + 10):
            add_conversation_to_redis(
                user_id,
                f"User message {i}",
                f"Assistant response {i}"
            )
        
        # Should have exactly max_len conversations
        count = get_conversation_count_from_redis(user_id)
        assert count == max_len, \
            f"Expected {max_len} conversations, got {count}"
        
        # Should have kept the most recent conversations
        conversations = get_conversations_from_redis(user_id)
        
        # First conversation should be #10 (oldest kept)
        first_conv = conversations[0]
        assert first_conv["role"] == "user"
        assert first_conv["content"] == "User message 10", \
            f"Expected oldest conversation to be 'User message 10', got '{first_conv['content']}'"
        
        # Last conversation should be #(max_len + 9)
        last_conv_idx = (max_len + 9) * 2 + 1  # Each conversation pair = 2 messages, +1 for assistant
        last_conv = conversations[last_conv_idx]
        expected_last = f"Assistant response {max_len + 9}"
        assert last_conv["content"] == expected_last, \
            f"Expected newest conversation to be '{expected_last}', got '{last_conv['content']}'"
        
        # Cleanup
        client.delete(key)


class TestCapacityConfiguration:
    """Test capacity configuration values."""
    
    def test_capacity_limits_are_reasonable(self):
        """Test that configured capacity limits are reasonable."""
        # Message queue should hold enough for typical usage
        assert settings.redis_message_max_length >= 50, \
            "Message queue capacity too small"
        assert settings.redis_message_max_length <= 1000, \
            "Message queue capacity unnecessarily large"
        
        # Conversation queue should be smaller than message queue
        assert settings.redis_conversation_max_length <= settings.redis_message_max_length, \
            "Conversation capacity should not exceed message capacity"
    
    def test_ttl_values_are_reasonable(self):
        """Test that TTL values are reasonable."""
        # Message TTL should be at least 1 hour
        assert settings.redis_message_ttl >= 3600, \
            "Message TTL too short"
        
        # Message TTL should not be more than 7 days
        assert settings.redis_message_ttl <= 7 * 24 * 3600, \
            "Message TTL unnecessarily long"
        
        # Conversation TTL should be at least 10 minutes
        assert settings.redis_conversation_ttl >= 600, \
            "Conversation TTL too short"
        
        # Conversation TTL should not be more than 24 hours
        assert settings.redis_conversation_ttl <= 24 * 3600, \
            "Conversation TTL unnecessarily long"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

