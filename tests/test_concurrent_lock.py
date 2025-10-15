"""
✅ P0-1: Unit tests for distributed lock and atomic message operations

Tests verify that:
1. Distributed locks prevent concurrent absorption by multiple pods
2. Atomic pop operations avoid race conditions
3. User isolation is maintained across concurrent operations
"""

import pytest
import threading
import time
from unittest.mock import Mock, patch
from mirix.agent.redis_message_store import (
    acquire_user_lock,
    release_user_lock,
    atomic_pop_messages,
    add_message_to_redis,
    get_message_count_from_redis,
    check_user_lock_exists,
)


class TestDistributedLock:
    """Test distributed lock acquisition and release."""
    
    def test_acquire_lock_success(self):
        """Test successful lock acquisition."""
        user_id = "test_user_1"
        
        # Clean up any existing lock
        release_user_lock(user_id)
        
        # First acquisition should succeed
        assert acquire_user_lock(user_id, timeout=10) is True
        
        # Lock should exist
        assert check_user_lock_exists(user_id) is True
        
        # Clean up
        release_user_lock(user_id)
        assert check_user_lock_exists(user_id) is False
    
    def test_acquire_lock_already_locked(self):
        """Test lock acquisition fails when already locked."""
        user_id = "test_user_2"
        
        # Clean up
        release_user_lock(user_id)
        
        # First acquisition succeeds
        assert acquire_user_lock(user_id, timeout=10) is True
        
        # Second acquisition should fail (already locked)
        assert acquire_user_lock(user_id, timeout=10) is False
        
        # Clean up
        release_user_lock(user_id)
    
    def test_lock_expires_after_timeout(self):
        """Test lock expires after timeout."""
        user_id = "test_user_3"
        
        # Clean up
        release_user_lock(user_id)
        
        # Acquire lock with 1 second timeout
        assert acquire_user_lock(user_id, timeout=1) is True
        
        # Wait for lock to expire
        time.sleep(2)
        
        # Should be able to acquire lock again
        assert acquire_user_lock(user_id, timeout=10) is True
        
        # Clean up
        release_user_lock(user_id)
    
    def test_acquire_lock_none_user_id(self):
        """Test lock acquisition rejects None user_id."""
        with pytest.raises(ValueError, match="user_id is required"):
            acquire_user_lock(None, timeout=10)
    
    def test_release_lock_none_user_id(self):
        """Test lock release rejects None user_id."""
        with pytest.raises(ValueError, match="user_id is required"):
            release_user_lock(None)


class TestAtomicPopMessages:
    """Test atomic message pop operations."""
    
    def test_atomic_pop_empty_queue(self):
        """Test popping from empty queue returns empty list."""
        user_id = "test_user_empty"
        
        # Ensure queue is empty
        result = atomic_pop_messages(user_id, 10)
        assert result == []
    
    def test_atomic_pop_basic(self):
        """Test basic atomic pop functionality."""
        user_id = "test_user_atomic_basic"
        
        # Add test messages
        for i in range(5):
            add_message_to_redis(
                user_id,
                f"2025-01-01 00:00:{i:02d}",
                {"message": f"msg_{i}", "image_uris": [], "sources": None}
            )
        
        # Pop 3 messages
        result = atomic_pop_messages(user_id, 3)
        
        # Should get 3 messages
        assert len(result) == 3
        
        # Verify messages are correct
        for i, (timestamp, data) in enumerate(result):
            assert data["message"] == f"msg_{i}"
        
        # Should have 2 messages remaining
        remaining = get_message_count_from_redis(user_id)
        assert remaining == 2
        
        # Clean up
        atomic_pop_messages(user_id, 10)
    
    def test_atomic_pop_more_than_available(self):
        """Test popping more messages than available."""
        user_id = "test_user_atomic_overflow"
        
        # Add 3 messages
        for i in range(3):
            add_message_to_redis(
                user_id,
                f"2025-01-01 00:00:{i:02d}",
                {"message": f"msg_{i}", "image_uris": [], "sources": None}
            )
        
        # Try to pop 10 messages
        result = atomic_pop_messages(user_id, 10)
        
        # Should get all 3 messages
        assert len(result) == 3
        
        # Should have 0 messages remaining
        remaining = get_message_count_from_redis(user_id)
        assert remaining == 0
    
    def test_atomic_pop_none_user_id(self):
        """Test atomic pop rejects None user_id."""
        with pytest.raises(ValueError, match="user_id is required"):
            atomic_pop_messages(None, 5)


class TestConcurrentAbsorption:
    """Test concurrent absorption scenarios with distributed locks."""
    
    def test_concurrent_lock_acquisition(self):
        """Test that only one thread can acquire the lock."""
        user_id = "test_user_concurrent"
        release_user_lock(user_id)
        
        results = {"thread1": None, "thread2": None}
        
        def try_acquire(thread_name):
            results[thread_name] = acquire_user_lock(user_id, timeout=10)
        
        # Start two threads trying to acquire lock simultaneously
        t1 = threading.Thread(target=try_acquire, args=("thread1",))
        t2 = threading.Thread(target=try_acquire, args=("thread2",))
        
        t1.start()
        t2.start()
        
        t1.join()
        t2.join()
        
        # Exactly one thread should succeed
        success_count = sum(1 for v in results.values() if v is True)
        assert success_count == 1
        
        # Clean up
        release_user_lock(user_id)
    
    def test_concurrent_pop_operations(self):
        """Test that atomic pop prevents race conditions."""
        user_id = "test_user_concurrent_pop"
        
        # Add 10 messages
        for i in range(10):
            add_message_to_redis(
                user_id,
                f"2025-01-01 00:00:{i:02d}",
                {"message": f"msg_{i}", "image_uris": [], "sources": None}
            )
        
        results = []
        
        def pop_messages(count):
            popped = atomic_pop_messages(user_id, count)
            results.append(len(popped))
        
        # Two threads try to pop 5 messages each
        t1 = threading.Thread(target=pop_messages, args=(5,))
        t2 = threading.Thread(target=pop_messages, args=(5,))
        
        t1.start()
        t2.start()
        
        t1.join()
        t2.join()
        
        # Total popped should be 10
        assert sum(results) == 10
        
        # Queue should be empty
        remaining = get_message_count_from_redis(user_id)
        assert remaining == 0
    
    def test_user_isolation_with_locks(self):
        """Test that locks are user-specific."""
        user1 = "test_user_isolation_1"
        user2 = "test_user_isolation_2"
        
        # Clean up
        release_user_lock(user1)
        release_user_lock(user2)
        
        # User 1 acquires lock
        assert acquire_user_lock(user1, timeout=10) is True
        
        # User 2 should still be able to acquire their lock
        assert acquire_user_lock(user2, timeout=10) is True
        
        # Both should have locks
        assert check_user_lock_exists(user1) is True
        assert check_user_lock_exists(user2) is True
        
        # Clean up
        release_user_lock(user1)
        release_user_lock(user2)


class TestEndToEndScenario:
    """End-to-end test simulating multi-pod absorption scenario."""
    
    def test_multi_pod_absorption_simulation(self):
        """Simulate multiple pods trying to absorb same user's messages."""
        user_id = "test_user_multipod"
        
        # Clean up
        release_user_lock(user_id)
        atomic_pop_messages(user_id, 1000)  # Clear all messages
        
        # Add 20 messages
        for i in range(20):
            add_message_to_redis(
                user_id,
                f"2025-01-01 00:00:{i:02d}",
                {"message": f"msg_{i}", "image_uris": [], "sources": None}
            )
        
        processed_counts = []
        
        def simulate_pod_absorption():
            """Simulate a pod attempting to absorb messages."""
            # Try to acquire lock (simulating absorb_content_into_memory)
            if acquire_user_lock(user_id, timeout=5):
                try:
                    # Check if should absorb (threshold check)
                    count = get_message_count_from_redis(user_id)
                    if count >= 10:
                        # Atomically pop messages
                        messages = atomic_pop_messages(user_id, 10)
                        processed_counts.append(len(messages))
                        
                        # Simulate processing time
                        time.sleep(0.1)
                finally:
                    release_user_lock(user_id)
            else:
                # Another pod is processing, skip
                processed_counts.append(0)
        
        # Simulate 3 pods trying to absorb simultaneously
        threads = []
        for _ in range(3):
            t = threading.Thread(target=simulate_pod_absorption)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # Only one pod should have processed messages
        successful_pods = [c for c in processed_counts if c > 0]
        assert len(successful_pods) == 1
        assert successful_pods[0] == 10
        
        # 10 messages should remain
        remaining = get_message_count_from_redis(user_id)
        assert remaining == 10
        
        # Clean up
        atomic_pop_messages(user_id, 1000)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

