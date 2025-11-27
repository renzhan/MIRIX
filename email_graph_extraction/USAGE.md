# Email Graph Extraction - 邮件知识图谱抽取

从企业邮件中抽取知识图谱，围绕人物、公司、业务及其相互关系构建。

## 核心功能

1. **实体抽取**：识别邮件中的人物（Person）、公司（Company）、业务（Business）
2. **关系抽取**：识别实体间的关系（WORKS_AT, COLLABORATES_WITH, INVOLVED_IN等）
3. **用户隔离**：每个用户的图谱数据完全隔离
4. **自然语言检索**：通过自然语言查询图谱（Text-to-Cypher）

## 快速开始

### 1. 安装依赖

```bash
pip install langchain langchain-openai langchain-neo4j langchain-community langchain-experimental neo4j markdownify beautifulsoup4 python-dotenv
```

### 2. 配置环境变量

```bash
# Neo4j 配置
export NEO4J_URI="bolt://localhost:7687"
export NEO4J_USERNAME="neo4j"
export NEO4J_PASSWORD="your_password"

# OpenAI 配置
export OPENAI_API_KEY="your_openai_api_key"
```

### 3. 初始化数据库约束（首次使用）

```bash
python -m email_graph_extraction.neo4j_constraints
```

### 4. 使用示例

```python
from email_graph_extraction import extract_and_store_graph, convert_html_to_markdown

# 步骤1：转换HTML邮件为Markdown
html_email = """<html>...</html>"""  # 你的HTML邮件内容
email_md = convert_html_to_markdown(html_email)

# 步骤2：抽取并存储图谱
result = extract_and_store_graph(
    email_content=email_md,          # Markdown格式的邮件内容
    user_id="user-123",               # 用户ID（必需）
    email_account="you@company.com"   # 你的邮箱账户（必需）
)

print(f"抽取了 {result['nodes_extracted']} 个节点")
print(f"抽取了 {result['relationships_extracted']} 个关系")

# 查看抽取的三元组
graph_doc = result['graph_document']
for node in graph_doc.nodes:
    print(f"节点: {node.id} (类型: {node.type})")

for rel in graph_doc.relationships:
    print(f"{rel.source.id} -[{rel.type}]-> {rel.target.id}")
```

## 自然语言检索

```python
from email_graph_extraction import GraphRetriever

# 创建检索器
retriever = GraphRetriever(user_id="user-123")

# 自然语言查询
result = retriever.query("图谱中有哪些公司？")
print(result['result'])

result = retriever.query("谁在 ABC 公司工作？")
print(result['result'])

result = retriever.query("我自己参与了哪些业务？")
print(result['result'])
```

## API 参考

### extract_and_store_graph

主函数：抽取并存储图谱

**参数：**
- `email_content` (str): 邮件Markdown文本内容（必需）
- `user_id` (str): 用户ID（必需，用于数据隔离）
- `email_account` (str): 用户邮箱账户（必需，用于识别用户本人）
- `neo4j_uri` (str, 可选): Neo4j连接URI
- `neo4j_username` (str, 可选): Neo4j用户名
- `neo4j_password` (str, 可选): Neo4j密码
- `openai_api_key` (str, 可选): OpenAI API Key
- `model_name` (str, 可选): LLM模型名称（默认gpt-5.1）

**返回：**
```python
{
    'nodes_extracted': 10,           # 抽取的节点数
    'relationships_extracted': 5,    # 抽取的关系数
    'nodes_stored': 10,              # 存储的节点数
    'relationships_stored': 5,       # 存储的关系数
    'graph_document': GraphDocument  # 完整的图谱文档对象
}
```

### convert_html_to_markdown

工具函数：将HTML邮件转换为Markdown

**参数：**
- `html_text` (str): HTML文本内容
- `heading_style` (str, 可选): 标题样式，"ATX"（默认）或"SETEXT"

**返回：** Markdown格式的文本

## 图谱 Schema

### 节点类型

**Person（人物）**
- name: 姓名
- email: 邮箱地址
- title: 职位
- department: 部门
- role: 角色（self/colleague/customer/vendor等）

**Company（公司）**
- name: 公司名称
- industry: 行业
- company_type: 公司类型（supplier/carrier/customer等）
- location/country/region: 地理位置

**Business（业务）**
- name: 业务名称
- description: 业务描述
- business_type: 业务类型（ticket/project/order等）
- priority: 优先级（high/medium/low）

### 关系类型

- `WORKS_AT`: 人物在公司工作
- `MANAGES`: 人物管理人物
- `REPORTS_TO`: 人物向人物汇报
- `COLLABORATES_WITH`: 人物与人物合作
- `INVOLVED_IN`: 人物参与业务
- `REQUESTS`: 人物请求业务
- `OPERATES`: 公司运营业务
- `PARTNERS_WITH`: 公司与公司合作
- `SUPPLIES_TO`: 供应商关系
- `BELONGS_TO`: 业务属于业务

## 数据隔离

每个用户的图谱数据完全隔离：
- 所有节点和关系都包含 `user_id` 属性
- Neo4j 数据库层面强制约束
- 查询时自动过滤 `user_id`

## 最佳实践

1. **HTML转Markdown**: 始终先使用 `convert_html_to_markdown` 转换HTML邮件
2. **用户隔离**: 确保每次调用都传入正确的 `user_id`
3. **角色识别**: 必须传入 `email_account` 以准确识别用户本人
4. **数据库初始化**: 首次部署时运行 `neo4j_constraints.py` 设置约束
5. **图谱检索**: 使用 `GraphRetriever` 进行自然语言查询

## 测试

```bash
# 交互式自然语言查询测试
python email_graph_extraction/test_nl_query.py user-123
```

## 故障排查

**问题：关系未抽取到**
- 检查 Prompt 是否包含关系抽取指令
- 确保邮件内容包含明确的关系描述

**问题：查询不到数据**
- 确认 `user_id` 是否一致
- 检查 Neo4j 约束是否正确设置

**问题：性能慢**
- 运行 `neo4j_constraints.py` 创建索引
- 考虑限制查询结果数量（LIMIT）

## 许可证

MIT

