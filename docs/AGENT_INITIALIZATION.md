# Agent 初始化指南

## 问题说明

在新版本中，服务器启动时**不会自动创建 agents**。这与旧版本不同，旧版本会在启动时自动创建所有默认 agents。

当前版本中：
- `initialize_meta_agent` 只创建 meta agent 和它的子 agents（memory agents）
- 不会创建其他类型的 agents（如 `chat_agent`、`email_reply_agent`、`workflow_agent`、`ace_memory_agent` 等）

## 解决方案

### 方法 1: 使用初始化脚本（推荐）

使用提供的 Python 脚本来初始化所有默认 agents：

```bash
# 从项目根目录运行
python scripts/initialize_default_agents.py
```

这个脚本会：
1. 创建所有在 `AGENT_CONFIGS` 中定义的默认 agents
2. 自动加载系统提示
3. 使用默认的 LLM 和 embedding 配置
4. 跳过已存在的 agents

### 方法 2: 使用 REST API

通过 REST API 端点初始化所有默认 agents：

```bash
curl -X POST "http://localhost:8531/agents/initialize-default" \
  -H "Content-Type: application/json" \
  -H "X-Client-Id: your-client-id" \
  -H "X-Api-Key: your-api-key" \
  -d '{
    "llm_config": {
      "model": "gpt-4o-mini",
      "provider": "openai"
    },
    "embedding_config": {
      "model": "text-embedding-004",
      "provider": "openai"
    }
  }'
```

API 响应示例：

```json
{
  "created": {
    "chat_agent": "agent-xxx",
    "background_agent": "agent-yyy",
    ...
  },
  "skipped": {
    "meta_memory_agent": "agent-zzz"
  },
  "failed": {},
  "total": 13,
  "created_count": 12,
  "skipped_count": 1,
  "failed_count": 0
}
```

### 方法 3: 在代码中初始化

在 Python 代码中直接调用初始化函数：

```python
from mirix.server.server import SyncServer
from scripts.initialize_default_agents import initialize_default_agents

# 创建服务器实例
server = SyncServer()

# 初始化所有默认 agents
created_agents = initialize_default_agents(server=server)

# 查看创建的 agents
for name, agent_state in created_agents.items():
    print(f"{name}: {agent_state.id}")
```

## 默认 Agents 列表

以下 agents 会被自动创建：

1. **chat_agent** - 主要聊天 agent
2. **background_agent** - 后台处理 agent
3. **reflexion_agent** - 反思和优化 agent
4. **episodic_memory_agent** - 事件记忆 agent
5. **procedural_memory_agent** - 程序记忆 agent
6. **knowledge_vault_memory_agent** - 知识库 agent
7. **meta_memory_agent** - 元记忆 agent
8. **semantic_memory_agent** - 语义记忆 agent
9. **core_memory_agent** - 核心记忆 agent
10. **resource_memory_agent** - 资源记忆 agent
11. **email_reply_agent** - 邮件回复 agent
12. **workflow_agent** - 工作流 agent
13. **ace_memory_agent** - ACE 记忆 agent

## 注意事项

1. **已存在的 agents**：如果某个 agent 已经存在（通过名称匹配），脚本会跳过创建，不会报错。

2. **系统提示**：脚本会自动从 `mirix/prompts/system/base/` 目录加载系统提示。如果某个 agent 的系统提示文件不存在，会使用默认提示。

3. **配置**：默认使用 `gpt-4o-mini` 作为 LLM 模型，`text-embedding-004` 作为 embedding 模型。可以通过 API 或脚本参数自定义。

4. **chat_agent 特殊处理**：`chat_agent` 会自动创建 `persona` 和 `human` memory blocks。

## 与 initialize_meta_agent 的区别

- **initialize_meta_agent**：只创建 meta agent 和它的子 agents（memory agents），这些 agents 有父子关系
- **initialize_default_agents**：创建所有独立的默认 agents，包括 chat_agent、email_reply_agent 等

两者可以同时使用，互不冲突。

## 故障排除

如果初始化失败，检查：

1. **数据库连接**：确保数据库连接正常
2. **权限**：确保有创建 agents 的权限
3. **日志**：查看服务器日志了解详细错误信息
4. **依赖**：确保所有必要的工具（base tools）已创建


