"""
ACE多轮邮件学习测试脚本

数据处理逻辑（简化版）：
1. 从数据库查询邮件会话（每个会话包含多封邮件）
2. 对每个会话：
   - ground_truth = 最后一封sent邮件（专家回复，作为学习目标）
   - history = 除ground_truth外的所有邮件（作为训练输入context）
   - topic = 从所有邮件中提取（包括ground_truth，用于生成question）
   - workflow = 基于所有邮件提取（包括ground_truth）
3. 训练样本构造：
   - question = 基于topic生成的问题
   - context = {"workflow_result": workflow, "history": history}  # 不包含ground_truth
   - ground_truth = 预处理后的专家回复
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import httpx
import pymysql
from pymysql.cursors import DictCursor
from sqlalchemy import create_engine, text  # 用于连接PostgreSQL

# 加载环境变量（从项目根目录加载 .env 文件）
project_root = Path(__file__).parent.parent
load_dotenv(project_root / '.env')

# 添加项目路径
sys.path.insert(0, str(project_root))

from ace import LiteLLMClient, Generator, Reflector, Curator, Playbook, OfflineAdapter, Sample, TaskEnvironment, EnvironmentResult
from mirix.agent.email_evaluation_agent import EmailEvaluationAgent

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_source_db_connection():
    """获取源数据库连接 (MySQL - 用于读取邮件)"""
    db_config = {
        'host': os.getenv('DEV_DB_HOST'),
        'port': int(os.getenv('DEV_DB_PORT', 3306)),
        'user': os.getenv('DEV_DB_USERNAME'),
        'password': os.getenv('DEV_DB_PASSWORD'),
        'database': os.getenv('DEV_DB_DATABASE'),
        'charset': 'utf8mb4',
        'cursorclass': DictCursor
    }
    
    logger.info(f"连接源数据库(MySQL): {db_config['host']}:{db_config['port']}/{db_config['database']}")
    
    try:
        connection = pymysql.connect(**db_config)
        logger.info("✓ 源数据库连接成功")
        return connection
    except Exception as e:
        logger.error(f"✗ 源数据库连接失败: {str(e)}")
        raise


# 全局变量：复用数据库引擎（避免重复创建）
_target_db_engine = None

def get_target_db_engine():
    """获取目标数据库引擎 (PostgreSQL - 用于写入学习结果)"""
    global _target_db_engine
    
    if _target_db_engine is not None:
        return _target_db_engine
    
    pg_uri = os.getenv('MIRIX_PG_URI')
    if not pg_uri:
        # 默认回退（如果环境变量没配）
        logger.warning("未找到 MIRIX_PG_URI，尝试使用默认配置...")
        pg_uri = 'postgresql+pg8000://aiop:G8CKsteyaWb#@pgsql01-share-rds-aliyun.item.pub:5432/mirix_pams'
    
    # 隐藏密码打印日志
    safe_uri = pg_uri.split('@')[-1] if '@' in pg_uri else '***'
    logger.info(f"连接目标数据库(PG): ...@{safe_uri}")
    
    try:
        # 使用SQLAlchemy创建引擎（带连接池配置，支持并发）
        _target_db_engine = create_engine(
            pg_uri, 
            echo=False,
            pool_size=10,  # 连接池大小
            max_overflow=20,  # 最大溢出连接数
            pool_pre_ping=True,  # 连接前ping测试
            pool_recycle=3600  # 1小时后回收连接
        )
        return _target_db_engine
    except Exception as e:
        logger.error(f"✗ 目标数据库连接失败: {str(e)}")
        raise


def init_learning_db():
    """检查学习记录表是否存在（在目标PG数据库）"""
    logger.info("正在检查 ACE 学习记录表 (PostgreSQL)...")
    
    try:
        engine = get_target_db_engine()
        with engine.connect() as conn:
            # 简单检查表是否存在（使用PostgreSQL的information_schema）
            check_sql = text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = 'ace_email_learning_records'
                );
            """)
            result = conn.execute(check_sql)
            table_exists = result.scalar()
            
            if table_exists:
                logger.info("✓ 学习记录表已存在，跳过初始化")
            else:
                logger.warning("⚠️ 学习记录表不存在！请手动执行 database/20251121_create_ace_learning_records.sql")
        
    except Exception as e:
        logger.warning(f"⚠️ 检查表失败: {str(e)}，继续执行（假设表已存在）")


def save_learning_record(record_data: dict):
    """
    保存单条学习记录到目标数据库 (PostgreSQL)
    注意：此函数是线程安全的，支持并发调用
    """
    engine = get_target_db_engine()
    try:
        # 构造SQL (使用SQLAlchemy的text和参数绑定)
        sql = text("""
            INSERT INTO ace_email_learning_records (
                email_id, conversation_id, topic, workflow_data, 
                ground_truth, learned_strategies, final_score
            ) VALUES (:email_id, :conversation_id, :topic, :workflow_data, 
                      :ground_truth, :learned_strategies, :final_score)
        """)
        
        # 准备参数
        params = {
            'email_id': str(record_data['email_id']),  # 确保是字符串
            'conversation_id': record_data.get('conversation_id'),
            'topic': record_data.get('topic'),
            'workflow_data': json.dumps(record_data.get('workflow_data', {}), ensure_ascii=False), 
            'ground_truth': record_data.get('ground_truth'),
            'learned_strategies': json.dumps(record_data.get('learned_strategies', []), ensure_ascii=False),
            'final_score': float(record_data.get('final_score', 0.0))
        }
        
        # 使用独立的连接（连接池会自动管理）
        # 每个并发任务都会从连接池获取独立连接，互不干扰
        with engine.connect() as conn:
            conn.execute(sql, params)
            conn.commit()  # 显式提交事务
        
        logger.info(f"✓ 已保存学习记录到 PG (Email ID: {record_data['email_id']})")
        
    except Exception as e:
        logger.error(f"✗ 保存学习记录失败 (Email ID: {record_data.get('email_id', 'unknown')}): {str(e)}")
        import traceback
        logger.error(traceback.format_exc())


def fetch_email_by_id(email_id: str, user_id: int = 1952974833739087873):
    """
    根据email_id查询单个邮件会话
    
    Args:
        email_id: 邮件ID
        user_id: 用户ID
    
    Returns:
        dict: 包含 email_id, conversation_id, content 的字典，如果不存在返回None
    """
    connection = get_source_db_connection()
    
    try:
        with connection.cursor() as cursor:
            sql = """
                SELECT
                    eb.id AS email_id,
                    eb.conversation_id,
                    eb.mail_type,
                    email_body.content_text
                FROM email_basic eb
                LEFT JOIN email_body ON email_body.email_basic_id = eb.id
                WHERE eb.id = %s
                  AND eb.user_id = %s
                  AND eb.mail_type = 'sent'
            """
            
            cursor.execute(sql, (email_id, user_id))
            result = cursor.fetchone()
            
            if not result:
                logger.warning(f"未找到邮件 ID: {email_id}")
                return None
            
            return {
                'content': result['content_text'],
                'email_id': result['email_id'],
                'conversation_id': result['conversation_id']
            }
            
    except Exception as e:
        logger.error(f"✗ 查询邮件失败: {str(e)}")
        raise
    finally:
        connection.close()


def fetch_email_conversations_from_db(user_id: int = 1952974833739087873, limit: int = 10, offset: int = 0):
    """
    从源数据库(MySQL)查询邮件会话
    """
    connection = get_source_db_connection()
    
    try:
        with connection.cursor() as cursor:
            # 严格执行用户提供的SQL（每条记录的content_text已包含完整会话）
            sql = """
                WITH s AS (
                    SELECT eb.*
                    FROM email_basic eb
                    WHERE eb.user_id = %s
                      AND eb.mail_type = 'sent'
                      AND eb.conversation_id IS NOT NULL
                ),
                r AS (
                    SELECT s.*, ROW_NUMBER() OVER (
                        PARTITION BY s.conversation_id
                        ORDER BY s.sent_date_time DESC
                    ) AS rn
                    FROM s
                ),
                latest_sent AS (
                    SELECT *
                    FROM r
                    WHERE rn = 1
                )
                SELECT
                    ls.id              AS email_id,
                    ls.conversation_id,
                    ls.mail_type,
                    eb.content_text
                FROM latest_sent ls
                LEFT JOIN email_body eb ON eb.email_basic_id = ls.id
                WHERE EXISTS (
                    SELECT 1
                    FROM email_basic x
                    LEFT JOIN email_body xb ON xb.email_basic_id = x.id
                    WHERE x.user_id = ls.user_id
                      AND x.conversation_id = ls.conversation_id
                      AND x.mail_type = 'received'
                      AND COALESCE(x.received_date_time, x.created_date_time, x.created_at)
                          <= COALESCE(ls.sent_date_time, ls.created_date_time, ls.created_at)
                      AND (
                          (x.internet_message_id IS NULL OR ls.internet_message_id IS NULL 
                           OR x.internet_message_id <> ls.internet_message_id)
                          OR (MD5(xb.content_text) IS NULL OR MD5(eb.content_text) IS NULL 
                              OR MD5(xb.content_text) <> MD5(eb.content_text))
                      )
                )
                ORDER BY ls.sent_date_time DESC
                LIMIT %s OFFSET %s
            """
            
            cursor.execute(sql, (user_id, limit, offset))
            results = cursor.fetchall()
            
            if not results:
                logger.info("✓ 未查询到符合条件的邮件会话")
                return []
            
            logger.info(f"✓ 查询到 {len(results)} 个会话")
            
            conversations_list = []
            for row in results:
                conversations_list.append({
                    'content': row['content_text'],
                    'email_id': row['email_id'],
                    'conversation_id': row['conversation_id']
                })
                
            logger.info(f"✓ 准备进行训练，共 {len(conversations_list)} 个会话")
            return conversations_list
            
    except Exception as e:
        logger.error(f"✗ 查询邮件数据失败: {str(e)}")
        raise
    finally:
        connection.close()


class EmailTaskEnvironment(TaskEnvironment):
    """ACE训练环境（使用EmailEvaluationAgent评估）"""
    
    def __init__(self, evaluation_agent: EmailEvaluationAgent):
        self.evaluation_agent = evaluation_agent
    
    def evaluate(self, sample: Sample, generated_output) -> EnvironmentResult:
        """
        ACE要求实现的评估方法
        """
        # 解析context（包含原始邮件、workflow和历史）
        email_context = json.loads(sample.context) if isinstance(sample.context, str) else sample.context
        
        # 获取生成的最终答案
        final_answer = generated_output.final_answer
        
        # 调用评估Agent（同步调用）
        result = self.evaluation_agent.evaluate_reply(
            generated_reply=final_answer,
            ground_truth_reply=sample.ground_truth,
            email_context=email_context
        )
        
        return result


async def call_workflow_extract_api(email_content: str, email_account: str = "test@example.com") -> dict:
    """调用真实的 /workflow/extract 接口获取workflow"""
    url = "https://aiop-dev.item.pub/pams/workflow/extract"
    
    payload = {
        "content": email_content,
        "email_account": email_account
    }
    
    try:
        # 超时设置为180秒（3分钟）
        async with httpx.AsyncClient(timeout=180.0) as client:
            logger.info(f"调用workflow提取API: {url}")
            
            response = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            response.raise_for_status()
            result = response.json()
            
            logger.info(f"✓ workflow提取成功")
            return result.get("workflow_result", result)
    
    except httpx.TimeoutException as e:
        logger.warning(f"  workflow提取超时（180秒），跳过")
        return {"workflow_type": "unknown", "reasoning": "API调用超时"}
    
    except httpx.HTTPError as e:
        logger.warning(f"  workflow提取失败: {str(e)}，跳过")
        return {"workflow_type": "unknown", "reasoning": f"API调用失败: {str(e)}"}
    
    except Exception as e:
        logger.warning(f"  workflow提取异常: {str(e)}，跳过")
        return {"workflow_type": "unknown", "reasoning": f"API调用异常: {str(e)}"}


async def preprocess_ground_truth_to_steps(natural_text: str, llm_client) -> str:
    """使用LLM将自然对话格式的邮件转换为严格步骤化格式"""
    preprocessing_prompt = f"""请将以下自然对话风格的邮件回复，改写为严格的步骤化格式。

严格要求：
1. **必须保留所有技术细节**：
   - 人名（如Frances、Anthony、Jeff、Eaden等）
   - 系统名（如WMS、EDI、API、CubeShip等）
   - 配置值（如ISA ID、PO号码等）
   - 团队名（如Joliet团队、B-Solutions等）

2. **格式要求**：
   - 使用"第一步：..."、"第二步：..."、"第三步：..."格式
   - 每个步骤用一句完整的话描述要做的事情
   - 步骤之间空一行
   - 不要使用"-"、"•"等列表符号
   - 不要在步骤下再分子要点

3. **保持原有内容**：
   - 保持开头和结尾的问候语
   - 保持时间预估
   - 保持签名

原始邮件：
{natural_text}

请严格按照上述格式要求输出改写后的邮件，不要添加任何解释或额外说明。"""
    
    logger.info("\n[预处理] 使用LLM将自然对话转换为步骤化格式...")
    response = llm_client.complete(preprocessing_prompt)
    processed_text = response.text.strip()
    
    logger.info("=" * 80)
    logger.info("✓ 预处理完成！LLM转换后的邮件内容：")
    logger.info("=" * 80)
    logger.info(processed_text)
    logger.info("=" * 80)
    
    return processed_text


def summarize_long_email(raw_emails: str, llm_client) -> str:
    """对超长邮件进行智能总结，保留关键信息"""
    logger.info(f"  邮件过长（{len(raw_emails)}字符），先进行智能总结...")
    
    summary_prompt = f"""这是一封很长的邮件线程。请总结关键信息，保持结构清晰。

邮件内容：
{raw_emails[:50000]}

总结要求：
1. 保留最新的回复内容（完整）
2. 保留所有人名、公司名、系统名、订单号、配置值等关键信息
3. 保留时间线和对话流程
4. 压缩重复内容和冗余的签名/声明
5. 保持"发件人:"/"From:"等分隔符
6. 目标长度：不超过20000字符

请输出总结后的邮件内容："""
    
    try:
        response = llm_client.complete(summary_prompt)
        summarized = response.text.strip()
        logger.info(f"  ✓ 总结完成，压缩到 {len(summarized)} 字符")
        return summarized
    except Exception as e:
        logger.warning(f"  总结失败: {str(e)}，使用截断方式")
        return raw_emails[:30000]


def process_conversation_with_llm(emails_data: list, llm_client, retry_count: int = 0) -> dict:
    """使用LLM智能处理邮件会话"""
    if not emails_data:
        raise ValueError("邮件数据为空")
    
    raw_emails = emails_data[0] if emails_data else ""
    
    logger.info(f"\n[LLM处理会话] 邮件内容长度: {len(raw_emails)} 字符")
    
    if len(raw_emails) > 30000:
        raw_emails = summarize_long_email(raw_emails, llm_client)
    
    processing_prompt = f"""你是专业的邮件分析助手。请从这封已发送的邮件中提取训练所需的信息。

【邮件内容】
{raw_emails}

【任务说明】
这封邮件包含：
- 最新回复内容（开头到第一个"发件人:"/"From:"之前）
- 历史邮件对话（从"发件人:"/"From:"开始的部分）

【输出要求 - 必须严格遵守】
你必须输出完整的XML格式，包含全部三个标签，每个标签都必须有实际内容：

<output>
<ground_truth>
[提取最新回复内容，去除签名但保留所有技术细节：人名、系统名、订单号、配置值等]
</ground_truth>
<history>
[提取历史邮件对话。如果找不到"发件人:"/"From:"分隔符，则填写"无历史对话"]
</history>
<topic>
[从邮件内容中提取核心主题，10-20字，必须描述具体业务场景。禁止使用"邮件处理"、"邮件回复"等泛化词]
</topic>
</output>

【示例】
邮件：Hi team, I've resolved the ARN issue. Testing can begin.

发件人: John <john@example.com>
主题: ARN Issue
Can you check the ARN mapping?

正确输出：
<output>
<ground_truth>
Hi team, I've resolved the ARN issue. Testing can begin.
</ground_truth>
<history>
发件人: John <john@example.com>
主题: ARN Issue
Can you check the ARN mapping?
</history>
<topic>
ARN映射问题解决通知
</topic>
</output>

【处理步骤】
1. 识别"发件人:"/"From:"分隔符位置
2. 分隔符之前 → ground_truth（去除签名）
3. 分隔符之后 → history（去除冗余声明）
4. 从整体内容提取具体的业务主题 → topic

现在开始处理上述邮件，必须输出完整的三个XML标签："""
    
    try:
        response = llm_client.complete(processing_prompt)
        result_text = response.text.strip()
        
        logger.debug(f"LLM返回内容（前1000字符）:\n{result_text[:1000]}")
        
        import re
        ground_truth_match = re.search(r'<ground_truth>(.*?)</ground_truth>', result_text, re.DOTALL)
        history_match = re.search(r'<history>(.*?)</history>', result_text, re.DOTALL)
        topic_match = re.search(r'<topic>(.*?)</topic>', result_text, re.DOTALL)
        
        missing_fields = []
        if not ground_truth_match: missing_fields.append("ground_truth")
        if not history_match: missing_fields.append("history")
        if not topic_match: missing_fields.append("topic")
        
        if missing_fields and retry_count == 0:
            logger.warning(f"  缺少必填字段: {missing_fields}，重试一次...")
            return process_conversation_with_llm(emails_data, llm_client, retry_count=1)
        
        if missing_fields:
            logger.error(f"LLM返回内容:\n{result_text}")
            raise ValueError(f"LLM返回格式错误，缺少必填字段: {missing_fields}")
        
        ground_truth = ground_truth_match.group(1).strip()
        history = history_match.group(1).strip()
        topic = topic_match.group(1).strip()
        
        if not ground_truth:
            raise ValueError("ground_truth不能为空")
        
        if not history:
            logger.warning("  history为空，要求LLM填充...")
            if retry_count == 0:
                return process_conversation_with_llm(emails_data, llm_client, retry_count=1)
            history = "无历史对话"
        
        generic_topics = ["邮件处理", "邮件回复", "邮件", "处理", "回复"]
        if topic in generic_topics:
            logger.warning(f"  topic '{topic}' 是泛化词，要求重新生成...")
            if retry_count == 0:
                return process_conversation_with_llm(emails_data, llm_client, retry_count=1)
        
        logger.info(f"  ✓ 提取成功")
        logger.info(f"  主题: {topic}")
        
        return {
            'ground_truth': ground_truth,
            'history': history,
            'topic': topic
        }
        
    except Exception as e:
        logger.error(f"✗ LLM处理失败: {str(e)}")
        if retry_count < 1:
            logger.info("  尝试重试一次...")
            return process_conversation_with_llm(emails_data, llm_client, retry_count=1)
        raise


        raise


async def process_single_email(
    conv_data: dict,
    idx: int,
    total: int,
    llm_client,
    eval_agent,
    semaphore: asyncio.Semaphore
):
    """
    处理单个邮件的完整流程（并行版本）
    
    Args:
        conv_data: 包含 email_id, conversation_id, content
        idx: 当前索引
        total: 总数
        llm_client: LLM客户端
        eval_agent: 评估Agent
        semaphore: 并发控制信号量
    
    Returns:
        dict: 处理结果 {'success': bool, 'strategies': list, 'error': str}
    """
    async with semaphore:  # 控制并发数
        email_id = conv_data['email_id']
        conversation_id = conv_data['conversation_id']
        email_content = conv_data['content']
        
        logger.info(f"\n{'='*60}")
        logger.info(f"正在处理会话 {idx}/{total} (Email ID: {email_id})")
        logger.info(f"{'='*60}")
        
        try:
            # --- 步骤 A: 预处理 ---
            processed = process_conversation_with_llm([email_content], llm_client)
            
            topic = processed['topic']
            history = processed['history']
            ground_truth_raw = processed['ground_truth']
            
            logger.info(f"  [{idx}] 主题: {topic}")
            
            # 调用workflow API
            workflow_result = await call_workflow_extract_api(topic, "shelia.sun@item.com")
            
            # 构造question
            specific_question = f"{topic}需要联系哪些人？需要检查哪些系统？需要执行哪些操作？"
            
            # 预处理ground_truth
            ground_truth_processed = await preprocess_ground_truth_to_steps(
                ground_truth_raw, 
                llm_client
            )
            
            # 构造单个样本
            sample = Sample(
                question=specific_question,
                context=json.dumps({
                    "workflow_result": workflow_result,
                    "history": history
                }, ensure_ascii=False),
                ground_truth=ground_truth_processed
            )
            
            # --- 步骤 B: 单样本微调 (5轮) ---
            logger.info(f"  [{idx}] >> 开始训练 5 轮...")
            
            # 每个任务使用独立的Playbook（避免并发冲突）
            local_playbook = Playbook()
            generator = Generator(llm_client)
            reflector = Reflector(llm_client)
            curator = Curator(llm_client)
            task_env = EmailTaskEnvironment(eval_agent)
            
            adapter = OfflineAdapter(
                playbook=local_playbook,
                generator=generator,
                reflector=reflector,
                curator=curator
            )
            
            # 运行训练
            results = adapter.run(
                samples=[sample],
                environment=task_env,
                epochs=5
            )
            
            # 获取最后一次评估的得分
            last_result = results[-1]
            final_score = last_result.environment_result.metrics.get('score', 0)
            logger.info(f"  [{idx}] >> 训练完成，得分: {final_score:.2f}")
            
            # --- 步骤 C: 提取策略 ---
            new_bullets = []
            if local_playbook._bullets:
                logger.info(f"  [{idx}] >> 本次产生 {len(local_playbook._bullets)} 条策略")
                for bullet in local_playbook._bullets.values():
                    bullet_dict = {
                        "id": bullet.id,
                        "section": bullet.section,
                        "content": bullet.content,
                        "helpful": bullet.helpful,
                        "harmful": bullet.harmful
                    }
                    new_bullets.append(bullet_dict)
            
            # --- 步骤 D: 保存到数据库 ---
            record_data = {
                'email_id': email_id,
                'conversation_id': conversation_id,
                'topic': topic,
                'workflow_data': workflow_result,
                'ground_truth': ground_truth_processed,
                'learned_strategies': new_bullets,
                'final_score': final_score
            }
            
            save_learning_record(record_data)
            logger.info(f"  [{idx}] ✓ 处理完成并已保存")
            
            return {
                'success': True,
                'strategies': new_bullets,
                'email_id': email_id,
                'score': final_score
            }
            
        except Exception as e:
            logger.error(f"  [{idx}] ✗ 处理失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                'success': False,
                'strategies': [],
                'email_id': email_id,
                'error': str(e)
            }


async def test_multi_turn_email_learning(conversations_list: list, max_concurrent: int = 3): 
    """
    并行处理邮件学习
    
    Args:
        conversations_list: 邮件会话列表
        max_concurrent: 最大并发数（默认3，可根据API限制调整）
    """
    logger.info("=" * 60)
    logger.info(f"开始 ACE 并行邮件学习（实时入库模式，并发数={max_concurrent}）")
    logger.info("=" * 60)
    
    # 初始化数据库表
    init_learning_db()
    
    # 验证输入数据
    if not conversations_list:
        raise ValueError("conversations_list 不能为空，请提供邮件会话列表")
    
    logger.info(f"收到 {len(conversations_list)} 个邮件会话")
    
    # 1. 检查环境配置
    logger.info("\n[1/5] 检查环境配置...")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("未找到 OPENAI_API_KEY，请在 .env 文件中设置")
        return
    logger.info("✓ API Key 已配置")
    
    # 2. 初始化 LLM 客户端
    logger.info("\n[2/5] 初始化 LLM 客户端...")
    llm_client = LiteLLMClient(
        model="gpt-4o",
        temperature=0.3,
        max_tokens=2048
    )
    logger.info("✓ LLM 客户端初始化完成")
    
    # 🔧 Monkey patch ACE的JSON解析
    import ace.roles
    original_safe_json_loads = ace.roles._safe_json_loads
    def patched_safe_json_loads(text: str):
        cleaned = text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        return original_safe_json_loads(cleaned)
    
    ace.roles._safe_json_loads = patched_safe_json_loads
    logger.info("✓ 已应用JSON解析补丁")
    
    # 3. 创建评估环境
    logger.info("\n[3/5] 创建训练环境...")
    eval_agent = EmailEvaluationAgent(llm_client=llm_client)
    logger.info("✓ 训练环境创建完成")
    
    # 4. 并行处理邮件会话
    logger.info(f"\n[4/5] 开始并行处理 {len(conversations_list)} 个邮件会话（并发数={max_concurrent}）...")
    
    # 创建并发控制信号量
    semaphore = asyncio.Semaphore(max_concurrent)
    
    # 创建所有任务
    tasks = [
        process_single_email(conv_data, idx, len(conversations_list), llm_client, eval_agent, semaphore)
        for idx, conv_data in enumerate(conversations_list, 1)
    ]
    
    # 并发执行所有任务
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 5. 统计结果并合并策略
    success_count = 0
    fail_count = 0
    all_strategies = []
    
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"任务 {i+1} 异常: {result}")
            fail_count += 1
        elif result.get('success'):
            success_count += 1
            all_strategies.extend(result.get('strategies', []))
        else:
            fail_count += 1
    
    # 6. 结束处理
    logger.info(f"\n{'='*60}")
    logger.info(f"所有会话处理完成")
    logger.info(f"{'='*60}")
    logger.info(f"✓ 成功: {success_count}")
    logger.info(f"✗ 失败: {fail_count}")
    logger.info(f"✓ 总共产生策略: {len(all_strategies)} 条")
    
    # 7. 保存最终合并的Playbook（可选）
    if all_strategies:
        logger.info("\n保存最终策略汇总...")
        final_playbook = Playbook()
        # 注意：这里只是汇总，实际的策略已经存在数据库中了
        logger.info(f"✓ 策略已汇总（实际策略已保存在数据库中）")
    
    return {'success_count': success_count, 'fail_count': fail_count, 'total_strategies': len(all_strategies)}


# 保留旧的串行版本作为备用（如果需要的话）
async def test_multi_turn_email_learning_serial(conversations_list: list): 
    """串行版本（旧版本，保留作为备用）"""
    logger.info("=" * 60)
    logger.info("开始 ACE 逐个邮件学习（串行模式）")
    logger.info("=" * 60)
    
    # 初始化数据库表
    init_learning_db()
    
    # 验证输入数据
    if not conversations_list:
        raise ValueError("conversations_list 不能为空，请提供邮件会话列表")
    
    logger.info(f"收到 {len(conversations_list)} 个邮件会话")
    
    # 1. 检查环境配置
    logger.info("\n[1/5] 检查环境配置...")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("未找到 OPENAI_API_KEY，请在 .env 文件中设置")
        return
    logger.info("✓ API Key 已配置")
    
    # 2. 初始化 LLM 客户端
    logger.info("\n[2/5] 初始化 LLM 客户端...")
    llm_client = LiteLLMClient(
        model="gpt-4o",
        temperature=0.3,
        max_tokens=2048
    )
    logger.info("✓ LLM 客户端初始化完成")
    
    # 🔧 Monkey patch ACE的JSON解析
    import ace.roles
    original_safe_json_loads = ace.roles._safe_json_loads
    def patched_safe_json_loads(text: str):
        cleaned = text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        return original_safe_json_loads(cleaned)
    
    ace.roles._safe_json_loads = patched_safe_json_loads
    logger.info("✓ 已应用JSON解析补丁")
    
    # 3. 创建评估环境和全局Playbook
    logger.info("\n[3/5] 创建训练环境...")
    eval_agent = EmailEvaluationAgent(llm_client=llm_client)
    task_env = EmailTaskEnvironment(eval_agent)
    
    # 全局Playbook，用于累积策略
    global_playbook = Playbook()
    
    # 初始化ACE组件
    generator = Generator(llm_client)
    reflector = Reflector(llm_client)
    curator = Curator(llm_client)
    
    logger.info("✓ 训练环境创建完成")
    
    # 4. 逐个处理邮件会话
    logger.info(f"\n[4/5] 开始逐个处理 {len(conversations_list)} 个邮件会话...")
    
    success_count = 0
    fail_count = 0
    
    for idx, conv_data in enumerate(conversations_list, 1):
        email_id = conv_data['email_id']
        conversation_id = conv_data['conversation_id']
        email_content = conv_data['content']
        
        logger.info(f"\n{'='*60}")
        logger.info(f"正在处理会话 {idx}/{len(conversations_list)} (Email ID: {email_id})")
        logger.info(f"{'='*60}")
        
        try:
            # --- 步骤 A: 预处理 ---
            processed = process_conversation_with_llm([email_content], llm_client)
            
            topic = processed['topic']
            history = processed['history']
            ground_truth_raw = processed['ground_truth']
            
            logger.info(f"  主题: {topic}")
            
            # 调用workflow API
            workflow_result = await call_workflow_extract_api(topic, "shelia.sun@item.com")
            
            # 构造question
            specific_question = f"{topic}需要联系哪些人？需要检查哪些系统？需要执行哪些操作？"
            
            # 预处理ground_truth
            ground_truth_processed = await preprocess_ground_truth_to_steps(
                ground_truth_raw, 
                llm_client
            )
            
            # 构造单个样本
            sample = Sample(
                question=specific_question,
                context=json.dumps({
                    "workflow_result": workflow_result,
                    "history": history
                }, ensure_ascii=False),
                ground_truth=ground_truth_processed
            )
            
            # --- 步骤 B: 单样本微调 (5轮) ---
            logger.info(f"  >> 开始针对该样本训练 5 轮...")
            
            initial_strategies = set(global_playbook._bullets.keys())
            
            adapter = OfflineAdapter(
                playbook=global_playbook,
                generator=generator,
                reflector=reflector,
                curator=curator
            )
            
            results = adapter.run(
                samples=[sample], 
                environment=task_env,
                epochs=5
            )
            
            last_result = results[-1]
            final_score = last_result.environment_result.metrics.get('score', 0)
            logger.info(f"  >> 训练完成，最终得分: {final_score:.2f}")
            
            # --- 步骤 C: 计算增量策略并入库 ---
            current_strategies = set(global_playbook._bullets.keys())
            new_strategy_ids = current_strategies - initial_strategies
            
            new_bullets = []
            if new_strategy_ids:
                logger.info(f"  >> 本次新增 {len(new_strategy_ids)} 条策略:")
                for bid in new_strategy_ids:
                    bullet = global_playbook._bullets[bid]
                    bullet_dict = {
                        "id": bullet.id,
                        "section": bullet.section,
                        "content": bullet.content,
                        "helpful": bullet.helpful,
                        "harmful": bullet.harmful
                    }
                    new_bullets.append(bullet_dict)
                    logger.info(f"     + [{bullet.section}] {bullet.content[:50]}...")
            else:
                logger.info(f"  >> 本次未产生新策略")
            
            # --- 步骤 D: 保存到数据库 ---
            record_data = {
                'email_id': email_id,
                'conversation_id': conversation_id,
                'topic': topic,
                'workflow_data': workflow_result,
                'ground_truth': ground_truth_processed,
                'learned_strategies': new_bullets,
                'final_score': final_score
            }
            
            save_learning_record(record_data)
            success_count += 1
            
        except Exception as e:
            logger.error(f"✗ 会话 {idx} 处理失败: {str(e)}")
            fail_count += 1
            continue
    
    # 5. 结束处理
    logger.info(f"\n{'='*60}")
    logger.info(f"所有会话处理完成")
    logger.info(f"{'='*60}")
    logger.info(f"✓ 成功: {success_count}")
    logger.info(f"✗ 失败: {fail_count}")
    logger.info(f"✓ 最终策略总数: {len(global_playbook._bullets)}")
    
    # 6. 保存最终完整的Playbook
    logger.info("\n保存最终完整Playbook...")
    playbook_path = "trained_email_playbook_final.json"
    global_playbook.save_to_file(playbook_path)
    logger.info(f"✓ Playbook已保存到: {playbook_path}")
    
    return global_playbook


async def main_with_database(user_id: int = 1952974833739087873, limit: int = 10, offset: int = 0, max_concurrent: int = 3):
    """
    从数据库读取邮件会话并进行ACE训练（并行版本）
    
    Args:
        user_id: 用户ID
        limit: 查询的会话数量限制
        offset: 查询的偏移量
        max_concurrent: 最大并发数（默认3）
    """
    print("\n" + "=" * 80)
    print("ACE 批量邮件学习脚本（从数据库读取，并行模式）")
    print("=" * 80)
    
    # 1. 从数据库查询邮件会话
    print("\n[步骤1] 从数据库查询邮件会话...")
    print(f"  用户ID: {user_id}")
    print(f"  会话数量: {limit}")
    print(f"  偏移量: {offset}")
    print(f"  并发数: {max_concurrent}")
    try:
        conversations_list = fetch_email_conversations_from_db(user_id=user_id, limit=limit, offset=offset)
        
        if not conversations_list:
            print("✗ 未查询到任何邮件会话")
            return
        
        print(f"✓ 成功查询到 {len(conversations_list)} 个会话")
        
    except Exception as e:
        print(f"✗ 数据库查询失败: {str(e)}")
        return
    
    # 2. 调用ACE训练（并行）
    print("\n[步骤2] 开始ACE并行训练...")
    try:
        result = await test_multi_turn_email_learning(conversations_list, max_concurrent=max_concurrent)
        print(f"\n✓ 训练完成！")
        print(f"  成功: {result['success_count']} 个")
        print(f"  失败: {result['fail_count']} 个")
        print(f"  总策略: {result['total_strategies']} 条")
        
    except Exception as e:
        print(f"✗ 训练失败: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # 配置训练参数
    USER_ID = 1952974833739087873
    LIMIT = 102  # 训练全部会话
    OFFSET = 0
    MAX_CONCURRENT = 1  # 最大并发数（降低到1避免Workflow API超时，因为API服务器可能无法处理并发请求）
    
    print("=" * 80)
    print("开始 ACE 邮件学习训练（并行模式）")
    print("=" * 80)
    print(f"用户ID: {USER_ID}")
    print(f"会话数量: {LIMIT}")
    print(f"偏移量: {OFFSET}")
    print(f"并发数: {MAX_CONCURRENT}")
    print("=" * 80)
    
    asyncio.run(main_with_database(
        user_id=USER_ID,
        limit=LIMIT,
        offset=OFFSET,
        max_concurrent=MAX_CONCURRENT
    ))
