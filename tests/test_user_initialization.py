"""
✅ P1-3: Unit tests for user initialization management

Tests verify that:
1. User initialization is idempotent (happens only once)
2. Distributed locking prevents concurrent initialization
3. Initialization state persists across method calls
4. Multiple users can be initialized independently
"""

import pytest
import time
import threading
from mirix.agent.redis_message_store import (
    is_user_initialized,
    mark_user_initialized,
    reset_user_initialization,
    try_acquire_user_init_lock,
    release_user_init_lock,
)


class TestUserInitializationBasic:
    """Test basic user initialization operations."""
    
    def test_user_not_initialized_initially(self):
        """Test that a new user is not marked as initialized."""
        user_id = "test_user_init_001"
        
        # Clean up
        reset_user_initialization(user_id)
        
        # Should not be initialized
        assert is_user_initialized(user_id) is False
    
    def test_mark_user_initialized(self):
        """Test marking a user as initialized."""
        user_id = "test_user_init_002"
        
        # Clean up
        reset_user_initialization(user_id)
        
        # Mark as initialized
        mark_user_initialized(user_id, ttl=60)
        
        # Should be initialized
        assert is_user_initialized(user_id) is True
        
        # Clean up
        reset_user_initialization(user_id)
    
    def test_mark_user_initialized_is_idempotent(self):
        """Test that marking a user as initialized multiple times is safe."""
        user_id = "test_user_init_003"
        
        # Clean up
        reset_user_initialization(user_id)
        
        # Mark multiple times
        mark_user_initialized(user_id, ttl=60)
        mark_user_initialized(user_id, ttl=60)
        mark_user_initialized(user_id, ttl=60)
        
        # Should still be initialized (no errors)
        assert is_user_initialized(user_id) is True
        
        # Clean up
        reset_user_initialization(user_id)
    
    def test_reset_user_initialization(self):
        """Test resetting user initialization."""
        user_id = "test_user_init_004"
        
        # Mark as initialized
        mark_user_initialized(user_id, ttl=60)
        assert is_user_initialized(user_id) is True
        
        # Reset
        reset_user_initialization(user_id)
        assert is_user_initialized(user_id) is False
    
    def test_none_user_id_validation(self):
        """Test that None user_id raises ValueError."""
        with pytest.raises(ValueError, match="user_id is required"):
            is_user_initialized(None)
        
        with pytest.raises(ValueError, match="user_id is required"):
            mark_user_initialized(None)
        
        with pytest.raises(ValueError, match="user_id is required"):
            reset_user_initialization(None)


class TestUserInitializationLock:
    """Test distributed locking for user initialization."""
    
    def test_acquire_init_lock(self):
        """Test acquiring initialization lock."""
        user_id = "test_user_lock_001"
        
        # Clean up
        release_user_init_lock(user_id)
        
        # First acquisition should succeed
        assert try_acquire_user_init_lock(user_id, timeout=10) is True
        
        # Clean up
        release_user_init_lock(user_id)
    
    def test_acquire_init_lock_already_locked(self):
        """Test that acquiring lock fails when already locked."""
        user_id = "test_user_lock_002"
        
        # Clean up
        release_user_init_lock(user_id)
        
        # First acquisition succeeds
        assert try_acquire_user_init_lock(user_id, timeout=10) is True
        
        # Second acquisition should fail
        assert try_acquire_user_init_lock(user_id, timeout=10) is False
        
        # Clean up
        release_user_init_lock(user_id)
    
    def test_lock_expires_after_timeout(self):
        """Test that lock expires after timeout."""
        user_id = "test_user_lock_003"
        
        # Clean up
        release_user_init_lock(user_id)
        
        # Acquire lock with 1 second timeout
        assert try_acquire_user_init_lock(user_id, timeout=1) is True
        
        # Wait for expiration
        time.sleep(2)
        
        # Should be able to acquire again
        assert try_acquire_user_init_lock(user_id, timeout=10) is True
        
        # Clean up
        release_user_init_lock(user_id)
    
    def test_release_init_lock(self):
        """Test releasing initialization lock."""
        user_id = "test_user_lock_004"
        
        # Acquire lock
        assert try_acquire_user_init_lock(user_id, timeout=10) is True
        
        # Release
        release_user_init_lock(user_id)
        
        # Should be able to acquire again
        assert try_acquire_user_init_lock(user_id, timeout=10) is True
        
        # Clean up
        release_user_init_lock(user_id)
    
    def test_none_user_id_validation_lock(self):
        """Test that None user_id raises ValueError for lock operations."""
        with pytest.raises(ValueError, match="user_id is required"):
            try_acquire_user_init_lock(None)
        
        with pytest.raises(ValueError, match="user_id is required"):
            release_user_init_lock(None)


class TestUserInitializationConcurrency:
    """Test concurrent initialization scenarios."""
    
    def test_concurrent_lock_acquisition(self):
        """Test that only one thread can acquire the lock."""
        user_id = "test_user_concurrent_001"
        release_user_init_lock(user_id)
        
        results = {"thread1": None, "thread2": None}
        
        def try_acquire(thread_name):
            results[thread_name] = try_acquire_user_init_lock(user_id, timeout=10)
        
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
        release_user_init_lock(user_id)
    
    def test_simulated_multi_pod_initialization(self):
        """Simulate multiple pods trying to initialize same user."""
        user_id = "test_user_multipod_001"
        
        # Clean up
        reset_user_initialization(user_id)
        release_user_init_lock(user_id)
        
        initialization_count = {"count": 0}
        
        def simulate_pod_initialization():
            """Simulate _ensure_user_initialized from different pods."""
            # Check if already initialized
            if is_user_initialized(user_id):
                return  # Already done
            
            # Try to acquire lock
            if not try_acquire_user_init_lock(user_id, timeout=30):
                return  # Another pod is initializing
            
            try:
                # Simulate initialization work
                time.sleep(0.1)
                
                # Mark as initialized
                mark_user_initialized(user_id, ttl=60)
                
                # Track that initialization happened
                initialization_count["count"] += 1
            finally:
                release_user_init_lock(user_id)
        
        # Simulate 5 pods trying to initialize simultaneously
        threads = []
        for _ in range(5):
            t = threading.Thread(target=simulate_pod_initialization)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # Only one pod should have actually initialized
        assert initialization_count["count"] == 1
        
        # User should be marked as initialized
        assert is_user_initialized(user_id) is True
        
        # Clean up
        reset_user_initialization(user_id)
    
    def test_multiple_users_independent_initialization(self):
        """Test that multiple users can be initialized independently."""
        user_ids = [f"test_user_multi_{i}" for i in range(3)]
        
        # Clean up
        for user_id in user_ids:
            reset_user_initialization(user_id)
        
        # Initialize all users concurrently
        def initialize_user(uid):
            mark_user_initialized(uid, ttl=60)
        
        threads = []
        for user_id in user_ids:
            t = threading.Thread(target=initialize_user, args=(user_id,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # All users should be initialized
        for user_id in user_ids:
            assert is_user_initialized(user_id) is True
        
        # Clean up
        for user_id in user_ids:
            reset_user_initialization(user_id)


class TestUserInitializationTTL:
    """Test TTL (time-to-live) for user initialization."""
    
    def test_initialization_expires_after_ttl(self):
        """Test that initialization flag expires after TTL."""
        user_id = "test_user_ttl_001"
        
        # Mark as initialized with 1 second TTL
        mark_user_initialized(user_id, ttl=1)
        
        # Should be initialized immediately
        assert is_user_initialized(user_id) is True
        
        # Wait for expiration
        time.sleep(2)
        
        # Should no longer be initialized
        assert is_user_initialized(user_id) is False
    
    def test_default_ttl(self):
        """Test that default TTL is reasonable (7 days)."""
        user_id = "test_user_ttl_default_001"
        
        # Clean up
        reset_user_initialization(user_id)
        
        # Mark with default TTL
        mark_user_initialized(user_id)  # Default: 7 days
        
        # Should still be initialized after a few seconds
        time.sleep(2)
        assert is_user_initialized(user_id) is True
        
        # Clean up
        reset_user_initialization(user_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

