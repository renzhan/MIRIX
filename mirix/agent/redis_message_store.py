"""
✅ TASK 2: Redis message store for temporary message accumulation (multi-pod user isolation)

This module encapsulates all Redis operations for storing temporary messages.
Messages are stored in Redis Lists with user-specific keys to ensure isolation.

Redis data structure:
- Key: mirix:temp_messages:{user_id}
- Type: List (FIFO queue)
- Operations: RPUSH (add), LRANGE (get), LTRIM (remove), LLEN (count)
"""

import json
import redis
import time
from typing import List, Optional, Dict, Any

from mirix.settings import settings


# Global Redis client singleton
_redis_client = None


def get_redis_client() -> redis.Redis:
    """
    Get Redis client singleton with connection pool.
    
    Returns:
        redis.Redis: Redis client instance
    """
    global _redis_client
    if _redis_client is None:
        pool = redis.ConnectionPool(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            db=settings.redis_db,
            max_connections=settings.redis_max_connections,
            socket_timeout=settings.redis_socket_timeout,
            decode_responses=False,  # Binary mode, manual encoding control
        )
        _redis_client = redis.Redis(connection_pool=pool)
    return _redis_client


def add_message_to_redis(user_id: str, timestamp: str, message_data: Dict[str, Any]) -> None:
    """
    Add a message to Redis List for a specific user.
    
    ✅ P1-4: Automatically applies TTL and capacity limits to prevent unbounded growth.
    
    Args:
        user_id: User ID for isolation
        timestamp: Message timestamp
        message_data: Message data dictionary containing image_uris, sources, audio_segments, message
        
    Raises:
        ValueError: If user_id is None
    """
    # ✅ Validate user_id for multi-user isolation
    if user_id is None:
        raise ValueError("user_id is required for add_message_to_redis")
    
    client = get_redis_client()
    key = f"mirix:temp_messages:{user_id}"
    
    # Serialize message
    serialized_data = _serialize_message(timestamp, message_data)
    
    # RPUSH to append to list tail (maintain chronological order)
    client.rpush(key, serialized_data)
    
    # ✅ P1-4: Apply TTL (expires after configured time)
    # This ensures messages don't accumulate forever if absorption fails
    client.expire(key, settings.redis_message_ttl)
    
    # ✅ P1-4: Apply capacity limit (keep only most recent N messages)
    # This prevents memory exhaustion from runaway message accumulation
    current_len = client.llen(key)
    max_len = settings.redis_message_max_length
    
    if current_len > max_len:
        # Trim from the head (remove oldest messages)
        # LTRIM keeps elements from -max_len to -1 (most recent)
        client.ltrim(key, -max_len, -1)


def get_messages_from_redis(user_id: str, limit: Optional[int] = None) -> List[tuple]:
    """
    Get messages from Redis for a specific user.
    
    Args:
        user_id: User ID for isolation
        limit: Optional limit on number of messages to retrieve
        
    Returns:
        List of (timestamp, message_data) tuples
        
    Raises:
        ValueError: If user_id is None
    """
    # ✅ Validate user_id for multi-user isolation
    if user_id is None:
        raise ValueError("user_id is required for get_messages_from_redis")
    
    client = get_redis_client()
    key = f"mirix:temp_messages:{user_id}"
    
    # LRANGE to get all or limited messages
    end = limit - 1 if limit else -1
    messages = client.lrange(key, 0, end)
    
    # Deserialize messages
    return [_deserialize_message(msg) for msg in messages]


def remove_messages_from_redis(user_id: str, count: int) -> None:
    """
    Remove processed messages from Redis (from head).
    
    Args:
        user_id: User ID for isolation
        count: Number of messages to remove from the head
        
    Raises:
        ValueError: If user_id is None
    """
    # ✅ Validate user_id for multi-user isolation
    if user_id is None:
        raise ValueError("user_id is required for remove_messages_from_redis")
    
    client = get_redis_client()
    key = f"mirix:temp_messages:{user_id}"
    
    # LTRIM to keep only messages after 'count'
    client.ltrim(key, count, -1)


def get_message_count_from_redis(user_id: str) -> int:
    """
    Get the number of messages in Redis for a specific user.
    
    Args:
        user_id: User ID for isolation
        
    Returns:
        Number of messages
        
    Raises:
        ValueError: If user_id is None
    """
    # ✅ Validate user_id for multi-user isolation
    if user_id is None:
        raise ValueError("user_id is required for get_message_count_from_redis")
    
    client = get_redis_client()
    key = f"mirix:temp_messages:{user_id}"
    return client.llen(key)


def _serialize_message(timestamp: str, message_data: Dict[str, Any]) -> bytes:
    """
    Serialize message to JSON bytes.
    
    Args:
        timestamp: Message timestamp
        message_data: Message data dictionary
        
    Returns:
        Serialized message as bytes
    """
    # Simplified serialization: only store essential information
    serialized = {
        "timestamp": timestamp,
        "image_uris": _serialize_image_uris(message_data.get("image_uris")),
        "sources": message_data.get("sources"),
        "audio_segments": _serialize_audio_segments(message_data.get("audio_segments")),
        "message": message_data.get("message"),
    }
    return json.dumps(serialized).encode("utf-8")


def _deserialize_message(data: bytes) -> tuple:
    """
    Deserialize message from JSON bytes.
    
    Args:
        data: Serialized message bytes
        
    Returns:
        Tuple of (timestamp, message_data)
    """
    msg = json.loads(data.decode("utf-8"))
    timestamp = msg["timestamp"]
    message_data = {
        "image_uris": _deserialize_image_uris(msg.get("image_uris")),
        "sources": msg.get("sources"),
        "audio_segments": _deserialize_audio_segments(msg.get("audio_segments")),
        "message": msg.get("message"),
    }
    return (timestamp, message_data)


def _serialize_image_uris(image_uris) -> Optional[List[Dict[str, Any]]]:
    """
    Serialize image URIs, handling three types:
    1. Pending upload placeholder (dict with 'pending' key)
    2. Google Cloud File object (has 'uri' attribute)
    3. Local file path (string or path object)
    
    Args:
        image_uris: List of image URI references
        
    Returns:
        Serialized list of image URI dictionaries or None
    """
    if not image_uris:
        return None
    
    result = []
    for ref in image_uris:
        if isinstance(ref, dict) and ref.get("pending"):
            # Pending upload placeholder
            result.append({
                "type": "pending",
                "upload_uuid": ref.get("upload_uuid"),
                "filename": ref.get("filename"),
            })
        elif hasattr(ref, "uri"):
            # Google Cloud File object
            result.append({
                "type": "google_cloud_file",
                "uri": ref.uri,
                "name": getattr(ref, "name", None),
            })
        else:
            # Local file path
            result.append({
                "type": "local_file",
                "path": str(ref),
            })
    return result


def _deserialize_image_uris(serialized_uris) -> Optional[List]:
    """
    Deserialize image URIs.
    
    Args:
        serialized_uris: Serialized list of image URI dictionaries
        
    Returns:
        List of deserialized image URI references or None
    """
    if not serialized_uris:
        return None
    
    result = []
    for item in serialized_uris:
        if item["type"] == "pending":
            # Rebuild pending placeholder
            result.append({
                "upload_uuid": item["upload_uuid"],
                "filename": item["filename"],
                "pending": True,
            })
        elif item["type"] == "google_cloud_file":
            # Return URI dictionary, caller needs to handle (e.g., query upload_manager)
            result.append(item)
        else:
            # Local file path
            result.append(item["path"])
    return result


def _serialize_audio_segments(audio_segments) -> Optional[Dict[str, int]]:
    """
    Serialize audio segments (only record count, not data).
    
    Args:
        audio_segments: List of audio segment data
        
    Returns:
        Dictionary with count or None
    """
    if not audio_segments:
        return None
    return {"count": len(audio_segments)}


def _deserialize_audio_segments(serialized_audio) -> None:
    """
    Deserialize audio segments.
    Audio data is not recovered from Redis.
    
    Args:
        serialized_audio: Serialized audio metadata
        
    Returns:
        None (audio data not stored in Redis)
    """
    return None


# ✅ User Conversation Storage Functions (for multi-user concurrency safety)

def add_conversation_to_redis(user_id: str, user_message: str, assistant_response: str):
    """
    Add a user conversation to Redis for the specified user.
    
    ✅ P1-4: Automatically applies TTL and capacity limits to prevent unbounded growth.
    
    Args:
        user_id: User ID for isolation
        user_message: User's message
        assistant_response: Assistant's response
    """
    if user_id is None:
        raise ValueError("user_id is required for add_conversation_to_redis")
    
    client = get_redis_client()
    key = f'mirix:user_conversations:{user_id}'
    
    conversation_data = json.dumps([
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": assistant_response}
    ])
    
    client.rpush(key, conversation_data)
    
    # ✅ P1-4: Apply TTL (shorter than messages since conversations are ephemeral)
    # Conversations are absorbed quickly, so 1 hour TTL is sufficient
    client.expire(key, settings.redis_conversation_ttl)
    
    # ✅ P1-4: Apply capacity limit (keep only most recent N conversation pairs)
    # This prevents accumulation if absorption is delayed
    current_len = client.llen(key)
    max_len = settings.redis_conversation_max_length
    
    if current_len > max_len:
        # Trim from the head (remove oldest conversations)
        client.ltrim(key, -max_len, -1)


def get_conversations_from_redis(user_id: str) -> List[Dict[str, str]]:
    """
    Get all conversations for the specified user from Redis.
    
    Args:
        user_id: User ID for isolation
        
    Returns:
        List of conversation dictionaries with 'role' and 'content' keys
    """
    if user_id is None:
        raise ValueError("user_id is required for get_conversations_from_redis")
    
    client = get_redis_client()
    key = f'mirix:user_conversations:{user_id}'
    
    serialized_conversations = client.lrange(key, 0, -1)
    
    conversations = []
    for serialized in serialized_conversations:
        conversation_pair = json.loads(serialized)
        conversations.extend(conversation_pair)
    
    return conversations


def clear_conversations_from_redis(user_id: str):
    """
    Clear all conversations for the specified user from Redis.
    
    Args:
        user_id: User ID for isolation
    """
    if user_id is None:
        raise ValueError("user_id is required for clear_conversations_from_redis")
    
    client = get_redis_client()
    key = f'mirix:user_conversations:{user_id}'
    client.delete(key)


def get_conversation_count_from_redis(user_id: str) -> int:
    """
    Get the count of conversation pairs for the specified user.
    
    Args:
        user_id: User ID for isolation
        
    Returns:
        Number of conversation pairs
    """
    if user_id is None:
        raise ValueError("user_id is required for get_conversation_count_from_redis")
    
    client = get_redis_client()
    key = f'mirix:user_conversations:{user_id}'
    return client.llen(key)


# ✅ P0-1: Distributed Lock and Atomic Operations for Multi-Pod Concurrency Safety

def acquire_user_lock(user_id: str, timeout: int = 30) -> bool:
    """
    Acquire a distributed lock for a specific user to prevent concurrent absorption.
    
    This lock prevents multiple pods from simultaneously processing the same user's messages,
    which could lead to duplicate processing or data loss.
    
    Args:
        user_id: User ID for lock isolation (required)
        timeout: Lock expiration time in seconds (default: 30)
        
    Returns:
        True if lock acquired successfully, False if already locked
        
    Raises:
        ValueError: If user_id is None
    """
    if user_id is None:
        raise ValueError("user_id is required for acquire_user_lock")
    
    client = get_redis_client()
    key = f'mirix:lock:absorb:{user_id}'
    
    # SET with NX (only set if not exists) and EX (expiration)
    # Returns True if key was set, False if key already exists
    return client.set(key, '1', nx=True, ex=timeout) is not None


def release_user_lock(user_id: str) -> None:
    """
    Release the distributed lock for a specific user.
    
    Args:
        user_id: User ID for lock isolation (required)
        
    Raises:
        ValueError: If user_id is None
    """
    if user_id is None:
        raise ValueError("user_id is required for release_user_lock")
    
    client = get_redis_client()
    key = f'mirix:lock:absorb:{user_id}'
    client.delete(key)


def atomic_pop_messages(user_id: str, count: int) -> List[tuple]:
    """
    Atomically retrieve and remove messages from Redis using Lua script.
    
    This ensures that the read + delete operation is atomic, preventing race conditions
    where multiple pods might read the same messages before any of them delete them.
    
    The Lua script runs on Redis server side, guaranteeing atomicity:
    1. LRANGE to get first N messages
    2. LTRIM to remove those messages
    3. Return the messages
    
    Args:
        user_id: User ID for isolation (required)
        count: Number of messages to pop from the head of the list
        
    Returns:
        List of (timestamp, message_data) tuples
        
    Raises:
        ValueError: If user_id is None
    """
    if user_id is None:
        raise ValueError("user_id is required for atomic_pop_messages")
    
    client = get_redis_client()
    key = f'mirix:temp_messages:{user_id}'
    
    # Lua script for atomic pop operation
    lua_script = """
    local key = KEYS[1]
    local count = tonumber(ARGV[1])
    
    -- Get first N messages
    local messages = redis.call('LRANGE', key, 0, count - 1)
    
    -- If we got any messages, remove them from the list
    if #messages > 0 then
        redis.call('LTRIM', key, count, -1)
    end
    
    return messages
    """
    
    # Execute Lua script atomically
    # KEYS[1] = key, ARGV[1] = count
    result = client.eval(lua_script, 1, key, count)
    
    # Deserialize the messages
    if result:
        return [_deserialize_message(msg) for msg in result]
    return []


def check_user_lock_exists(user_id: str) -> bool:
    """
    Check if a lock exists for a specific user (for debugging/monitoring).
    
    Args:
        user_id: User ID for lock isolation (required)
        
    Returns:
        True if lock exists, False otherwise
        
    Raises:
        ValueError: If user_id is None
    """
    if user_id is None:
        raise ValueError("user_id is required for check_user_lock_exists")
    
    client = get_redis_client()
    key = f'mirix:lock:absorb:{user_id}'
    return client.exists(key) > 0


# ✅ P0-2: Upload Status Management in Redis for Cross-Pod Visibility

def set_upload_status(upload_id: str, status: str, result: Any = None, filename: str = None, ttl: int = 3600) -> None:
    """
    Set upload status to Redis for cross-pod visibility.
    
    This enables any pod to check the upload status, not just the pod that initiated it.
    Critical for multi-pod deployments where image processing might happen on different pods.
    
    Args:
        upload_id: Unique upload identifier (UUID)
        status: Upload status ('pending', 'completed', 'failed')
        result: Upload result (file_ref object for completed, None for others)
        filename: Original filename for debugging
        ttl: Time-to-live in seconds (default: 1 hour)
        
    Raises:
        ValueError: If upload_id is None
    """
    if upload_id is None:
        raise ValueError("upload_id is required for set_upload_status")
    
    client = get_redis_client()
    key = f'mirix:upload_status:{upload_id}'
    
    # Serialize upload status
    data = {
        'status': status,
        'filename': filename,
        'timestamp': time.time()
    }
    
    # Serialize result based on status
    if status == 'completed' and result is not None:
        # For Google Cloud file reference (Gemini API)
        if hasattr(result, 'uri') and hasattr(result, 'name'):
            data['result'] = {
                'type': 'google_cloud',
                'uri': result.uri,
                'name': result.name,
                'create_time': getattr(result, 'create_time', None)
            }
        else:
            # For other file references (string paths, etc.)
            data['result'] = {
                'type': 'other',
                'value': str(result)
            }
    else:
        data['result'] = None
    
    # Store in Redis with TTL
    client.setex(key, ttl, json.dumps(data))


def get_upload_status(upload_id: str) -> Dict[str, Any]:
    """
    Get upload status from Redis.
    
    This allows any pod to check the status of an upload initiated by another pod.
    
    Args:
        upload_id: Unique upload identifier (UUID)
        
    Returns:
        Dict with keys:
        - status: 'pending', 'completed', 'failed', or 'unknown' (if not found)
        - result: Deserialized file reference (for completed) or None
        - filename: Original filename (if available)
        
    Raises:
        ValueError: If upload_id is None
    """
    if upload_id is None:
        raise ValueError("upload_id is required for get_upload_status")
    
    client = get_redis_client()
    key = f'mirix:upload_status:{upload_id}'
    
    data_str = client.get(key)
    if data_str is None:
        return {'status': 'unknown', 'result': None}
    
    data = json.loads(data_str)
    
    # Deserialize result if present
    result = None
    if data.get('result') is not None:
        result_data = data['result']
        if result_data.get('type') == 'google_cloud':
            # Reconstruct Google Cloud file reference
            # Note: This creates a dict representation; the actual object
            # reconstruction depends on how it's used downstream
            result = {
                'uri': result_data.get('uri'),
                'name': result_data.get('name'),
                'create_time': result_data.get('create_time')
            }
        elif result_data.get('type') == 'other':
            result = result_data.get('value')
    
    return {
        'status': data.get('status', 'unknown'),
        'result': result,
        'filename': data.get('filename')
    }


def delete_upload_status(upload_id: str) -> None:
    """
    Delete upload status from Redis (cleanup after processing).
    
    Args:
        upload_id: Unique upload identifier (UUID)
        
    Raises:
        ValueError: If upload_id is None
    """
    if upload_id is None:
        raise ValueError("upload_id is required for delete_upload_status")
    
    client = get_redis_client()
    key = f'mirix:upload_status:{upload_id}'
    client.delete(key)


def get_all_upload_statuses() -> Dict[str, Dict[str, Any]]:
    """
    Get all upload statuses from Redis (for debugging/monitoring).
    
    Returns:
        Dict mapping upload_id to status info
    """
    client = get_redis_client()
    pattern = 'mirix:upload_status:*'
    
    statuses = {}
    for key in client.scan_iter(match=pattern):
        upload_id = key.decode('utf-8').split(':')[-1]
        try:
            statuses[upload_id] = get_upload_status(upload_id)
        except:
            pass  # Skip invalid entries
    
    return statuses


# ✅ P1-3: User Initialization Management for Multi-Pod Safety

def try_acquire_user_init_lock(user_id: str, timeout: int = 30) -> bool:
    """
    Try to acquire a lock for user initialization.
    
    This prevents multiple pods from initializing the same user concurrently.
    
    Args:
        user_id: User ID for lock isolation (required)
        timeout: Lock expiration time in seconds (default: 30)
        
    Returns:
        True if lock acquired successfully, False if already locked
        
    Raises:
        ValueError: If user_id is None
    """
    if user_id is None:
        raise ValueError("user_id is required for try_acquire_user_init_lock")
    
    client = get_redis_client()
    key = f'mirix:lock:init:{user_id}'
    
    # SET with NX (only set if not exists) and EX (expiration)
    return client.set(key, '1', nx=True, ex=timeout) is not None


def release_user_init_lock(user_id: str) -> None:
    """
    Release the user initialization lock.
    
    Args:
        user_id: User ID for lock isolation (required)
        
    Raises:
        ValueError: If user_id is None
    """
    if user_id is None:
        raise ValueError("user_id is required for release_user_init_lock")
    
    client = get_redis_client()
    key = f'mirix:lock:init:{user_id}'
    client.delete(key)


def is_user_initialized(user_id: str) -> bool:
    """
    Check if a user has been initialized (existing files processed).
    
    Args:
        user_id: User ID to check (required)
        
    Returns:
        True if user has been initialized, False otherwise
        
    Raises:
        ValueError: If user_id is None
    """
    if user_id is None:
        raise ValueError("user_id is required for is_user_initialized")
    
    client = get_redis_client()
    key = f'mirix:user_init_done:{user_id}'
    return client.exists(key) > 0


def mark_user_initialized(user_id: str, ttl: int = 7 * 24 * 3600) -> None:
    """
    Mark a user as initialized (existing files have been processed).
    
    Uses SETNX for idempotency - if already set, this is a no-op.
    
    Args:
        user_id: User ID to mark (required)
        ttl: Time-to-live in seconds (default: 7 days)
        
    Raises:
        ValueError: If user_id is None
    """
    if user_id is None:
        raise ValueError("user_id is required for mark_user_initialized")
    
    client = get_redis_client()
    key = f'mirix:user_init_done:{user_id}'
    
    # SETNX: only set if not exists (idempotent)
    if client.setnx(key, '1'):
        # First time setting, also set TTL
        client.expire(key, ttl)


def reset_user_initialization(user_id: str) -> None:
    """
    Reset user initialization flag (for testing/debugging).
    
    Args:
        user_id: User ID to reset (required)
        
    Raises:
        ValueError: If user_id is None
    """
    if user_id is None:
        raise ValueError("user_id is required for reset_user_initialization")
    
    client = get_redis_client()
    key = f'mirix:user_init_done:{user_id}'
    client.delete(key)

