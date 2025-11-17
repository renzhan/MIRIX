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

# 加载环境变量
load_dotenv('.env')

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from ace import LiteLLMClient, Generator, Reflector, Curator, Playbook, OfflineAdapter, Sample, TaskEnvironment, EnvironmentResult
from mirix.agent.email_evaluation_agent import EmailEvaluationAgent

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_db_connection():
    """从环境变量获取数据库连接"""
    db_config = {
        'host': os.getenv('DEV_DB_HOST'),
        'port': int(os.getenv('DEV_DB_PORT', 3306)),
        'user': os.getenv('DEV_DB_USERNAME'),
        'password': os.getenv('DEV_DB_PASSWORD'),
        'database': os.getenv('DEV_DB_DATABASE'),
        'charset': 'utf8mb4',
        'cursorclass': DictCursor
    }
    
    logger.info(f"连接数据库: {db_config['host']}:{db_config['port']}/{db_config['database']}")
    
    try:
        connection = pymysql.connect(**db_config)
        logger.info("✓ 数据库连接成功")
        return connection
    except Exception as e:
        logger.error(f"✗ 数据库连接失败: {str(e)}")
        raise


def fetch_email_conversations_from_db(user_id: int = 1952974833739087873, limit: int = 10, offset: int = 0):
    """
    从数据库查询邮件会话（基于实际的email_basic和email_body表结构）
    
    Args:
        user_id: 用户ID
        limit: 查询的会话数量限制
        offset: 查询的偏移量
    
    Returns:
        list: 邮件会话列表，每个会话包含该conversation_id下的所有邮件
    """
    connection = get_db_connection()
    
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
            
            # 每条记录就是一个完整会话，content_text包含所有历史对话
            conversations_list = [[row['content_text']] for row in results]
            logger.info(f"✓ 准备进行训练，共 {len(conversations_list)} 个会话")
            
            return conversations_list
            
    except Exception as e:
        logger.error(f"✗ 查询邮件数据失败: {str(e)}")
        import traceback
        traceback.print_exc()
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
        
        Args:
            sample: 训练样本（包含question, context, ground_truth等）
            generated_output: Generator生成的输出
        
        Returns:
            EnvironmentResult: 评估结果
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
    """
    调用真实的 /workflow/extract 接口获取workflow
    
    Args:
        email_content: 邮件内容（现在通常是topic）
        email_account: 邮件账户
    
    Returns:
        workflow提取结果
    """
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
        return {
            "workflow_type": "unknown",
            "referenced_workflows": [],
            "next_steps": [],
            "reasoning": "API调用超时"
        }
    
    except httpx.HTTPError as e:
        logger.warning(f"  workflow提取失败: {str(e)}，跳过")
        return {
            "workflow_type": "unknown",
            "referenced_workflows": [],
            "next_steps": [],
            "reasoning": f"API调用失败: {str(e)}"
        }
    
    except Exception as e:
        logger.warning(f"  workflow提取异常: {str(e)}，跳过")
        return {
            "workflow_type": "unknown",
            "referenced_workflows": [],
            "next_steps": [],
            "reasoning": f"API调用异常: {str(e)}"
        }


async def preprocess_ground_truth_to_steps(natural_text: str, llm_client) -> str:
    """
    使用LLM将自然对话格式的邮件转换为严格步骤化格式
    """
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
    """
    对超长邮件进行智能总结，保留关键信息
    """
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
    """
    使用LLM智能处理邮件会话
    emails_data = [完整的邮件线程content_text]（只有1个元素，包含所有历史对话）
    """
    if not emails_data:
        raise ValueError("邮件数据为空")
    
    # emails_data[0] 就是完整的邮件线程
    raw_emails = emails_data[0] if emails_data else ""
    
    logger.info(f"\n[LLM处理会话] 邮件内容长度: {len(raw_emails)} 字符")
    
    # 如果邮件过长，先进行智能总结
    if len(raw_emails) > 30000:
        raw_emails = summarize_long_email(raw_emails, llm_client)
    
    # 使用LLM智能处理（强化的提示词）
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
        
        # 添加调试日志
        logger.debug(f"LLM返回内容（前1000字符）:\n{result_text[:1000]}")
        
        # 使用XML解析
        import re
        ground_truth_match = re.search(r'<ground_truth>(.*?)</ground_truth>', result_text, re.DOTALL)
        history_match = re.search(r'<history>(.*?)</history>', result_text, re.DOTALL)
        topic_match = re.search(r'<topic>(.*?)</topic>', result_text, re.DOTALL)
        
        # 严格验证必填字段
        missing_fields = []
        if not ground_truth_match:
            missing_fields.append("ground_truth")
        if not history_match:
            missing_fields.append("history")
        if not topic_match:
            missing_fields.append("topic")
        
        # 如果缺少字段且未重试过，则重试一次
        if missing_fields and retry_count == 0:
            logger.warning(f"  缺少必填字段: {missing_fields}，重试一次...")
            logger.error(f"LLM返回内容:\n{result_text[:1000]}...")
            return process_conversation_with_llm(emails_data, llm_client, retry_count=1)
        
        # 重试后仍失败，抛出异常
        if missing_fields:
            logger.error(f"LLM返回内容:\n{result_text}")
            raise ValueError(f"LLM返回格式错误，缺少必填字段: {missing_fields}")
        
        # 提取字段
        ground_truth = ground_truth_match.group(1).strip()
        history = history_match.group(1).strip()
        topic = topic_match.group(1).strip()
        
        # 验证字段不能为空
        if not ground_truth:
            raise ValueError("ground_truth不能为空")
        
        if not history:
            logger.warning("  history为空，要求LLM填充...")
            if retry_count == 0:
                return process_conversation_with_llm(emails_data, llm_client, retry_count=1)
            history = "无历史对话"
        
        # 验证topic不能是泛化词
        generic_topics = ["邮件处理", "邮件回复", "邮件", "处理", "回复"]
        if topic in generic_topics:
            logger.warning(f"  topic '{topic}' 是泛化词，要求重新生成...")
            if retry_count == 0:
                return process_conversation_with_llm(emails_data, llm_client, retry_count=1)
        
        logger.info(f"  ✓ 提取成功")
        logger.info(f"  主题: {topic}")
        logger.info(f"  ground_truth长度: {len(ground_truth)} 字符")
        logger.info(f"  history长度: {len(history)} 字符")
        
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




async def test_multi_turn_email_learning(conversations_list: list): 
    logger.info("=" * 60)
    logger.info("开始 ACE 批量邮件学习（真实数据）")
    logger.info("=" * 60)
    
    # 验证输入数据
    if not conversations_list:
        raise ValueError("conversations_list 不能为空，请提供邮件会话列表")
    
    logger.info(f"收到 {len(conversations_list)} 个邮件会话")
    
    # 1. 检查环境配置
    logger.info("\n[1/7] 检查环境配置...")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("未找到 OPENAI_API_KEY，请在 .env 文件中设置")
        return
    logger.info("✓ API Key 已配置")
    
    # 2. 初始化 LLM 客户端
    logger.info("\n[2/7] 初始化 LLM 客户端...")
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
    task_env = EmailTaskEnvironment(eval_agent)
    logger.info("✓ 训练环境创建完成")
    
    # 4. 批量处理所有邮件会话
    logger.info(f"\n[4/5] 批量处理 {len(conversations_list)} 个邮件会话...")
    training_samples = []
    failed_conversations = []
    
    for idx, conversation in enumerate(conversations_list, 1):
        logger.info(f"\n{'='*60}")
        logger.info(f"处理会话 {idx}/{len(conversations_list)}")
        logger.info(f"{'='*60}")
        
        try:
            # 4.1 使用LLM一次性处理整个会话（解析、格式化、提取主题）
            processed = process_conversation_with_llm(conversation, llm_client)
            
            topic = processed['topic']
            history = processed['history']
            ground_truth_raw = processed['ground_truth']
            
            logger.info(f"  主题: {topic}")
            
            # 4.2 调用workflow API（改用topic作为输入，减少token消耗和提高响应速度）
            workflow_result = await call_workflow_extract_api(topic, "shelia.sun@item.com")
            
            # 4.3 构造question
            specific_question = f"{topic}需要联系哪些人？需要检查哪些系统？需要执行哪些操作？"
            
            # 4.4 预处理ground_truth为步骤化格式
            ground_truth_processed = await preprocess_ground_truth_to_steps(
                ground_truth_raw, 
                llm_client
            )
            
            # 4.5 构造训练样本
            sample = Sample(
                question=specific_question,
                context=json.dumps({
                    "workflow_result": workflow_result,
                    "history": history
                }, ensure_ascii=False),
                ground_truth=ground_truth_processed
            )
            
            training_samples.append(sample)
            logger.info(f"✓ 会话 {idx} 处理成功")
            
        except Exception as e:
            logger.error(f"✗ 会话 {idx} 处理失败: {str(e)}")
            failed_conversations.append({"index": idx, "error": str(e)})
            continue
    
    # 输出处理摘要
    logger.info(f"\n{'='*60}")
    logger.info(f"批量处理完成")
    logger.info(f"{'='*60}")
    logger.info(f"✓ 成功处理: {len(training_samples)} 个会话")
    logger.info(f"✗ 失败: {len(failed_conversations)} 个会话")
    
    if failed_conversations:
        logger.warning(f"\n失败会话列表:")
        for fail in failed_conversations[:5]:  # 只显示前5个
            logger.warning(f"  - 会话 {fail['index']}: {fail['error']}")
        if len(failed_conversations) > 5:
            logger.warning(f"  ... 还有 {len(failed_conversations)-5} 个")
    
    if not training_samples:
        raise ValueError("没有成功处理任何会话，无法进行训练")
    
    # 5. 开始ACE训练
    logger.info("\n[5/5] 开始ACE批量训练")
    logger.info("=" * 60)
    logger.info(f"训练样本数: {len(training_samples)} 个会话")
    logger.info("=" * 60)
    
    playbook = Playbook()
    generator = Generator(llm_client)
    reflector = Reflector(llm_client)
    curator = Curator(llm_client)
    
    adapter = OfflineAdapter(
        playbook=playbook,
        generator=generator,
        reflector=reflector,
        curator=curator
    )
    
    # 训练轮数（每个样本都会训练num_epochs轮）
    num_epochs = 5
    logger.info(f"\n训练配置:")
    logger.info(f"  - 样本数量: {len(training_samples)}")
    logger.info(f"  - 训练轮数: {num_epochs}")
    logger.info(f"  - 总迭代次数: {len(training_samples) * num_epochs}")
    
    logger.info("\n开始训练...")
    results = adapter.run(
        samples=training_samples,
        environment=task_env,
        epochs=num_epochs
    )
    
    # 输出训练结果
    for i, result in enumerate(results):
        epoch_num = (i // len(training_samples)) + 1
        sample_num = (i % len(training_samples)) + 1
        logger.info(f"\n{'=' * 60}")
        logger.info(f"轮次 {epoch_num}/{num_epochs}, 样本 {sample_num}/{len(training_samples)}")
        logger.info(f"{'=' * 60}")
        
        metrics = result.environment_result.metrics
        score = metrics.get('score', 0)
        workflow_sim = metrics.get('workflow_similarity', 0)
        entities_matched = metrics.get('entities_matched_count', 0)
        entities_missing = metrics.get('entities_missing_count', 0)
        steps_matched = metrics.get('workflow_steps_matched', 0)
        steps_total = metrics.get('workflow_steps_total', 0)
        
        logger.info(f"  总评分: {score:.2f}")
        logger.info(f"  工作流相似度: {workflow_sim:.2f} (70%权重)")
        logger.info(f"  匹配实体数: {entities_matched} | 缺失实体数: {entities_missing}")
        logger.info(f"  匹配步骤: {steps_matched}/{steps_total}")
        
        feedback = result.environment_result.feedback
        if feedback:
            feedback_lines = feedback.split(' | ')
            for line in feedback_lines[:3]:
                logger.info(f"  {line}")
        logger.info(f"{'=' * 60}")
    
    logger.info(f"\n当前Playbook策略数量: {len(playbook._bullets)}")
    logger.info("✓ 批量训练完成！")
    
    # 6. 保存Playbook
    logger.info("\n保存Playbook...")
    playbook_path = "trained_email_playbook_multi_turn.json"
    playbook.save_to_file(playbook_path)
    logger.info(f"✓ Playbook已保存到: {playbook_path}")
    
    # 7. 输出学到的策略
    logger.info("\n学到的策略总结")
    logger.info("=" * 60)
    logger.info(f"训练会话数: {len(training_samples)}")
    logger.info(f"策略总数: {len(playbook._bullets)}")
    
    if playbook._bullets:
        logger.info("\n前5条策略：")
        bullet_list = list(playbook._bullets.values())
        for i, bullet in enumerate(bullet_list[:5], 1):
            logger.info(f"\n策略 #{i}:")
            logger.info(f"  ID: {bullet.id}")
            logger.info(f"  Section: {bullet.section}")
            logger.info(f"  内容: {bullet.content[:200]}...")
            logger.info(f"  Helpful: {bullet.helpful}, Harmful: {bullet.harmful}")
    else:
        logger.info("未学到任何策略")
    
    logger.info("\n" + "=" * 60)
    logger.info("批量邮件学习完成！")
    logger.info("=" * 60)
    logger.info(f"✓ 成功: {len(training_samples)}/{len(conversations_list)}")
    logger.info(f"✗ 失败: {len(failed_conversations)}")
    logger.info(f"✓ 学到策略: {len(playbook._bullets)} 条")
    
    return playbook


async def main_with_database(user_id: int = 1952974833739087873, limit: int = 10, offset: int = 0):
    """
    从数据库读取邮件会话并进行ACE训练
    
    Args:
        user_id: 用户ID
        limit: 查询的会话数量限制
        offset: 查询的偏移量
    """
    print("\n" + "=" * 80)
    print("ACE 批量邮件学习脚本（从数据库读取）")
    print("=" * 80)
    
    # 1. 从数据库查询邮件会话
    print("\n[步骤1] 从数据库查询邮件会话...")
    print(f"  用户ID: {user_id}")
    print(f"  会话数量: {limit}")
    print(f"  偏移量: {offset}")
    try:
        conversations_list = fetch_email_conversations_from_db(user_id=user_id, limit=limit, offset=offset)
        
        if not conversations_list:
            print("✗ 未查询到任何邮件会话")
            return
        
        print(f"✓ 成功查询到 {len(conversations_list)} 个会话")
        
    except Exception as e:
        print(f"✗ 数据库查询失败: {str(e)}")
        return
    
    # 2. 调用ACE训练
    print("\n[步骤2] 开始ACE训练...")
    try:
        playbook = await test_multi_turn_email_learning(conversations_list)
        print(f"\n✓ 训练完成！学到 {len(playbook._bullets)} 条策略")
        
    except Exception as e:
        print(f"✗ 训练失败: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # 配置训练参数
    USER_ID = 1952974833739087873
    LIMIT = 102  # 训练全部会话
    OFFSET = 0
    
    print("=" * 80)
    print("开始 ACE 邮件学习训练")
    print("=" * 80)
    print(f"用户ID: {USER_ID}")
    print(f"会话数量: {LIMIT}")
    print(f"偏移量: {OFFSET}")
    print("=" * 80)
    
    asyncio.run(main_with_database(
        user_id=USER_ID,
        limit=LIMIT,
        offset=OFFSET
    ))

