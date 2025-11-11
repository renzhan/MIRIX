# -*- coding: utf-8 -*-
"""
ACE邮件学习测试脚本（LLM预处理版本）

与test_ace_email_learning.py的区别：
- test_ace_email_learning.py: 使用手工编写的严格结构化ground_truth
- 本文件: 使用自然语言ground_truth，通过LLM自动转换为严格结构化格式

目的：对比两种方式的学习效果
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import httpx  # 用于异步HTTP请求

# 加载环境变量
load_dotenv('.env')

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from ace import LiteLLMClient, Generator, Reflector, Curator, Playbook, OfflineAdapter, Sample, TaskEnvironment, EnvironmentResult
from mirix.agent.email_evaluation_agent import EmailEvaluationAgent

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


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
        # 解析context（包含原始邮件和workflow）
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
        email_content: 邮件内容
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
        async with httpx.AsyncClient(timeout=30.0) as client:
            logger.info(f"调用workflow提取API: {url}")
            response = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            response.raise_for_status()
            result = response.json()
            
            logger.info(f"✓ workflow提取成功")
            return result.get("workflow_result", result)  # 兼容不同的返回格式
            
    except httpx.HTTPError as e:
        logger.error(f"workflow提取失败: {str(e)}")
        # 返回一个空的workflow作为fallback
        return {
            "workflow_type": "unknown",
            "referenced_workflows": [],
            "next_steps": [],
            "reasoning": f"API调用失败: {str(e)}"
        }


def extract_email_topic(email_content: str, llm_client) -> str:
    """
    使用LLM从邮件中提取核心主题
    
    Args:
        email_content: 邮件内容
        llm_client: LLM客户端
    
    Returns:
        提取出的主题（简洁描述）
    """
    extraction_prompt = f"""请从以下邮件中提取核心问题/任务的简洁描述（10-20个字）。

要求：
1. 提取问题的核心主题（如"解决Duke Cannon订单类型问题"）
2. 保留关键实体（客户名、系统名等）
3. 用动宾短语格式（"做什么"）
4. 只输出主题本身，不要加任何解释

邮件内容：
{email_content}

核心主题："""
    
    try:
        response = llm_client.complete(extraction_prompt)
        topic = response.text.strip()
        logger.info(f"✓ 提取到的主题: {topic}")
        return topic
    except Exception as e:
        logger.error(f"主题提取失败: {str(e)}")
        return "处理邮件中的技术问题"  # 回退到通用主题


async def preprocess_ground_truth_to_steps(natural_text: str, llm_client) -> str:
    """
    使用LLM将自然对话格式的邮件转换为严格步骤化格式
    
    Args:
        natural_text: 自然对话格式的邮件内容
        llm_client: LLM客户端
    
    Returns:
        严格步骤化格式的邮件内容
    """
    preprocessing_prompt = f"""请将以下自然对话风格的邮件回复，改写为严格的步骤化格式。

严格要求：
1. **必须保留所有技术细节**：
   - 人名（如Frances Parro Belleza、Cody Gorsuch等）
   - 系统名（如WMS、EDI等）
   - 配置值（如'Regular Order'等）
   - 团队名（如Joliet团队、B-Solutions、Unis等）

2. **格式要求**：
   - 使用"第一步：..."、"第二步：..."、"第三步：..."格式
   - 每个步骤用一句完整的话描述要做的事情
   - 步骤之间空一行
   - 不要使用"-"、"•"等列表符号
   - 不要在步骤下再分子要点

3. **保持原有内容**：
   - 保持开头和结尾的问候语
   - 保持时间预估（如"2-3个工作日"）
   - 保持签名（Best regards等）

标准格式示例：
```
您好！

很好，API映射确认没问题了。接下来Duke Cannon的订单类型问题需要按照以下步骤处理：

第一步：与Frances Parro Belleza协调讨论Duke Cannon的EDI订单逻辑调整，重点确认B2B地面订单的order type是否应统一设置为'Regular Order'

第二步：与Cody Gorsuch确认订单类型在传输过程中是否正确

第三步：检查WMS是否有影响订单类型的预导入更改

预计2-3个工作日可以推进到测试阶段。有问题随时项目群里说。

Best regards,
Shelia Sun
```

原始邮件：
{natural_text}

请严格按照上述格式要求输出改写后的邮件，不要添加任何解释或额外说明。"""
    
    logger.info("\n[预处理] 使用LLM将自然对话转换为步骤化格式...")
    logger.info("调用LLM中...")
    response = llm_client.complete(preprocessing_prompt)
    processed_text = response.text.strip()
    
    logger.info("=" * 80)
    logger.info("✓ 预处理完成！LLM转换后的邮件内容：")
    logger.info("=" * 80)
    logger.info(processed_text)
    logger.info("=" * 80)
    
    return processed_text


async def test_ace_email_learning():
    """测试ACE邮件学习（自然语言 + LLM预处理版本）"""
    
    logger.info("=" * 60)
    logger.info("开始 ACE 邮件学习测试（LLM预处理版 + 新评估标准）")
    logger.info("=" * 60)
    
    # 1. 检查 API Key
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
        max_tokens=2048  # 增加token限制，避免JSON被截断
    )
    logger.info("✓ LLM 客户端初始化完成")
    
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
    
    # 3. 创建评估Agent和训练环境
    logger.info("\n[3/5] 创建训练环境（使用新的评估标准：70%权重在关键实体匹配）...")
    eval_agent = EmailEvaluationAgent(llm_client=llm_client)
    task_env = EmailTaskEnvironment(eval_agent)
    logger.info("✓ 训练环境创建完成")
    
    # 4. 准备测试数据
    logger.info("\n[4/5] 准备测试数据...")
    
    # 示例1：Duke Cannon订单类型问题
    sample_email_1 = """
发件人: 项目组 <project@company.com>
收件人: Shelia Sun <shelia.sun@item.com>

Shelia，

我现在打算解决Duke Cannon的订单类型问题，目前我已经检查了当前的API映射确保了正确捕获订单类型，下一步我该做什么？

谢谢！
"""
    
    # 调用真实的workflow提取API
    test_email_account = "shelia.sun@item.com"
    logger.info(f"调用workflow提取接口...")
    workflow_result_1 = await call_workflow_extract_api(sample_email_1, test_email_account)
    
    # 用户真实回复（ground truth）- 自然语言格式（口语化）
    ground_truth_1_natural = """
您好！

很好，API映射确认没问题了。接下来Duke Cannon的订单类型问题需要多方协调处理，我这边给你梳理一下思路。

首先你需要跟Frances Parro Belleza聊一下，主要讨论Duke Cannon的EDI订单逻辑需要做哪些调整。特别要确认一下B2B地面订单的order type是不是应该统一设置成'Regular Order'，这个很重要。

跟Frances确认好逻辑之后，记得找Cody Gorsuch确认一下订单类型在传输过程中是否正确。同时也要检查一下WMS那边有没有什么预导入的更改会影响到订单类型，这个环节经常容易被忽略。

另外建议你跟Joliet团队接洽一下，了解他们那边的技术能力，看看有没有更好的解决方案。还有B-Solutions和Unis那边关于API处理和订单类型区分的反馈也要整合进来，他们可能有一些我们没注意到的细节。

整个过程中要定期跟Frances Parro Belleza和Michael Jan Francisco同步进展，确保大家的理解是一致的。这种跨团队协作的项目，沟通很关键。

大概2-3个工作日应该能推进到测试阶段。有问题随时项目群里说。

Best regards,
Shelia Sun
"""
    
    # 5. 使用LLM预处理ground_truth
    logger.info("\n[5/5] 预处理ground_truth...")
    logger.info("=" * 80)
    logger.info("原始邮件（自然对话风格）：")
    logger.info("=" * 80)
    logger.info(ground_truth_1_natural)
    logger.info("=" * 80)
    
    # 🔑 使用LLM将自然语言转为严格步骤
    ground_truth_1_processed = await preprocess_ground_truth_to_steps(ground_truth_1_natural, llm_client)
    
    # 🔑 自动提取邮件主题
    logger.info("\n🔑 使用LLM自动提取邮件主题...")
    email_topic_1 = extract_email_topic(sample_email_1, llm_client)
    
    # 基于提取的主题构造question（简洁直接，只问"做什么"不问"怎么做"）
    specific_question_1 = f"{email_topic_1}需要联系哪些人？需要检查哪些系统？需要执行哪些具体化流程？"
    logger.info(f"✓ 动态生成的question: {specific_question_1}")
    
    # 构造训练样本（使用LLM预处理后的ground_truth）
    training_samples = [
        Sample(
            question=specific_question_1,
            context=json.dumps({
                "original_email": sample_email_1,
                "workflow_result": workflow_result_1,
                "history": ""
            }, ensure_ascii=False),
            ground_truth=ground_truth_1_processed,  # 使用LLM预处理后的版本
            metadata={"email_type": "technical_support", "sample_id": 1, "extracted_topic": email_topic_1, "preprocessing": "llm"}
        )
    ]
    
    logger.info(f"✓ 准备了 {len(training_samples)} 个训练样本（LLM预处理）")
    
    # 6. 开始训练
    logger.info("\n" + "=" * 60)
    logger.info("开始ACE训练（LLM预处理版）")
    logger.info("=" * 60)
    playbook = Playbook()
    
    # 创建 ACE 的三个核心组件
    generator = Generator(llm_client)
    reflector = Reflector(llm_client)
    curator = Curator(llm_client)
    
    # 创建适配器
    adapter = OfflineAdapter(
        playbook=playbook,
        generator=generator,
        reflector=reflector,
        curator=curator
    )
    
    # 训练5轮
    num_epochs = 5
    logger.info(f"训练轮数: {num_epochs}")
    logger.info(f"样本数量: {len(training_samples)}")
    
    # 使用 run() 方法进行训练
    logger.info("\n开始训练...")
    results = adapter.run(
        samples=training_samples,
        environment=task_env,
        epochs=num_epochs
    )
    
    # 输出训练结果（详细显示关键实体匹配情况）
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
        
        # 显示部分反馈
        feedback = result.environment_result.feedback
        if feedback:
            feedback_lines = feedback.split(' | ')
            for line in feedback_lines[:3]:  # 显示前3条反馈
                logger.info(f"  {line}")
        logger.info(f"{'=' * 60}")
    
    logger.info(f"\n当前Playbook策略数量: {len(playbook._bullets)}")
    
    logger.info("\n✓ 训练完成！")
    
    # 6. 保存并查看Playbook（保存到不同文件名，避免覆盖）
    logger.info("\n保存Playbook...")
    playbook_path = "trained_email_playbook.json"
    playbook.save_to_file(playbook_path)
    logger.info(f"✓ Playbook已保存到: {playbook_path}")
    
    # 7. 输出学到的策略
    logger.info("\n学到的策略总结")
    logger.info("=" * 60)
    logger.info(f"策略总数: {len(playbook._bullets)}")
    
    if playbook._bullets:
        logger.info("\n前5条策略：")
        bullet_list = list(playbook._bullets.values())
        for i, bullet in enumerate(bullet_list[:5], 1):
            logger.info(f"\n策略 #{i}:")
            logger.info(f"  ID: {bullet.id}")
            logger.info(f"  Section: {bullet.section}")
            logger.info(f"  内容: {bullet.content[:150]}...")
            logger.info(f"  Helpful: {bullet.helpful}, Harmful: {bullet.harmful}")
    else:
        logger.info("未学到任何策略（可能评分都较低）")
    
    logger.info("\n" + "=" * 60)
    logger.info("测试完成！")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_ace_email_learning())


