# Email Graph Extraction

使用LangChain LLMGraphTransformer从企业邮件中抽取知识图谱的模块。

## 功能特性

- 使用LangChain的LLMGraphTransformer进行图谱抽取
- 支持从邮件中抽取**人物(Person)、公司(Company)、业务(Business)、部门(Department)**实体及其关系
- 使用Neo4j图数据库存储图谱数据
- 支持用户数据隔离（Multi-tenant）
- 智能区分个人、组邮箱、部门、公司
- 提供简单的集成接口

## 安装

### 1. 安装依赖

```bash
pip install -r email_graph_extraction/requirements.txt
```

### 2. 安装Neo4j

请参考[Neo4j官方文档](https://neo4j.com/docs/)安装和配置Neo4j数据库。

### 3. 配置环境变量

在项目根目录的`.env`文件中添加以下配置：

```env
# OpenAI配置
OPENAI_API_KEY=your_openai_api_key_here
# 默认使用GPT-5.1模型，无需配置OPENAI_MODEL环境变量

# Neo4j配置
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_neo4j_password_here
```

## 快速开始

### 基本使用

```python
from email_graph_extraction import extract_and_store_graph

# 邮件数据
email_data = {
    'subject': 'Project Update',
    'body': 'John Smith from ABC Corp is leading the Q4 project. He works with Mary Johnson.',
    'sender': 'john@example.com',
    'recipients': 'team@company.com',  # 用户邮箱账户
    'id': 'email-123'
}

# 抽取并存储图谱
result = extract_and_store_graph(
    email_data=email_data,
    user_id='user-123',
    email_account='team@company.com',  # 必需：用户邮箱账户
    email_id='email-123'
)

print(f"抽取了 {result['nodes_extracted']} 个节点")
print(f"抽取了 {result['relationships_extracted']} 个关系")

# 打印三元组
graph_doc = result['graph_document']
for rel in graph_doc.relationships:
    print(f"{rel.source.id} -[{rel.type}]-> {rel.target.id}")
```

### 高级使用

```python
from email_graph_extraction import GraphExtractor, Neo4jManager
from email_graph_extraction.config import NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD

# 创建Neo4j管理器
neo4j_manager = Neo4jManager(
    uri=NEO4J_URI,
    username=NEO4J_USERNAME,
    password=NEO4J_PASSWORD
)

# 创建图谱抽取器
extractor = GraphExtractor(
    neo4j_manager=neo4j_manager,
    model_name='gpt-5.1',  # 默认使用GPT-5.1
    # GPT-5.1不支持temperature参数
)

# 抽取图谱
graph_doc = extractor.extract_from_email(
    email_data=email_data,
    user_id='user-123'
)

# 存储到Neo4j
stats = extractor.store_graph_document(
    graph_doc=graph_doc,
    user_id='user-123',
    source_email_id='email-123'
)

print(f"存储了 {stats['nodes_created']} 个节点")
print(f"存储了 {stats['relationships_created']} 个关系")

# 关闭连接
neo4j_manager.close()
```

## API文档

### extract_and_store_graph

主集成函数，用于抽取并存储图谱。

**参数：**
- `email_data` (dict): 邮件数据字典，应包含以下字段：
  - `subject` (str): 邮件主题
  - `body` 或 `content` 或 `content_text` (str): 邮件正文
  - `sender` 或 `from` (str): 发件人
  - `recipients` 或 `to` (str): 收件人（可选）
  - `id` 或 `email_id` (str): 邮件ID（可选）
- `user_id` (str): 用户ID，用于数据隔离
- `email_id` (str, 可选): 邮件ID，如果不提供则从email_data中提取
- `neo4j_uri` (str, 可选): Neo4j连接URI
- `neo4j_username` (str, 可选): Neo4j用户名
- `neo4j_password` (str, 可选): Neo4j密码
- `openai_api_key` (str, 可选): OpenAI API Key
- `model_name` (str, 可选): OpenAI模型名称（默认：gpt-5.1）

**返回：**
- `dict`: 包含以下字段的字典：
  - `nodes_extracted` (int): 抽取的节点数
  - `relationships_extracted` (int): 抽取的关系数
  - `nodes_stored` (int): 存储的节点数
  - `relationships_stored` (int): 存储的关系数

### GraphExtractor

图谱抽取器类。

**方法：**
- `extract_from_text(text, user_id, source_email_id)`: 从文本抽取图谱
- `extract_from_email(email_data, user_id, email_id, email_account)`: 从邮件抽取图谱
- `store_graph_document(graph_doc, user_id, source_email_id)`: 存储图谱文档
- `extract_and_store(email_data, user_id, email_account, email_id)`: 抽取并存储（一站式）
- `batch_extract(emails, user_id, email_account)`: 批量处理多封邮件

### Neo4jManager

Neo4j数据库管理器类。

**方法：**
- `create_or_update_node(node_type, name, properties, user_id)`: 创建或更新节点
- `create_or_update_relationship(...)`: 创建或更新关系
- `query_nodes(...)`: 查询节点
- `query_relationships(...)`: 查询关系
- `get_subgraph(node_name, node_type, depth, user_id)`: 获取子图谱
- `close()`: 关闭数据库连接

## 图谱Schema

### 节点类型

#### 1. Person（人物）
**定义：**真实的个人（内部员工或外部联系人）

**必需属性：**
- `name` (string): 姓名
- `role` (string): 角色分类，可选值：
  - `self`: 用户本人
  - `colleague`: 内部同事
  - `customer_contact`: 客户联系人
  - `vendor_contact`: 供应商联系人
  - `carrier_contact`: 承运人联系人
  - `partner_contact`: 合作伙伴联系人

**可选属性：**
- `email` (string): 邮箱地址
- `title` (string): 职位（如 "Sales Manager"）
- `department` (string): 部门名称（签名中的部门信息）
- `domain` (string): 邮箱域名（从email提取）

**系统属性：**user_id, created_at, updated_at

**注意：**组邮箱/部门邮箱应该使用 **Department** 节点，不是Person

---

#### 2. Department（部门/团队）
**定义：**组织内的部门、团队或组邮箱

**必需属性：**
- `name` (string): 部门/团队名称（如 "Customer Support", "HR Department"）
- `is_internal` (bool): 是否为内部部门

**可选属性：**
- `company_name` (string): 所属公司名称

**系统属性：**user_id, created_at, updated_at

**识别规则：**
- 邮箱地址包含关键词：hr, finance, ops, support, help, it, sales, info, team, dept, admin, service
- 名称包含：Department, Dept, Team, Support, Helpdesk

**示例：**
- ✅ `hr@item.com` → Department
- ✅ `Customer Support` → Department
- ✅ `IT Team` → Department
- ❌ `John Smith` → Person（不是Department）

---

#### 3. Company（公司）
**定义：**真实的法人实体（客户、供应商、承运人、合作伙伴、竞争对手）

**必需属性：**
- `name` (string): 公司名称
- `company_type` (string): 公司类型，可选值：
  - `supplier`: 供应商
  - `carrier`: 承运人/物流公司
  - `customer`: 客户公司
  - `partner`: 合作伙伴公司
  - `competitor`: 竞争对手
  - `internal`: 内部公司（总部/分公司）

**可选属性：**
- `location` (string): 位置/地址
- `country` (string): 国家
- `region` (string): 地区
- `domain` (string): 公司域名
- `is_internal` (bool): 是否为内部公司

**系统属性：**user_id, created_at, updated_at

**示例：**
- ✅ `UNIS Transportation` → Company (carrier)
- ✅ `Honey Stinger` → Company (customer)
- ❌ `Customer Support` → Department（不是Company）
- ❌ `IT Department` → Department（不是Company）

---

#### 4. Business（业务）
**定义：**业务实体（工单、项目、订单、服务）

**必需属性：**
- `name` (string): 业务标识符（如 "Ticket #12345", "C1425289"）
- `business_type` (string): 业务类型，可选值：
  - `ticket`: 工单/票据
  - `support`: 技术支持
  - `project`: 项目
  - `order`: 订单
  - `service`: 服务

**可选属性：**
- `description` (string): 业务描述

**系统属性：**user_id, created_at, updated_at

---

### 关系类型

#### Person 相关关系
- `Person -[WORKS_AT]-> Company`: 人物在公司工作（内部员工或外部联系人）
- `Person -[BELONGS_TO_DEPARTMENT]-> Department`: 人物隶属于部门
- `Person -[MANAGES]-> Person`: 人物管理人物
- `Person -[REPORTS_TO]-> Person`: 人物向人物汇报
- `Person -[COLLABORATES_WITH]-> Person`: 人物与人物合作（仅当邮件正文明确提及协作时）
- `Person -[INVOLVED_IN]-> Business`: 人物参与业务
- `Person -[OWNS]-> Business`: 人物拥有业务
- `Person -[REQUESTS]-> Business`: 人物请求业务（如提交工单）

#### Department 相关关系
- `Department -[PART_OF_DEPARTMENT]-> Department`: 部门隶属于上级部门
- `Department -[PART_OF_COMPANY]-> Company`: 部门隶属于公司
- `Department -[DEPARTMENT_INVOLVED_IN]-> Business`: 部门参与业务处理
- `Department -[COLLABORATES_WITH_DEPARTMENT]-> Department`: 部门间协作

#### Company 相关关系
- `Company -[OPERATES]-> Business`: 公司运营业务
- `Company -[PARTNERS_WITH]-> Company`: 公司合作关系
- `Company -[SUBSIDIARY_OF]-> Company`: 子公司关系
- `Company -[COMPETES_WITH]-> Company`: 竞争关系
- `Company -[SUPPLIES_TO]-> Company`: 供应关系
- `Company -[CARRIES_FOR]-> Company`: 承运关系

#### Business 相关关系
- `Business -[RELATED_TO]-> Business`: 业务相关
- `Business -[DEPENDS_ON]-> Business`: 业务依赖
- `Business -[BELONGS_TO]-> Business`: 业务从属（如子任务属于项目）
- `Business -[TRIGGERS]-> Business`: 业务触发

## 后续集成指南

### 集成到邮件处理流程

在 `mirix/server/fastapi_server.py` 的 `process_email_reply_task` 函数中，可以添加：

```python
from email_graph_extraction import extract_and_store_graph

# 在邮件处理完成后
try:
    result = extract_and_store_graph(
        email_data={
            'subject': email_content,
            'body': full_email_content,
            'sender': email_account,
            'id': email_basic_id,
            # ... 其他邮件元数据
        },
        user_id=user_id,
        email_id=email_basic_id
    )
    logger.info(f"图谱抽取完成: {result}")
except Exception as e:
    logger.error(f"图谱抽取失败: {str(e)}")
```

### 异步处理

可以使用线程池或异步任务队列进行异步处理：

```python
from concurrent.futures import ThreadPoolExecutor

# 创建线程池
graph_extraction_executor = ThreadPoolExecutor(max_workers=2)

# 异步处理
graph_extraction_executor.submit(
    extract_and_store_graph,
    email_data=email_data,
    user_id=user_id,
    email_id=email_id
)
```

## 查询图谱

### 使用Neo4j Browser查询

1. 打开Neo4j Browser（通常是 http://localhost:7474）
2. 使用Cypher查询语言查询图谱

**示例查询：**

```cypher
// 查询所有人物节点
MATCH (n:Person)
RETURN n
LIMIT 10

// 查询某个人物的所有关系
MATCH (p:Person {name: "John Smith"})-[r]->(connected)
RETURN p, r, connected

// 查询某个用户的所有图谱
MATCH (n)
WHERE n.user_id = "user-123"
RETURN n

// 查询公司及其运营的业务
MATCH (c:Company)-[r:OPERATES]->(b:Business)
RETURN c, r, b
```

## 用户数据隔离

### 隔离机制

**每个用户都有完全独立的邮箱图谱**，通过以下机制确保数据隔离：

1. **user_id必需参数**: 所有操作都要求提供`user_id`参数，不能为空
2. **节点唯一性**: 节点的唯一性基于`(user_id, name)`组合，确保：
   - 用户A的"John"和用户B的"John"是不同的节点
   - 同一用户内的同名节点会被合并（MERGE操作）
3. **关系隔离**: 关系只能在同一用户的节点之间创建
4. **查询隔离**: 所有查询操作都强制使用`user_id`过滤，确保只返回该用户的数据

### 设置数据库约束

为了确保数据隔离的完整性，建议运行约束设置脚本：

```bash
python -m email_graph_extraction.neo4j_constraints
```

这将创建：
- `user_id`非空约束（确保所有节点都有user_id）
- `(user_id, name)`唯一性约束（确保同一用户内节点唯一）
- `user_id`索引（提高查询性能）

### 验证隔离

```python
from email_graph_extraction.neo4j_manager import Neo4jManager
from email_graph_extraction.neo4j_constraints import verify_user_isolation
from email_graph_extraction.config import NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD

neo4j_manager = Neo4jManager(NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD)

# 验证用户隔离
result = verify_user_isolation(neo4j_manager.driver, 'user-123')
print(f"用户节点数: {result['node_count']}")
print(f"用户关系数: {result['relationship_count']}")
print(f"隔离有效性: {result['isolation_valid']}")

neo4j_manager.close()
```

### 查询用户图谱

```cypher
// 查询用户的所有节点
MATCH (n)
WHERE n.user_id = "user-123"
RETURN n

// 查询用户的所有关系
MATCH (a)-[r]->(b)
WHERE a.user_id = "user-123" AND b.user_id = "user-123"
RETURN a, r, b

// 查询用户的特定类型节点
MATCH (p:Person {user_id: "user-123"})
RETURN p
```

## 注意事项

1. **API Key安全**: 确保不要将API Key提交到版本控制系统
2. **Neo4j性能**: 对于大量数据，建议配置Neo4j的索引和约束（运行neo4j_constraints.py）
3. **错误处理**: 建议在生产环境中添加完善的错误处理和重试机制
4. **数据隔离**: 
   - **user_id是必需参数**，所有操作都必须提供
   - 每个用户的数据完全隔离，不会相互影响
   - 建议在生产环境运行前设置数据库约束以确保隔离完整性
5. **用户ID格式**: 建议使用有意义的用户ID（如邮箱地址的hash或数据库用户ID），避免使用空字符串

## 分类属性说明

### 人物角色（Person.role）

LLM会根据邮件上下文自动识别人物角色：
- **self**: 邮件发送者本人（"我"、"我们"）
- **colleague**: 同事、团队成员（"我们的团队"、"同事"）
- **customer**: 客户（"客户"、"客户公司"）
- **vendor**: 供应商联系人（"供应商"、"供应商联系人"）
- **partner**: 合作伙伴（"合作伙伴"）
- **manager**: 上级管理者（"我的上级"、"经理"）
- **subordinate**: 下属（"我的下属"、"团队成员"）

### 公司类型（Company.company_type）

LLM会根据邮件内容识别公司类型：
- **supplier**: 供应商（提到"供应商"、"supplier"）
- **carrier**: 承运人/物流公司（提到"承运人"、"物流"、"carrier"）
- **customer**: 客户公司（提到"客户公司"）
- **partner**: 合作伙伴公司（提到"合作伙伴公司"）
- **competitor**: 竞争对手（提到"竞争对手"）
- **vendor**: 供应商公司（与supplier类似）

### 业务类型（Business.business_type）

LLM会根据业务名称和上下文识别业务类型：
- **ticket**: 工单、票据（包含"工单"、"ticket"、"#"等）
- **support**: 技术支持、客服支持（包含"支持"、"support"、"技术支持"）
- **project**: 项目（包含"项目"、"project"）
- **order**: 订单（包含"订单"、"order"）
- **service**: 服务（包含"服务"、"service"）
- **contract**: 合同（包含"合同"、"contract"）
- **meeting**: 会议（包含"会议"、"meeting"）
- **task**: 任务（包含"任务"、"task"）

### 地理信息（Company.location/country/region）

如果邮件中提到地理位置，LLM会自动提取：
- **location**: 完整位置信息（如"北京"、"上海市"）
- **country**: 国家（如"中国"、"美国"）
- **region**: 地区（如"华东"、"欧洲"）

### 查询分类属性

在Neo4j中查询特定分类的节点：

```cypher
// 查询所有客户
MATCH (p:Person {role: "customer"})
RETURN p

// 查询所有供应商公司
MATCH (c:Company {company_type: "supplier"})
RETURN c

// 查询所有工单
MATCH (b:Business {business_type: "ticket"})
RETURN b

// 查询位于中国的公司
MATCH (c:Company)
WHERE c.country = "中国" OR c.location CONTAINS "中国"
RETURN c
```

## 故障排除

### Neo4j连接失败

- 检查Neo4j服务是否运行：`neo4j status`
- 检查连接URI、用户名、密码是否正确
- 检查防火墙设置

### OpenAI API调用失败

- 检查API Key是否正确
- 检查账户余额和配额
- 检查网络连接

### 图谱抽取结果为空

- 检查邮件内容是否包含实体信息
- 尝试使用更强大的模型（如GPT-5.1）
- 检查LLMGraphTransformer的配置

### 分类属性未正确识别

- 确保邮件内容明确提到了分类信息（如"客户"、"供应商"等）
- 使用GPT-5.1模型可以获得更好的分类准确性
- 可以在后处理阶段根据关键词进行补充分类

## Agent集成指南

### 当前设计便于Agent集成

当前设计已经考虑了后续作为Agent集成到记忆系统的需求：

1. **用户隔离完善** ✅
   - `user_id`是必需参数，与Agent系统的用户机制兼容
   - 所有操作都自动使用`self.user.id`获取用户ID

2. **Agent工具函数** ✅
   - 已创建`agent_tools.py`，提供Agent可调用的工具函数
   - 符合Mirix Agent工具函数规范
   - 自动处理用户隔离

3. **查询接口** ✅
   - 提供多种查询方式（实体查询、关系查询、子图谱查询）
   - 返回格式化的文本结果，便于Agent使用

### 后续集成步骤

1. **注册工具函数**
   ```python
   # 在mirix/functions/function_sets/中添加
   from email_graph_extraction.agent_tools import (
       query_graph_entities,
       query_graph_relationships,
       get_entity_subgraph,
       extract_graph_from_email,
       get_graph_statistics
   )
   
   GRAPH_EXTRACTION_TOOLS = [
       "query_graph_entities",
       "query_graph_relationships",
       "get_entity_subgraph",
       "extract_graph_from_email",
       "get_graph_statistics"
   ]
   ```

2. **创建Graph Extraction Agent**
   - 在`mirix/schemas/agent.py`中添加`AgentType.graph_extraction_agent`
   - 在`agent_configs.py`中添加配置
   - 创建系统提示词`graph_extraction_agent.txt`

3. **集成到记忆系统**
   - 其他Agent可以通过工具函数查询图谱
   - Chat Agent可以使用图谱信息回答问题
   - Semantic Memory Agent可以查询图谱获取实体关系

### Agent工具函数使用示例

```python
# Agent可以这样调用
result = query_graph_entities(
    self=agent,
    agent_state=agent_state,
    entity_name="John",
    entity_type="Person",
    limit=5
)
# 返回格式化的文本结果
```

详细设计文档请参考：`AGENT_INTEGRATION_DESIGN.md`

## 许可证

本项目遵循项目主许可证。

