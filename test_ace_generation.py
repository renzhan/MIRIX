"""
测试使用训练好的 Playbook 生成邮件回复
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import httpx

sys.path.insert(0, str(Path(__file__).parent))

from ace import LiteLLMClient, Generator, Playbook

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 加载环境变量
load_dotenv('.env')


async def call_workflow_extract_api(email_content: str, email_account: str = "test@example.com") -> dict:
    """调用 workflow 提取接口"""
    url = "https://aiop-dev.item.pub/pams/workflow/extract"
    payload = {
        "content": email_content,
        "email_account": email_account
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            result = response.json()
            return result.get("workflow_result", result)
    except httpx.HTTPError as e:
        logger.error(f"workflow提取失败: {str(e)}")
        return {
            "workflow_type": "unknown",
            "referenced_workflows": [],
            "next_steps": [],
            "reasoning": f"API调用失败: {str(e)}"
        }


async def test_generation_with_playbook():
    """测试使用训练好的 Playbook 生成回复"""
    
    # 检查 API Key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("未找到 OPENAI_API_KEY，请在 .env 文件中设置")
        return
    
    logger.info("=" * 80)
    logger.info("开始测试：使用训练好的 Playbook 生成邮件回复")
    logger.info("=" * 80)
    
    # 1. 加载训练好的 Playbook
    try:
        playbook = Playbook.load_from_file("trained_email_playbook2.json")
        logger.info(f"\n[SUCCESS] 成功加载 Playbook，包含 {len(playbook._bullets)} 条策略")
        
        # 显示策略摘要
        logger.info("\n当前 Playbook 策略概览：")
        for section_name, bullet_ids in playbook._sections.items():
            logger.info(f"  分类: {section_name}")
            logger.info(f"  策略数量: {len(bullet_ids)}")
            # 显示前3条策略内容的前50个字符
            for i, bullet_id in enumerate(bullet_ids[:3]):
                bullet = playbook._bullets[bullet_id]
                content_preview = bullet.content[:50].replace("\n", " ")
                logger.info(f"    [{i+1}] {content_preview}...")
    except Exception as e:
        logger.error(f"[ERROR] 加载 Playbook 失败: {str(e)}")
        return
    
    # 2. 初始化 LLM 客户端和 Generator
    llm_client = LiteLLMClient(
        model="gpt-4o",
        temperature=0.3,
        max_tokens=2048
    )
    
    # 🔧 Monkey patch ACE的JSON解析，支持markdown包裹的JSON
    import ace.roles
    original_safe_json_loads = ace.roles._safe_json_loads
    def patched_safe_json_loads(text: str):
        """清理markdown标记后再解析JSON"""
        cleaned = text.strip()
        # 移除markdown的```json```包裹
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]  # 移除```json
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]  # 移除```
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]  # 移除结尾的```
        cleaned = cleaned.strip()
        return original_safe_json_loads(cleaned)
    
    ace.roles._safe_json_loads = patched_safe_json_loads
    logger.info("✓ 已应用JSON解析补丁（支持markdown格式）")
    
    generator = Generator(llm_client)
    
    # 3. 准备测试场景
    test_scenarios = [
        {
            "name": "场景1: 类似问题（Duke Cannon订单类型 - 前端显示问题）",
            "email": """
发件人: 技术支持 <support@company.com>
收件人: Shelia Sun <shelia.sun@item.com>
主题: Duke Cannon前端订单类型显示异常

Shelia，

我现在打算解决Duke Cannon的订单类型问题，目前我已经检查了当前的API映射确保了正确捕获订单类型，下一步我们该做什么？

谢谢！
""",
            "email_account": "shelia.sun@item.com"
        },
        {
            "name": "场景2: 相关但不同的问题（另一个客户的订单问题）",
            "email": """
发件人: 项目组 <project@company.com>
收件人: Shelia Sun <shelia.sun@item.com>
主题: ABC公司订单状态同步问题

Shelia，

ABC公司反馈他们的订单状态在系统中更新不及时，
有时候订单已经发货了但是系统还显示"处理中"。
我该从哪里开始排查这个问题？

谢谢！
""",
            "email_account": "shelia.sun@item.com"
        }
    ]
    
    # 4. 对每个场景生成回复
    for scenario in test_scenarios:
        logger.info("\n" + "=" * 80)
        logger.info(f"\n{scenario['name']}")
        logger.info("=" * 80)
        logger.info(f"\n收到的邮件：\n{scenario['email']}")
        
        # 构造上下文（只包含原始邮件）
        context = json.dumps({
            "original_email": scenario['email'],
            "history": ""
        }, ensure_ascii=False)
        
        # 使用 Generator 生成回复
        logger.info("\n[STEP] 使用 Generator + Playbook 生成回复...")
        try:
            result = generator.generate(
                playbook=playbook,
                question="如何处理回复这封邮件？需要具体执行哪些步骤？需要联系哪些人和团队？",
                context=context
            )
            
            logger.info("\n" + "🤖 " * 40)
            logger.info("生成的邮件回复：")
            logger.info("=" * 80)
            logger.info(result.final_answer)
            logger.info("=" * 80)
            
            # 显示使用的策略（如果有该属性）
            if hasattr(result, 'bullets_used') and result.bullets_used:
                logger.info(f"\n✓ 使用了 {len(result.bullets_used)} 条 Playbook 策略：")
                for bullet_id in result.bullets_used:
                    if bullet_id in playbook._bullets:
                        bullet = playbook._bullets[bullet_id]
                        content_preview = bullet.content[:80].replace("\n", " ")
                        logger.info(f"  • [{bullet_id}] {content_preview}...")
            else:
                # 这是正常现象，ACE Generator 内部使用策略但不返回具体的策略ID列表
                logger.debug("[DEBUG] 当前ACE版本不返回具体策略ID（这不影响生成质量）")
                
        except Exception as e:
            logger.error(f"\n[ERROR] 生成回复失败: {str(e)}")
            import traceback
            traceback.print_exc()
    
    logger.info("\n" + "=" * 80)
    logger.info("测试完成！")
    logger.info("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_generation_with_playbook())

