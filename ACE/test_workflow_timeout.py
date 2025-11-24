"""
测试 Workflow API 超时问题
专门用于复现和测试指定邮件ID的 Workflow 提取是否超时
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import httpx
import pymysql
from pymysql.cursors import DictCursor

# 加载环境变量（从项目根目录加载 .env 文件）
project_root = Path(__file__).parent.parent
load_dotenv(project_root / '.env')

# 添加项目路径
sys.path.insert(0, str(project_root))

from ace import LiteLLMClient

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


def fetch_email_by_id(email_id: str, user_id: int = 1952974833739087873):
    """
    根据email_id查询单个邮件会话
    
    Returns:
        dict: 包含 email_id, conversation_id, content 的字典
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
            
            logger.info(f"✓ 找到邮件 ID: {email_id}")
            logger.info(f"  内容长度: {len(result['content_text'] or '')} 字符")
            
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


def extract_topic_from_email(email_content: str, llm_client) -> str:
    """从邮件内容中提取topic"""
    logger.info("\n[提取Topic] 使用LLM提取邮件主题...")
    
    # 如果邮件过长，先截断
    if len(email_content) > 30000:
        logger.warning(f"  邮件过长（{len(email_content)}字符），截断到30000字符")
        email_content = email_content[:30000]
    
    prompt = f"""请从以下邮件内容中提取核心业务主题（10-20字，必须描述具体业务场景）。

邮件内容：
{email_content}

请只输出主题，不要其他内容："""
    
    try:
        response = llm_client.complete(prompt)
        topic = response.text.strip()
        logger.info(f"✓ Topic提取成功: {topic}")
        return topic
    except Exception as e:
        logger.error(f"✗ Topic提取失败: {str(e)}")
        raise


async def test_workflow_api(topic: str, email_account: str = "shelia.sun@item.com", timeout: float = 180.0):
    """
    测试 Workflow API 调用，检查是否超时
    
    Args:
        topic: 邮件主题
        email_account: 邮件账户
        timeout: 超时时间（秒）
    
    Returns:
        dict: API返回结果
    """
    url = "https://aiop-dev.item.pub/pams/workflow/extract"
    
    payload = {
        "content": topic,
        "email_account": email_account
    }
    
    logger.info("=" * 80)
    logger.info("开始测试 Workflow API 调用")
    logger.info("=" * 80)
    logger.info(f"URL: {url}")
    logger.info(f"Topic: {topic}")
    logger.info(f"Email Account: {email_account}")
    logger.info(f"超时设置: {timeout} 秒")
    logger.info("=" * 80)
    
    start_time = asyncio.get_event_loop().time()
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            logger.info(f"\n⏱️  开始调用 API...")
            
            response = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            elapsed_time = asyncio.get_event_loop().time() - start_time
            
            response.raise_for_status()
            result = response.json()
            
            logger.info("=" * 80)
            logger.info("✓ API调用成功！")
            logger.info("=" * 80)
            logger.info(f"⏱️  耗时: {elapsed_time:.2f} 秒")
            logger.info(f"📊 返回结果:")
            logger.info(f"   workflow_type: {result.get('workflow_result', {}).get('workflow_type', 'N/A')}")
            logger.info(f"   reasoning: {result.get('workflow_result', {}).get('reasoning', 'N/A')[:100]}...")
            
            return result.get("workflow_result", result)
    
    except httpx.TimeoutException as e:
        elapsed_time = asyncio.get_event_loop().time() - start_time
        logger.error("=" * 80)
        logger.error("❌ API调用超时！")
        logger.error("=" * 80)
        logger.error(f"⏱️  耗时: {elapsed_time:.2f} 秒（超过 {timeout} 秒限制）")
        logger.error(f"📋 超时详情: {str(e)}")
        return {
            "workflow_type": "unknown",
            "referenced_workflows": [],
            "next_steps": [],
            "reasoning": f"API调用超时（{timeout}秒）"
        }
    
    except httpx.HTTPError as e:
        elapsed_time = asyncio.get_event_loop().time() - start_time
        logger.error("=" * 80)
        logger.error("❌ API调用失败！")
        logger.error("=" * 80)
        logger.error(f"⏱️  耗时: {elapsed_time:.2f} 秒")
        logger.error(f"📋 错误详情: {str(e)}")
        return {
            "workflow_type": "unknown",
            "referenced_workflows": [],
            "next_steps": [],
            "reasoning": f"API调用失败: {str(e)}"
        }
    
    except Exception as e:
        elapsed_time = asyncio.get_event_loop().time() - start_time
        logger.error("=" * 80)
        logger.error("❌ API调用异常！")
        logger.error("=" * 80)
        logger.error(f"⏱️  耗时: {elapsed_time:.2f} 秒")
        logger.error(f"📋 异常详情: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            "workflow_type": "unknown",
            "referenced_workflows": [],
            "next_steps": [],
            "reasoning": f"API调用异常: {str(e)}"
        }


async def main():
    """主函数：测试指定邮件ID的Workflow API调用"""
    # 要测试的邮件ID
    EMAIL_ID = "1987724550649024513"
    USER_ID = 1952974833739087873
    TIMEOUT = 180.0  # 超时时间（秒）
    
    print("\n" + "=" * 80)
    print("Workflow API 超时问题复现测试")
    print("=" * 80)
    print(f"邮件ID: {EMAIL_ID}")
    print(f"用户ID: {USER_ID}")
    print(f"超时设置: {TIMEOUT} 秒")
    print("=" * 80)
    
    # 1. 查询邮件
    logger.info("\n[步骤1] 查询邮件内容...")
    conv_data = fetch_email_by_id(EMAIL_ID, USER_ID)
    
    if not conv_data:
        logger.error(f"✗ 未找到邮件 ID: {EMAIL_ID}")
        return
    
    logger.info(f"✓ 邮件查询成功")
    
    # 2. 提取Topic
    logger.info("\n[步骤2] 提取邮件Topic...")
    llm_client = LiteLLMClient(
        model="gpt-4o",
        temperature=0.3,
        max_tokens=2048
    )
    
    try:
        topic = extract_topic_from_email(conv_data['content'], llm_client)
    except Exception as e:
        logger.error(f"✗ Topic提取失败: {str(e)}")
        return
    
    # 3. 测试Workflow API
    logger.info("\n[步骤3] 测试Workflow API调用...")
    result = await test_workflow_api(topic, "shelia.sun@item.com", timeout=TIMEOUT)
    
    # 4. 输出最终结果
    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)
    print(f"邮件ID: {EMAIL_ID}")
    print(f"Topic: {topic}")
    print(f"Workflow类型: {result.get('workflow_type', 'unknown')}")
    print(f"结果: {'✅ 成功' if result.get('workflow_type') != 'unknown' else '❌ 失败/超时'}")
    print(f"原因: {result.get('reasoning', 'N/A')}")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())

