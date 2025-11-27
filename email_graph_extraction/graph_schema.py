"""
图谱Schema定义：定义允许的节点类型和关系类型
"""

from typing import List, Tuple

# 允许的节点类型
ALLOWED_NODES: List[str] = [
    "Person",      # 人物（真实个人）
    "Company",     # 公司
    "Business",    # 业务
    "Department",  # 部门 / 团队（可由组邮箱或HR主数据抽象而来）
]

# 允许的关系类型
# 格式：(源节点类型, 关系类型, 目标节点类型)
ALLOWED_RELATIONSHIPS: List[Tuple[str, str, str]] = [
    # 人物相关关系
    ("Person", "WORKS_AT", "Company"),              # 人物在公司工作（内部员工或外部联系人）
    ("Person", "BELONGS_TO_DEPARTMENT", "Department"),  # 人物隶属于部门（可来自HR或签名）
    ("Person", "MANAGES", "Person"),               # 人物管理人物
    ("Person", "REPORTS_TO", "Person"),            # 人物向人物汇报
    ("Person", "COLLABORATES_WITH", "Person"),     # 人物与人物合作
    ("Person", "INVOLVED_IN", "Business"),         # 人物参与业务
    ("Person", "OWNS", "Business"),                # 人物拥有业务
    ("Person", "REQUESTS", "Business"),            # 人物请求业务（如工单）

    # 部门相关关系
    ("Department", "PART_OF_DEPARTMENT", "Department"),     # 部门隶属于上级部门（部门树）
    ("Department", "PART_OF_COMPANY", "Company"),           # 部门隶属于某公司
    ("Department", "DEPARTMENT_INVOLVED_IN", "Business"),   # 部门整体参与某项业务（聚合得到）
    ("Department", "COLLABORATES_WITH_DEPARTMENT", "Department"),  # 部门之间的协作关系（由人员协作聚合）

    # 公司相关关系
    ("Company", "OPERATES", "Business"),           # 公司运营业务 / 发起业务
    ("Company", "PARTNERS_WITH", "Company"),       # 公司与公司合作
    ("Company", "SUBSIDIARY_OF", "Company"),       # 公司是公司的子公司
    ("Company", "COMPETES_WITH", "Company"),       # 公司与公司竞争
    ("Company", "SUPPLIES_TO", "Company"),         # 公司向公司供应（供应商关系）
    ("Company", "CARRIES_FOR", "Company"),         # 公司为其他公司承运

    # 业务相关关系
    ("Business", "RELATED_TO", "Business"),        # 业务与业务相关
    ("Business", "DEPENDS_ON", "Business"),        # 业务依赖业务
    ("Business", "BELONGS_TO", "Business"),        # 业务属于业务（如子任务属于母项目）
    ("Business", "TRIGGERS", "Business"),          # 业务触发业务
]

# 节点属性定义
NODE_PROPERTIES = {
    "Person": {
        "name": "string",            # 姓名
        "email": "string",           # 邮箱（可选）
        "title": "string",           # 职位（可选）
        "department": "string",      # 部门名称原文（可选，例如签名里的 "IT Department"）
        "role": "string",            # 角色分类：self（自己）、colleague（同事，内部员工）、 customer_contact（客户联系人）、vendor_contact（供应商联系人）、carrier_contact（承运人联系人）、partner_contact（合作伙伴联系人）等
        "user_id": "string",         # 用户ID（用于多租户隔离）
        "created_at": "datetime",    # 创建时间
        "updated_at": "datetime",    # 更新时间
    },
    "Company": {
        "name": "string",            # 公司名称
        "company_type": "string",    # 公司类型：supplier、carrier、customer、partner、competitor、internal 等
        "location": "string",        # 国家/地区（可选，自由文本）
        "country": "string",         # 国家（可选，标准化字段）
        "region": "string",          # 地区（可选）
        "user_id": "string",         # 用户ID
        "created_at": "datetime",
        "updated_at": "datetime",
    },
    "Business": {
        "name": "string",            # 业务名称（ticket号、订单号、项目名等）
        "description": "string",     # 业务描述（可选）
        "business_type": "string",   # 业务类型：ticket、support、project、order、service 等
        "user_id": "string",         # 用户ID
        "created_at": "datetime",
        "updated_at": "datetime",
    },
    "Department": {
        "name": "string",            # 部门/团队名称（例如 "Customer Service", "IT Department"）
        "company_name": "string",    # 所属公司名称（可选，内部部门通常为你们公司名）
        "is_internal": "bool",       # 是否内部部门（根据域名 + 配置判断）
        "user_id": "string",         # 用户ID
        "created_at": "datetime",
        "updated_at": "datetime",
    },
}

# 关系属性定义（所有关系共享）
RELATIONSHIP_PROPERTIES = {
    "user_id": "string",             # 用户ID
    "created_at": "datetime",        # 创建时间
    "updated_at": "datetime",        # 更新时间
    "strength": "string",            # 关系强度（strong/medium/weak），可由出现频率等计算
    "description": "string",         # 关系描述（可选）
}


def get_llm_extractable_properties(node_type: str) -> list[str]:
    """
    获取LLM应该抽取的属性列表（排除系统属性）
    
    系统属性（user_id, created_at, updated_at）由代码自动添加，不需要LLM抽取
    
    Args:
        node_type: 节点类型
    
    Returns:
        LLM应该抽取的属性名称列表
    """
    if node_type not in NODE_PROPERTIES:
        return []
    
    all_properties = NODE_PROPERTIES[node_type]
    
    # 系统属性：由代码自动添加，LLM不需要抽取
    system_properties = {"user_id", "created_at", "updated_at", "name"}
    
    # 返回业务属性
    return [prop for prop in all_properties.keys() if prop not in system_properties]


def get_all_node_properties_for_llm() -> list[str]:
    """
    获取所有节点类型的LLM可抽取属性（合并去重）
    
    Returns:
        所有业务属性的合并列表
    """
    all_props = set()
    for node_type in ALLOWED_NODES:
        all_props.update(get_llm_extractable_properties(node_type))
    return sorted(all_props)


def get_all_relationship_properties_for_llm() -> list[str]:
    """
    获取所有关系类型的LLM可抽取属性（排除系统属性）
    
    Returns:
        关系业务属性列表
    """
    system_properties = {"user_id", "created_at", "updated_at"}
    return [prop for prop in RELATIONSHIP_PROPERTIES.keys() if prop not in system_properties]
