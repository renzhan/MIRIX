"""
重新处理单个邮件ID的学习任务
用于复现和修复之前失败或超时的邮件处理
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
from sqlalchemy import create_engine, text

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
        return connection
    except Exception as e:
        logger.error(f"✗ 源数据库连接失败: {str(e)}")
        raise


# 全局变量：复用数据库引擎
_target_db_engine = None

def get_target_db_engine():
    """获取目标数据库引擎 (PostgreSQL - 用于写入学习结果)"""
    global _target_db_engine
    
    if _target_db_engine is not None:
        return _target_db_engine
    
    pg_uri = os.getenv('MIRIX_PG_URI')
    if not pg_uri:
        pg_uri = 'postgresql+pg8000://aiop:G8CKsteyaWb#@pgsql01-share-rds-aliyun.item.pub:5432/mirix_pams'
    
    safe_uri = pg_uri.split('@')[-1] if '@' in pg_uri else '***'
    logger.info(f"连接目标数据库(PG): ...@{safe_uri}")
    
    try:
        _target_db_engine = create_engine(
            pg_uri, 
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=3600
        )
        return _target_db_engine
    except Exception as e:
        logger.error(f"✗ 目标数据库连接失败: {str(e)}")
        raise


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
            
            logger.info(f"✓ 找到邮件 ID: {email_id}, 内容长度: {len(result['content_text'] or '')} 字符")
            
            return {
                'content': result['content_text'],
                'email_id': str(result['email_id']),
                'conversation_id': result['conversation_id']
            }
            
    except Exception as e:
        logger.error(f"✗ 查询邮件失败: {str(e)}")
        raise
    finally:
        connection.close()


async def call_workflow_extract_api(email_content: str, email_account: str = "shelia.sun@item.com") -> dict:
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


def process_conversation_with_llm(emails_data: list, llm_client, retry_count: int = 0) -> dict:
    """使用LLM智能处理邮件会话"""
    if not emails_data:
        raise ValueError("邮件数据为空")
    
    raw_emails = emails_data[0] if emails_data else ""
    
    logger.info(f"\n[LLM处理会话] 邮件内容长度: {len(raw_emails)} 字符")
    
    # 如果邮件过长，先进行智能总结
    if len(raw_emails) > 30000:
        logger.warning(f"  邮件过长（{len(raw_emails)}字符），需要总结...")
        # 这里简化处理，直接截断
        raw_emails = raw_emails[:30000]
    
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

现在开始处理上述邮件，必须输出完整的三个XML标签："""
    
    try:
        response = llm_client.complete(processing_prompt)
        result_text = response.text.strip()
        
        import re
        ground_truth_match = re.search(r'<ground_truth>(.*?)</ground_truth>', result_text, re.DOTALL)
        history_match = re.search(r'<history>(.*?)</history>', result_text, re.DOTALL)
        topic_match = re.search(r'<topic>(.*?)</topic>', result_text, re.DOTALL)
        
        if not ground_truth_match or not history_match or not topic_match:
            if retry_count == 0:
                logger.warning("  LLM返回格式不完整，重试一次...")
                return process_conversation_with_llm(emails_data, llm_client, retry_count=1)
            raise ValueError("LLM返回格式错误")
        
        ground_truth = ground_truth_match.group(1).strip()
        history = history_match.group(1).strip()
        topic = topic_match.group(1).strip()
        
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


async def preprocess_ground_truth_to_steps(natural_text: str, llm_client) -> str:
    """使用LLM将自然对话格式的邮件转换为严格步骤化格式"""
    preprocessing_prompt = f"""请将以下自然对话风格的邮件回复，改写为严格的步骤化格式。

严格要求：
1. **必须保留所有技术细节**：人名、系统名、订单号、配置值等
2. **格式要求**：使用"第一步：..."、"第二步：..."、"第三步：..."格式
3. **保持原有内容**：保持开头和结尾的问候语、时间预估、签名

原始邮件：
{natural_text}

请严格按照上述格式要求输出改写后的邮件，不要添加任何解释或额外说明。"""
    
    logger.info("\n[预处理] 使用LLM将自然对话转换为步骤化格式...")
    response = llm_client.complete(preprocessing_prompt)
    processed_text = response.text.strip()
    
    logger.info("✓ 预处理完成")
    return processed_text


def save_learning_record(record_data: dict):
    """保存单条学习记录到目标数据库 (PostgreSQL) - 如果存在则更新，不存在则插入"""
    engine = get_target_db_engine()
    try:
        email_id = str(record_data['email_id'])
        
        # 先检查是否存在
        check_sql = text("SELECT id FROM ace_email_learning_records WHERE email_id = :email_id")
        
        with engine.connect() as conn:
            result = conn.execute(check_sql, {'email_id': email_id})
            exists = result.fetchone() is not None
            
            if exists:
                # 更新现有记录
                update_sql = text("""
                    UPDATE ace_email_learning_records SET
                        conversation_id = :conversation_id,
                        topic = :topic,
                        workflow_data = :workflow_data,
                        ground_truth = :ground_truth,
                        learned_strategies = :learned_strategies,
                        final_score = :final_score,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE email_id = :email_id
                """)
                
                params = {
                    'email_id': email_id,
                    'conversation_id': record_data.get('conversation_id'),
                    'topic': record_data.get('topic'),
                    'workflow_data': json.dumps(record_data.get('workflow_data', {}), ensure_ascii=False), 
                    'ground_truth': record_data.get('ground_truth'),
                    'learned_strategies': json.dumps(record_data.get('learned_strategies', []), ensure_ascii=False),
                    'final_score': float(record_data.get('final_score', 0.0))
                }
                
                conn.execute(update_sql, params)
                logger.info(f"✓ 已更新学习记录 (Email ID: {email_id})")
            else:
                # 插入新记录
                insert_sql = text("""
                    INSERT INTO ace_email_learning_records (
                        email_id, conversation_id, topic, workflow_data, 
                        ground_truth, learned_strategies, final_score
                    ) VALUES (:email_id, :conversation_id, :topic, :workflow_data, 
                              :ground_truth, :learned_strategies, :final_score)
                """)
                
                params = {
                    'email_id': email_id,
                    'conversation_id': record_data.get('conversation_id'),
                    'topic': record_data.get('topic'),
                    'workflow_data': json.dumps(record_data.get('workflow_data', {}), ensure_ascii=False), 
                    'ground_truth': record_data.get('ground_truth'),
                    'learned_strategies': json.dumps(record_data.get('learned_strategies', []), ensure_ascii=False),
                    'final_score': float(record_data.get('final_score', 0.0))
                }
                
                conn.execute(insert_sql, params)
                logger.info(f"✓ 已插入新学习记录 (Email ID: {email_id})")
            
            conn.commit()
        
    except Exception as e:
        logger.error(f"✗ 保存学习记录失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())


class EmailTaskEnvironment(TaskEnvironment):
    """ACE训练环境（使用EmailEvaluationAgent评估）"""
    
    def __init__(self, evaluation_agent: EmailEvaluationAgent):
        self.evaluation_agent = evaluation_agent
    
    def evaluate(self, sample: Sample, generated_output) -> EnvironmentResult:
        email_context = json.loads(sample.context) if isinstance(sample.context, str) else sample.context
        final_answer = generated_output.final_answer
        
        result = self.evaluation_agent.evaluate_reply(
            generated_reply=final_answer,
            ground_truth_reply=sample.ground_truth,
            email_context=email_context
        )
        
        return result


async def retry_single_email(email_id: str, user_id: int = 1952974833739087873):
    """
    重新处理单个邮件ID
    
    Args:
        email_id: 要重新处理的邮件ID
        user_id: 用户ID
    """
    logger.info("=" * 80)
    logger.info(f"重新处理邮件 ID: {email_id}")
    logger.info("=" * 80)
    
    # 1. 查询邮件
    logger.info("\n[1/6] 查询邮件内容...")
    conv_data = fetch_email_by_id(email_id, user_id)
    
    if not conv_data:
        logger.error(f"✗ 未找到邮件 ID: {email_id}")
        return
    
    logger.info(f"✓ 邮件查询成功")
    logger.info(f"  Email ID: {conv_data['email_id']}")
    logger.info(f"  Conversation ID: {conv_data['conversation_id']}")
    logger.info(f"  内容长度: {len(conv_data['content'])} 字符")
    
    # 2. 初始化LLM客户端
    logger.info("\n[2/6] 初始化LLM客户端...")
    llm_client = LiteLLMClient(
        model="gpt-4o",
        temperature=0.3,
        max_tokens=2048
    )
    logger.info("✓ LLM客户端初始化完成")
    
    # 3. 处理邮件会话
    logger.info("\n[3/6] 使用LLM处理邮件会话...")
    try:
        processed = process_conversation_with_llm([conv_data['content']], llm_client)
        topic = processed['topic']
        history = processed['history']
        ground_truth_raw = processed['ground_truth']
        
        logger.info(f"✓ 处理成功")
        logger.info(f"  主题: {topic}")
        logger.info(f"  Ground Truth长度: {len(ground_truth_raw)} 字符")
        logger.info(f"  History长度: {len(history)} 字符")
    except Exception as e:
        logger.error(f"✗ LLM处理失败: {str(e)}")
        return
    
    # 4. 调用Workflow API
    logger.info("\n[4/6] 调用Workflow提取API...")
    workflow_result = await call_workflow_extract_api(topic, "shelia.sun@item.com")
    
    logger.info(f"✓ Workflow提取完成")
    logger.info(f"  Workflow类型: {workflow_result.get('workflow_type', 'unknown')}")
    logger.info(f"  原因: {workflow_result.get('reasoning', 'N/A')}")
    
    # 检查是否超时
    if workflow_result.get('workflow_type') == 'unknown':
        logger.warning(f"⚠️ Workflow提取可能失败或超时: {workflow_result.get('reasoning')}")
        logger.warning(f"  是否继续训练？(y/n): ", end='')
        # 这里简化处理，直接继续
        logger.info("  继续处理...")
    
    # 5. 预处理ground_truth
    logger.info("\n[5/6] 预处理Ground Truth...")
    try:
        ground_truth_processed = await preprocess_ground_truth_to_steps(ground_truth_raw, llm_client)
        logger.info(f"✓ 预处理完成")
    except Exception as e:
        logger.error(f"✗ 预处理失败: {str(e)}")
        return
    
    # 6. ACE训练
    logger.info("\n[6/6] 开始ACE训练...")
    
    # 初始化ACE组件
    eval_agent = EmailEvaluationAgent(llm_client=llm_client)
    task_env = EmailTaskEnvironment(eval_agent)
    
    local_playbook = Playbook()
    generator = Generator(llm_client)
    reflector = Reflector(llm_client)
    curator = Curator(llm_client)
    
    adapter = OfflineAdapter(
        playbook=local_playbook,
        generator=generator,
        reflector=reflector,
        curator=curator
    )
    
    # 构造样本
    specific_question = f"{topic}需要联系哪些人？需要检查哪些系统？需要执行哪些操作？"
    sample = Sample(
        question=specific_question,
        context=json.dumps({
            "workflow_result": workflow_result,
            "history": history
        }, ensure_ascii=False),
        ground_truth=ground_truth_processed
    )
    
    # 运行训练（5轮）
    logger.info("  >> 开始训练 5 轮...")
    results = adapter.run(
        samples=[sample],
        environment=task_env,
        epochs=5
    )
    
    # 获取最终得分
    last_result = results[-1]
    final_score = last_result.environment_result.metrics.get('score', 0)
    logger.info(f"  >> 训练完成，最终得分: {final_score:.2f}")
    
    # 提取策略
    new_bullets = []
    if local_playbook._bullets:
        logger.info(f"  >> 产生 {len(local_playbook._bullets)} 条策略")
        for bullet in local_playbook._bullets.values():
            bullet_dict = {
                "id": bullet.id,
                "section": bullet.section,
                "content": bullet.content,
                "helpful": bullet.helpful,
                "harmful": bullet.harmful
            }
            new_bullets.append(bullet_dict)
    
    # 7. 保存到数据库
    logger.info("\n[7/7] 保存到数据库...")
    record_data = {
        'email_id': conv_data['email_id'],
        'conversation_id': conv_data['conversation_id'],
        'topic': topic,
        'workflow_data': workflow_result,
        'ground_truth': ground_truth_processed,
        'learned_strategies': new_bullets,
        'final_score': final_score
    }
    
    save_learning_record(record_data)
    
    logger.info("\n" + "=" * 80)
    logger.info("✓ 重新处理完成！")
    logger.info("=" * 80)
    logger.info(f"  Email ID: {email_id}")
    logger.info(f"  最终得分: {final_score:.2f}")
    logger.info(f"  策略数量: {len(new_bullets)}")
    logger.info(f"  Workflow类型: {workflow_result.get('workflow_type', 'unknown')}")


if __name__ == "__main__":
    # 要重新处理的邮件ID
    EMAIL_ID = "1987724550649024513"
    USER_ID = 1952974833739087873
    
    print("=" * 80)
    print("重新处理单个邮件学习任务")
    print("=" * 80)
    print(f"邮件ID: {EMAIL_ID}")
    print(f"用户ID: {USER_ID}")
    print("=" * 80)
    
    asyncio.run(retry_single_email(EMAIL_ID, USER_ID))

