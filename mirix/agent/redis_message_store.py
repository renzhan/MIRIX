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
from typing import List, Optional, Dict, Any

from mirix.schemas.mirix_message import MirixMessage, ReasoningMessage

from mirix.settings import settings


# Global Redis client singleton
_redis_client = None


def _coerce_conversation_content(value: Any) -> str:
    """
    Ensure conversation entries are JSON serializable strings.

    Args:
        value: Content to store

    Returns:
        String representation safe for JSON storage
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, ReasoningMessage):
        return value.reasoning
    if isinstance(value, MirixMessage):
        try:
            return value.model_dump_json()
        except TypeError:
            return json.dumps(value.model_dump(mode="json"), ensure_ascii=False)
    if value is None:
        return "None"
    return str(value)


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
    
    Args:
        user_id: User ID for isolation
        user_message: User's message
        assistant_response: Assistant's response
    """
    if user_id is None:
        raise ValueError("user_id is required for add_conversation_to_redis")
    
    client = get_redis_client()
    key = f'mirix:user_conversations:{user_id}'
    
    conversation_data = json.dumps(
        [
            {"role": "user", "content": _coerce_conversation_content(user_message)},
            {
                "role": "assistant",
                "content": _coerce_conversation_content(assistant_response),
            },
        ],
        ensure_ascii=False,
    )
    
    client.rpush(key, conversation_data)


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

