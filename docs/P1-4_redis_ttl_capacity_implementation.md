# ✅ P1-4: Redis TTL 和容量限制 - 实现文档

## 📋 **任务概述**

**目标**: 为 Redis 消息队列和对话队列添加 TTL（过期时间）和容量限制，防止无限增长导致内存耗尽。

**优先级**: P1（重要）

**实现日期**: 2025-10-15

---

## 🎯 **解决的核心问题**

### **问题描述**

在修改前，Redis 消息队列存在以下风险：

1. **无 TTL**: 消息永不过期，即使长时间未被吸收
2. **无容量限制**: 队列可以无限增长
3. **内存泄漏风险**: 
   - 如果吸收流程失败/卡住，消息无限累积
   - 异常用户疯狂发送消息，队列爆炸
   - Redis 内存耗尽，影响所有用户

### **影响范围**
- Redis 内存压力增大
- 性能下降（大队列操作变慢）
- 可能导致 OOM (Out Of Memory)
- 影响系统稳定性

---

## 🔧 **实现方案**

### **设计原则**

1. **TTL 防止永久积压**: 消息在一定时间后自动过期
2. **容量限制防止爆炸**: 队列超过上限时自动裁剪
3. **保留最新数据**: 裁剪时删除最旧的消息
4. **自动应用**: 每次添加消息时自动应用规则

### **架构图**

```
修改前（无保护）:
┌─────────────────────────────────┐
│ Redis: mirix:temp_messages:user1│
│ ├─ msg_1                        │
│ ├─ msg_2                        │
│ ├─ ...                          │
│ ├─ msg_9999  ← 无限增长 ❌      │
│ └─ msg_10000                    │
│ TTL: -1 (永不过期) ❌            │
│ 大小: 无限制 ❌                  │
└─────────────────────────────────┘
        ↓
    内存耗尽 💥


修改后（有保护）:
┌─────────────────────────────────┐
│ Redis: mirix:temp_messages:user1│
│ ├─ msg_1                        │
│ ├─ msg_2                        │
│ ├─ ...                          │
│ ├─ msg_99                       │
│ └─ msg_100  ← 容量上限 ✅       │
│ TTL: 86400秒 (24小时) ✅        │
│ 大小: 最多100条 ✅               │
└─────────────────────────────────┘
        ↓
    安全可控 ✅
```

---

## 📐 **实现细节**

### **1. 配置参数（settings.py）**

```python
# ✅ P1-4: Redis TTL and capacity limits for message queues
redis_message_ttl: int = 86400  # 24 hours (seconds)
redis_message_max_length: int = 100  # Maximum messages per user queue
redis_conversation_ttl: int = 3600  # 1 hour (seconds)
redis_conversation_max_length: int = 50  # Maximum conversation pairs per user
```

#### **参数说明**

| 参数 | 默认值 | 说明 | 原因 |
|------|--------|------|------|
| `redis_message_ttl` | 86400秒<br>(24小时) | 消息队列过期时间 | 正常情况下消息在几分钟内被吸收，<br>24小时是足够的兜底时间 |
| `redis_message_max_length` | 100条 | 消息队列最大长度 | 正常吸收阈值为10条，<br>100条是10倍缓冲 |
| `redis_conversation_ttl` | 3600秒<br>(1小时) | 对话队列过期时间 | 对话很快被吸收，<br>1小时足够 |
| `redis_conversation_max_length` | 50对 | 对话队列最大长度 | 对话数量通常较少，<br>50对足够 |

---

### **2. add_message_to_redis 修改**

#### **修改前（无保护）**
```python
def add_message_to_redis(user_id, timestamp, message_data):
    client = get_redis_client()
    key = f"mirix:temp_messages:{user_id}"
    
    serialized_data = _serialize_message(timestamp, message_data)
    client.rpush(key, serialized_data)
    # ❌ 无 TTL
    # ❌ 无容量限制
```

#### **修改后（有保护）**
```python
def add_message_to_redis(user_id, timestamp, message_data):
    client = get_redis_client()
    key = f"mirix:temp_messages:{user_id}"
    
    serialized_data = _serialize_message(timestamp, message_data)
    client.rpush(key, serialized_data)
    
    # ✅ P1-4: Apply TTL
    client.expire(key, settings.redis_message_ttl)
    
    # ✅ P1-4: Apply capacity limit
    current_len = client.llen(key)
    max_len = settings.redis_message_max_length
    
    if current_len > max_len:
        # Trim from head (remove oldest messages)
        client.ltrim(key, -max_len, -1)
```

#### **保护机制说明**

1. **TTL 保护**:
   - 每次添加消息时刷新 TTL
   - 如果队列24小时未被访问，自动删除
   - 防止僵尸队列永久占用内存

2. **容量保护**:
   - 每次添加后检查队列长度
   - 超过100条时，删除最旧的消息
   - 保留最新的100条消息

3. **LTRIM 详解**:
   ```
   原队列: [msg_1, msg_2, ..., msg_100, msg_101]  # 101条
   
   LTRIM key -100 -1
   
   结果: [msg_2, msg_3, ..., msg_100, msg_101]  # 100条
   
   解释: -100 到 -1 表示从倒数第100个到最后一个
   ```

---

### **3. add_conversation_to_redis 修改**

#### **修改内容**
```python
def add_conversation_to_redis(user_id, user_message, assistant_response):
    client = get_redis_client()
    key = f'mirix:user_conversations:{user_id}'
    
    conversation_data = json.dumps([
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": assistant_response}
    ])
    
    client.rpush(key, conversation_data)
    
    # ✅ P1-4: Apply TTL (shorter than messages)
    client.expire(key, settings.redis_conversation_ttl)
    
    # ✅ P1-4: Apply capacity limit
    current_len = client.llen(key)
    max_len = settings.redis_conversation_max_length
    
    if current_len > max_len:
        client.ltrim(key, -max_len, -1)
```

#### **为什么对话 TTL 更短？**

- **消息**: 需要累积到一定数量才吸收（10条阈值），可能需要几分钟到几小时
- **对话**: 每次用户交互后立即添加并很快被吸收，生命周期短
- **结论**: 对话 1 小时 TTL 足够，消息需要 24 小时

---

## 🔄 **执行流程**

### **场景 1: 正常使用（无触发限制）**

```
用户发送消息 #1
    ↓
add_message_to_redis("user123", "2025-01-01 00:00:00", {...})
    ├─ RPUSH mirix:temp_messages:user123 = [msg_1]
    ├─ EXPIRE mirix:temp_messages:user123 86400
    ├─ LLEN = 1 (< 100) → 无需裁剪
    └─ 完成 ✅

... 用户继续发送 9 条消息 ...

用户发送消息 #10
    ↓
达到吸收阈值 (TEMPORARY_MESSAGE_LIMIT = 10)
    ↓
absorb_content_into_memory()
    ├─ atomic_pop_messages("user123", 10)
    │   └─ Lua 脚本原子删除 10 条
    ├─ 处理消息并发送到记忆系统
    └─ 队列清空 → LLEN = 0

结果: 队列保持健康，无积压 ✅
```

---

### **场景 2: 异常用户疯狂发送（触发容量限制）**

```
异常用户连续发送 120 条消息（吸收未触发）
    ↓
第 101 条消息:
add_message_to_redis("user_spam", "...", {...})
    ├─ RPUSH → [msg_1, msg_2, ..., msg_100, msg_101]
    ├─ EXPIRE 86400
    ├─ LLEN = 101 (> 100) 🚨
    └─ LTRIM -100 -1 → 删除 msg_1，保留 msg_2 到 msg_101

第 102 条消息:
add_message_to_redis("user_spam", "...", {...})
    ├─ RPUSH → [msg_2, ..., msg_101, msg_102]
    ├─ EXPIRE 86400
    ├─ LLEN = 101 (> 100) 🚨
    └─ LTRIM -100 -1 → 删除 msg_2，保留 msg_3 到 msg_102

... 以此类推 ...

第 120 条消息:
    └─ 队列始终保持 100 条最新消息

结果: 队列不会爆炸，内存可控 ✅
```

---

### **场景 3: 吸收失败长时间未处理（触发 TTL）**

```
时刻 T0: 用户发送 5 条消息
    ├─ 队列: [msg_1, msg_2, msg_3, msg_4, msg_5]
    └─ TTL: 86400 秒

时刻 T1 (12小时后): 吸收流程因异常失败
    ├─ 队列: 仍然是 [msg_1, ..., msg_5]
    └─ TTL: 43200 秒（剩余12小时）

时刻 T2 (24小时后): TTL 到期
    ├─ Redis 自动删除 Key
    └─ 队列: [] (已不存在)

结果: 僵尸队列自动清理，不占内存 ✅
```

---

## 📂 **修改的文件**

### **1. `mirix/settings.py`**
- 新增 `redis_message_ttl = 86400` - 消息 TTL（24小时）
- 新增 `redis_message_max_length = 100` - 消息容量上限
- 新增 `redis_conversation_ttl = 3600` - 对话 TTL（1小时）
- 新增 `redis_conversation_max_length = 50` - 对话容量上限

**行数**: +5 行

---

### **2. `mirix/agent/redis_message_store.py`**

#### `add_message_to_redis` (Line 47-86)
- 添加 `client.expire()` 调用（TTL）
- 添加容量检查和 `client.ltrim()` 调用
- 更新文档说明

#### `add_conversation_to_redis` (Line 308-343)
- 添加 `client.expire()` 调用（TTL）
- 添加容量检查和 `client.ltrim()` 调用
- 更新文档说明

**修改行数**: +28 行

---

## 🧪 **测试覆盖**

### **测试文件**: `tests/test_redis_ttl_capacity.py`

### **测试场景**

#### **1. 消息队列 TTL 测试**
- ✅ TTL 自动应用
- ✅ TTL 在添加新消息时刷新
- ✅ TTL 机制验证（实际过期需时间）

#### **2. 消息队列容量测试**（关键）
- ✅ 超过容量时自动裁剪
- ✅ 保留最新消息（最旧的被删除）
- ✅ 渐进式添加的容量控制

#### **3. 对话队列 TTL 测试**
- ✅ TTL 自动应用
- ✅ 对话 TTL 短于消息 TTL（设计验证）

#### **4. 对话队列容量测试**
- ✅ 超过容量时自动裁剪
- ✅ 保留最新对话

#### **5. 配置验证测试**
- ✅ 容量限制值合理（50-1000）
- ✅ TTL 值合理（1小时-7天）

---

## 📊 **性能影响分析**

### **额外操作**
- **每次 `add_message_to_redis`**:
  - +1 `EXPIRE` 命令 (~0.1ms)
  - +1 `LLEN` 命令 (~0.1ms)
  - +1 `LTRIM` 命令（仅当超容量时，~1ms）

**总增加**: 正常情况 ~0.2ms，超容量时 ~1.2ms

### **吞吐量影响**
- **可忽略**: 相比序列化和网络开销（5-10ms），增加的延迟可忽略
- **容量裁剪**: 仅在异常情况触发（正常使用不会超100条）

### **内存节省**
- ✅ 防止无限增长
- ✅ 自动清理僵尸队列
- ✅ 可预测的内存使用

---

## ⚠️ **注意事项**

### **1. 容量限制的副作用**

**问题**: 如果用户真的需要超过100条消息怎么办？

**答案**: 
- 正常情况下，10条就会触发吸收
- 100条是10倍缓冲，足够应对暂时的吸收延迟
- 如果真的超过100条，说明吸收流程有问题，需要修复而不是增加容量

**建议**: 
- 监控队列长度，如果经常接近100，说明吸收阈值或频率需要调整

---

### **2. TTL 的权衡**

**太短**: 消息可能在被吸收前过期（数据丢失）  
**太长**: 僵尸队列占用内存时间长

**当前选择**: 24小时是合理的平衡点
- 正常情况: 消息在几分钟内被吸收
- 异常情况: 24小时内应该能发现和修复问题
- 极端情况: 24小时后自动清理，防止永久泄漏

---

### **3. 裁剪策略**

**LTRIM 从头部裁剪**（删除最旧消息）的原因：
- 时序重要性: 最新的消息更重要
- 上下文连续性: 保留最近的上下文
- 用户体验: 如果要丢失，丢失旧消息用户感知更小

**备选方案（未采用）**:
- 从尾部裁剪: 会丢失最新消息 ❌
- 随机裁剪: 破坏时序 ❌
- 拒绝新消息: 用户体验差 ❌

---

### **4. 配置调优建议**

根据实际使用调整配置：

**高频用户场景** (每分钟多条消息):
```python
redis_message_max_length = 200  # 增加缓冲
redis_message_ttl = 86400 * 2   # 2天
```

**低频用户场景** (偶尔使用):
```python
redis_message_max_length = 50   # 减少内存占用
redis_message_ttl = 3600 * 6    # 6小时足够
```

**测试环境**:
```python
redis_message_max_length = 20   # 快速触发
redis_message_ttl = 300         # 5分钟（快速测试过期）
```

---

## 🔍 **监控建议**

### **关键指标**

1. **队列长度分布**:
   ```python
   # 监控各用户队列长度
   for user_id in active_users:
       count = get_message_count_from_redis(user_id)
       if count > 80:  # 接近上限
           alert(f"User {user_id} queue near capacity: {count}/100")
   ```

2. **TTL 健康度**:
   ```python
   # 检查队列 TTL
   client = get_redis_client()
   for key in client.scan_iter("mirix:temp_messages:*"):
       ttl = client.ttl(key)
       if ttl < 3600:  # 小于1小时
           alert(f"Queue {key} TTL low: {ttl}s")
   ```

3. **裁剪频率**:
   ```python
   # 添加日志记录裁剪事件
   if current_len > max_len:
       logger.warning(f"Trimming user {user_id} queue from {current_len} to {max_len}")
       client.ltrim(key, -max_len, -1)
   ```

---

## ✅ **验收标准**

- [x] **配置添加**: 4 个新配置项正确添加
- [x] **TTL 应用**: 消息和对话队列自动设置 TTL
- [x] **容量限制**: 队列超限时自动裁剪
- [x] **保留最新**: 裁剪时删除最旧数据
- [x] **测试覆盖**: 5 个测试类，15+ 测试用例
- [x] **无 linter 错误**: 所有修改通过检查
- [x] **文档完备**: 实现文档、配置说明、监控建议

---

## 📚 **相关文档**

- [Redis EXPIRE 命令](https://redis.io/commands/expire/)
- [Redis LTRIM 命令](https://redis.io/commands/ltrim/)
- [Redis TTL 最佳实践](https://redis.io/docs/manual/keyspace/)

---

## 👥 **贡献者**

- **实现**: AI Assistant
- **审查**: User
- **日期**: 2025-10-15

---

**状态**: ✅ 已完成并通过测试

**影响**: 显著提升系统稳定性，防止 Redis 内存耗尽，确保多用户环境下的可预测性能。

