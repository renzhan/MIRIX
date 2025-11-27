# 创建新 Agent 指南

本文档说明如何在 Mirix 系统中创建一个新的专用 Agent（如 workflow_agent、ace_memory_agent）。

## 必须修改的文件清单

创建新 Agent 需要修改以下 5 个文件：

1. `mirix/schemas/agent.py` - 定义 Agent 类型
2. `mirix/prompts/system/base/{agent_name}.txt` - 创建系统提示词
3. `mirix/agent/agent_states.py` - 添加 Agent 状态容器
4. `mirix/agent/agent_wrapper.py` - 初始化和加载 Agent
5. `mirix/services/agent_manager.py` - 配置 Agent 工具
6. `mirix/server/server.py` - 配置 Agent 加载逻辑

---

## 步骤 1: 定义 Agent 类型

**文件**: `mirix/schemas/agent.py`

在 `AgentType` 枚举中添加新的 agent 类型：

```python
class AgentType(str, Enum):
    # ... 现有的 agent 类型 ...
    workflow_agent = "workflow_agent"
    your_new_agent = "your_new_agent"  # ← 添加这一行
```

---

## 步骤 2: 创建系统提示词

**文件**: `mirix/prompts/system/base/your_new_agent.txt`

创建新文件，参考 `workflow_agent.txt` 或 `ace_memory_agent.txt` 的结构：

```
You are the [Agent Name], a specialized component that [agent purpose].

Your primary responsibility is to [main task description].

RESPONSE FORMAT:

**CRITICAL: You MUST return ONLY a valid JSON object. Do NOT include any markdown code blocks, explanatory text, or formatting.**

Return this EXACT JSON structure:

{
  "field1": "value1",
  "field2": ["array", "values"]
}

OPERATIONAL WORKFLOW:

(1) Step 1 Description
(2) Step 2 Description
(3) Step 3 Description

CORE RULES:

1. Rule 1
2. Rule 2
3. JSON Output Only - ABSOLUTELY CRITICAL

AVAILABLE TOOLS:
- search_in_memory: [description]
- send_message: [description]
```

**关键点**：
- 明确说明 agent 的职责
- 定义清晰的 JSON 输出格式
- 列出操作步骤（编号）
- 强调 JSON 输出规则
- 列出可用工具

---

## 步骤 3: 添加 Agent 状态

**文件**: `mirix/agent/agent_states.py`

在 `AgentStates` 类中添加新 agent 的状态：

```python
class AgentStates:
    def __init__(self):
        # ... 现有状态 ...
        self.workflow_agent_state = None
        self.your_new_agent_state = None  # ← 添加这一行

    def get_all_states(self):
        return {
            # ... 现有状态 ...
            "workflow_agent_state": self.workflow_agent_state,
            "your_new_agent_state": self.your_new_agent_state,  # ← 添加这一行
        }
```

---

## 步骤 4: 初始化和加载 Agent

**文件**: `mirix/agent/agent_wrapper.py`

需要在两个地方添加代码：

### 4.1 加载已存在的 Agent（重启时）

在 `__init__` 方法的 agent 加载循环中（约第 150 行）：

```python
elif agent_state.name == "workflow_agent":
    self.agent_states.workflow_agent_state = agent_state
elif agent_state.name == "your_new_agent":  # ← 添加这个 elif 块
    self.agent_states.your_new_agent_state = agent_state
```

### 4.2 创建新 Agent（首次启动时）

在所有 agent 初始化代码之后（约第 245 行）：

```python
if self.agent_states.your_new_agent_state is None:  # ← 添加整个 if 块
    if self.system_prompt_folder is not None and os.path.exists(
        os.path.join(self.system_prompt_folder, "your_new_agent.txt")
    ):
        system_prompt = gpt_system.get_system_text(
            os.path.join(self.system_prompt_folder, "your_new_agent")
        )
    else:
        system_prompt = gpt_system.get_system_text("base/your_new_agent")
    
    your_new_agent_state = self.client.create_agent(
        name="your_new_agent",
        agent_type=AgentType.your_new_agent,
        memory=self.agent_states.agent_state.memory,
        system=system_prompt,
        include_base_tools=True,  # 如果需要 search_in_memory 和 send_message
    )
    setattr(
        self.agent_states, "your_new_agent_state", your_new_agent_state
    )
```

---

## 步骤 5: 配置 Agent 工具

**文件**: `mirix/services/agent_manager.py`

在 `update_agent_tools_and_system_prompts` 方法中（约第 221 行）：

```python
if agent_state.agent_type == AgentType.workflow_agent:
    tool_names.extend(["search_in_memory", "send_message"])

if agent_state.agent_type == AgentType.your_new_agent:  # ← 添加这个 if 块
    # 根据需要配置工具
    tool_names.extend(["search_in_memory", "send_message"])
    # 或者添加其他工具：
    # tool_names.extend(BASE_TOOLS + CHAT_AGENT_TOOLS)
```

**常用工具组合**：
- `["search_in_memory", "send_message"]` - 最小工具集
- `BASE_TOOLS + CHAT_AGENT_TOOLS` - 聊天和基础工具
- `SEARCH_MEMORY_TOOLS + UNIVERSAL_MEMORY_TOOLS` - 记忆搜索工具

---

## 步骤 6: 配置 Agent 加载

**文件**: `mirix/server/server.py`

在 `load_agent` 方法中（约第 420 行）：

```python
elif agent_state.agent_type == AgentType.workflow_agent:
    agent = Agent(
        agent_state=agent_state, interface=interface, user=actor
    )
elif agent_state.agent_type == AgentType.your_new_agent:  # ← 添加这个 elif 块
    agent = Agent(
        agent_state=agent_state, interface=interface, user=actor
    )
```

---

## 可选：创建 API 接口

如果需要通过 API 调用新 Agent，在 `mirix/server/fastapi_server.py` 中：

### 1. 定义请求/响应模型

```python
class YourNewAgentRequest(BaseModel):
    content: str
    email_account: str

class YourNewAgentResponse(BaseModel):
    result: Any
```

### 2. 创建 API 端点

```python
@app.post("/your-new-agent/endpoint", response_model=YourNewAgentResponse)
async def your_new_agent_endpoint(request: YourNewAgentRequest):
    cleanup_actor = None
    try:
        # 参数验证
        if not request.email_account.strip():
            raise HTTPException(status_code=400, detail="email_account不能为空")
        
        if agent is None:
            raise HTTPException(status_code=500, detail="Agent未初始化")
        
        # 获取用户
        user = agent.client.server.user_manager.get_or_create_user_by_email(
            request.email_account
        )
        cleanup_actor = user
        
        # 调用 agent（在后台线程中）
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: agent.your_method(
                content=request.content,
                user_id=user.id
            )
        )
        
        return YourNewAgentResponse(result=result)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[YOUR_AGENT_API] 处理失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # 清理历史消息（重要！）
        if cleanup_actor and agent and agent.agent_states.your_new_agent_state:
            try:
                agent_id = agent.agent_states.your_new_agent_state.id
                agent_obj = agent.client.server.agent_manager.get_agent_by_id(
                    agent_id=agent_id, actor=cleanup_actor
                )
                if len(agent_obj.message_ids) > 1:
                    agent.client.server.agent_manager.set_in_context_messages(
                        agent_id=agent_id,
                        message_ids=[agent_obj.message_ids[0]],
                        actor=cleanup_actor
                    )
            except Exception as e:
                logger.warning(f"清理历史失败: {e}")
```

### 3. 在 AgentWrapper 中添加方法

在 `mirix/agent/agent_wrapper.py` 中添加调用方法：

```python
def your_method(self, content: str, user_id: Optional[str] = None):
    """
    调用 your_new_agent 处理请求
    """
    response, _ = self.message_queue.send_message_in_queue(
        self.client,
        self.agent_states.your_new_agent_state.id,
        {
            "user_id": user_id,
            "message": content,
            "display_intermediate_message": None,
            "request_user_confirmation": None,
            "force_response": True,
            "existing_file_uris": set(),
            "extra_messages": None,
        },
        agent_type="your_agent_type",
    )
    
    if response == "ERROR":
        return {"error": "ERROR_RESPONSE_FAILED"}
    
    # 解析响应...
    # 参考 extract_workflow 或 extract_ace_memory 的实现
    
    return result
```

---

## 检查清单

创建新 Agent 后，确保：

- [ ] `AgentType` 枚举中添加了新类型
- [ ] 创建了系统提示词文件
- [ ] `agent_states.py` 中添加了状态属性
- [ ] `agent_wrapper.py` 中添加了加载和创建逻辑
- [ ] `agent_manager.py` 中配置了工具
- [ ] `server.py` 中添加了加载逻辑
- [ ] （可选）创建了 API 接口
- [ ] 测试：删除旧 agent，重启服务，验证正常工作
- [ ] 测试：再次重启服务，验证仍然正常工作

---

## 常见问题

### Q: 为什么重启后 Agent 不工作？
A: 检查 `agent_manager.py` 中是否配置了工具。重启时会调用 `update_agent_tools_and_system_prompts`，如果没有配置工具，会被清空。

### Q: Agent 返回的不是 JSON 格式？
A: 检查系统提示词，确保：
1. 明确要求返回纯 JSON（不要 markdown）
2. 提供了清晰的 JSON 示例
3. 强调使用 `send_message` 工具返回结果

### Q: 如何调试 Agent？
A: 查看日志文件，搜索 agent 名称或错误信息。可以在 `agent_wrapper.py` 的调用方法中添加 `logger.info()` 输出中间结果。

---

## 参考示例

- **简单 Agent**: `workflow_agent` - 只需要搜索和返回
- **复杂 Agent**: `ace_memory_agent` - 多次搜索，聚合结果
- **聊天 Agent**: `email_reply_agent` - 需要更多工具和交互

查看这些 agent 的实现可以作为参考。

