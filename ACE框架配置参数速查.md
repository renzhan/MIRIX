# ACE 框架配置参数速查

> 使用 `pip install ace-framework` 安装后的配置说明

---

## 📋 一、训练样本配置（Sample）

```python
from ace import Sample

Sample(
    question="客户邮件内容",              # ✅ 必填
    context="订单状态、workflow信息",    # ⚪ 可选，默认 ""
    ground_truth="优质回复示例",         # ⚪ 可选，用于监督学习
    metadata={                           # ⚪ 可选，自定义元数据
        "email_id": "12345",
        "priority": "high"
    }
)
```

---

## 🤖 二、LLM 客户端配置（LiteLLMClient）

```python
from ace import LiteLLMClient

client = LiteLLMClient(
    # 基础配置
    model="gpt-4o-mini",                # ✅ 必填：模型名称
    api_key="sk-...",                   # ⚪ 可选，默认从环境变量读取
    
    # 生成参数
    temperature=0.7,                    # ⚪ 默认 0.0，范围 0-2（越高越随机）
    max_tokens=512,                     # ⚪ 默认 512（生成长度）
    top_p=0.9,                          # ⚪ 默认 0.9，范围 0-1
    
    # 容错配置
    fallbacks=["gpt-3.5-turbo"],       # ⚪ 可选：备用模型
    max_retries=3,                      # ⚪ 默认 3：重试次数
    timeout=60,                         # ⚪ 默认 60秒
)
```

### 常用模型名称
- OpenAI: `gpt-4o-mini`, `gpt-4o`, `gpt-3.5-turbo`
- Claude: `claude-3-5-sonnet-20241022`, `claude-3-haiku-20240307`
- Gemini: `gemini-pro`, `gemini-1.5-pro`

---

## 🔧 三、Agent 配置

```python
from ace import Generator, Reflector, Curator

# Generator（生成回复）
generator = Generator(
    llm=client,                         # ✅ 必填
    max_retries=3                       # ⚪ 默认 3
)

# Reflector（反思分析）
reflector = Reflector(
    llm=client,
    max_retries=3
)

# Curator（更新策略）
curator = Curator(
    llm=client,
    max_retries=3
)
```

---

## 🎓 四、学习适配器配置（OfflineAdapter）

```python
from ace import OfflineAdapter, Playbook

adapter = OfflineAdapter(
    playbook=Playbook(),                # ⚪ 可选，策略库
    generator=generator,                # ✅ 必填
    reflector=reflector,                # ✅ 必填
    curator=curator,                    # ✅ 必填
    
    max_refinement_rounds=1,            # ⚪ 默认 1：反思轮次（1-5）
    reflection_window=3,                # ⚪ 默认 3：记住最近N次反思
    enable_observability=True,          # ⚪ 默认 True：启用监控
)
```

---

## 🚀 五、运行学习

```python
# 运行训练
results = adapter.run(
    samples=training_samples,           # ✅ 必填：样本列表
    environment=my_environment,         # ✅ 必填：评估环境
    epochs=2,                           # ⚪ 默认 1：训练轮数
)
```

---

## 💡 完整示例

```python
from ace import (
    LiteLLMClient, Generator, Reflector, Curator,
    OfflineAdapter, Sample, Playbook
)

# 1. 配置 LLM
client = LiteLLMClient(
    model="gpt-4o-mini",
    temperature=0.7,
    max_tokens=800
)

# 2. 创建 ACE 组件
adapter = OfflineAdapter(
    playbook=Playbook(),
    generator=Generator(client),
    reflector=Reflector(client),
    curator=Curator(client),
    max_refinement_rounds=2
)

# 3. 准备训练样本
samples = [
    Sample(
        question="客户邮件内容",
        context="订单状态信息",
        ground_truth="优质回复示例"  # 可选
    ),
]

# 4. 运行学习
results = adapter.run(
    samples=samples,
    environment=my_environment,
    epochs=2
)

# 5. 保存学习成果
adapter.playbook.save_to_file("email_playbook.json")
```

---

## 📊 参数优先级建议

| 场景 | 推荐参数 |
|------|---------|
| **邮件回复** | temperature=0.7, max_tokens=800 |
| **客服对话** | temperature=0.5, max_tokens=500 |
| **事实性回复** | temperature=0.0, max_tokens=300 |
| **创意内容** | temperature=1.0, max_tokens=1000 |

---

## ⚙️ 快速调优指南

1. **温度（temperature）**：
   - 0.0 = 确定性，适合标准回复
   - 0.7 = 平衡，适合大多数场景
   - 1.5+ = 创造性，适合营销文案

2. **训练轮数（epochs）**：
   - 1-2 轮：快速验证
   - 3-5 轮：常规训练
   - 5+ 轮：深度学习（可能过拟合）

3. **反思轮次（max_refinement_rounds）**：
   - 1 = 快速反馈
   - 2-3 = 深度分析（推荐）
   - 5+ = 最深度（耗时较长）

---

**所有参数都可以在初始化时直接传入配置！** ✅

