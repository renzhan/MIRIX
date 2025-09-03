import os
import sys
import psycopg2
import asyncio
import aiohttp
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional
import time

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class LatestEmailProcessor:
    def __init__(self, server_url: str = "http://localhost:47283"):
        """初始化处理器"""
        logger.info("🔄 初始化处理器...")
        self.server_url = server_url.rstrip('/')  # 移除末尾斜杠
        
    def get_company_email_db_connection(self):
        """获取公司邮件数据库连接"""
        conn_params = {
            'host': os.getenv('DB_HOST', 'ec2-35-85-97-177.us-west-2.compute.amazonaws.com'),
            'port': os.getenv('DB_PORT', '5432'),
            'database': os.getenv('DB_NAME', 'email'),
            'user': os.getenv('DB_USER', 'postgres'),
            'password': os.getenv('DB_PASSWORD', 'aiop123456')
        }
        return psycopg2.connect(**conn_params)
    
    def fetch_latest_conversation_emails(self, page_size: int = 100) -> List[Dict]:
        """
        按页获取满足条件的全部邮件
        
        流程：
        1. 从conversation表过滤特定category记录  
        2. 对每个conv_id取最新记录（去重）
        3. 根据conv_id在emails表中查找对应的entry_id（每个对话取最新）
        4. 按页返回所有结果
        """
        try:
            conn = self.get_company_email_db_connection()
            cursor = conn.cursor()
            
            logger.info(f"📧 查询特定类别对话邮件...")
            
            # 首先获取总数
            cursor.execute("""
                SELECT COUNT(DISTINCT conv_id) as total_count
                FROM conversation 
                WHERE category IN ('human_resources_approval', 'internal_business_communication')
                  AND conv_id IS NOT NULL
            """)
            
            total_count = cursor.fetchone()[0]
            logger.info(f"📊 符合条件的对话总数: {total_count}")
            
            if total_count == 0:
                logger.warning("⚠️ 没有找到符合条件的对话记录")
                cursor.close()
                conn.close()
                return []
            
            # 分页获取所有符合条件的conv_id
            all_conv_ids = []
            offset = 0
            
            while offset < total_count:
                cursor.execute("""
                    SELECT DISTINCT ON (conv_id) 
                           conv_id
                    FROM conversation 
                    WHERE category IN ('human_resources_approval', 'internal_business_communication')
                      AND conv_id IS NOT NULL
                    ORDER BY conv_id, mail_time DESC
                    LIMIT %s OFFSET %s
                """, (page_size, offset))
                
                conversation_rows = cursor.fetchall()
                if not conversation_rows:
                    break
                
                page_conv_ids = [row[0] for row in conversation_rows]
                all_conv_ids.extend(page_conv_ids)
                offset += page_size
                
                logger.info(f"📄 已获取 {len(all_conv_ids)}/{total_count} 个对话ID")
            
            logger.info(f"✅ 获取到 {len(all_conv_ids)} 个对话ID，开始获取邮件详情...")
            
            # 分页获取邮件详情
            all_emails = []
            email_offset = 0
            
            while email_offset < len(all_conv_ids):
                # 获取当前页的conv_ids
                current_page_conv_ids = all_conv_ids[email_offset:email_offset + page_size]
                
                # 构建IN查询的占位符
                placeholders = ','.join(['%s'] * len(current_page_conv_ids))
                
                # 查询emails表，每个conversation_id取最新的entry_id
                cursor.execute(f"""
                    SELECT DISTINCT ON (conversation_id) 
                           entry_id, conversation_id, mail_time, subject, sender_name
                    FROM emails 
                    WHERE conversation_id IN ({placeholders})
                    ORDER BY conversation_id, mail_time DESC
                """, current_page_conv_ids)
                
                email_rows = cursor.fetchall()
                
                for row in email_rows:
                    all_emails.append({
                        "entry_id": row[0],
                        "conversation_id": row[1], 
                        "mail_time": row[2].isoformat() if row[2] else None,
                        "subject": row[3],
                        "sender_name": row[4]
                    })
                
                email_offset += page_size
                logger.info(f"📧 已获取 {len(all_emails)} 个邮件详情")
            
            cursor.close()
            conn.close()
            
            logger.info(f"✅ 最终获取到 {len(all_emails)} 个待处理邮件")
            return all_emails
                
        except Exception as e:
            logger.error(f"❌ 获取最新对话邮件失败: {e}")
            raise e
    
    def fetch_email_by_entry_id(self, entry_id: str) -> Optional[Dict]:
        """根据entry_id从emails表获取指定邮件"""
        try:
            conn = self.get_company_email_db_connection()
            cursor = conn.cursor()
            
            # 查询指定entry_id的邮件（从emails表）
            cursor.execute("""
                SELECT id, entry_id, subject, body, sender_email, sender_name, 
                       recipients, mail_time, conversation_id, category
                FROM emails 
                WHERE entry_id = %s
            """, (entry_id,))
            
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if row:
                return {
                    "id": row[0],
                    "entry_id": row[1], 
                    "subject": row[2],
                    "content": row[3],  # body字段
                    "sender_email": row[4],
                    "sender_name": row[5],
                    "recipients": row[6],
                    "mail_time": row[7].isoformat() if row[7] else None,
                    "conversation_id": row[8],
                    "category": row[9]
                }
            else:
                return None
                
        except Exception as e:
            logger.error(f"❌ 获取邮件失败: {e}")
            raise e
    
    def extract_key_fields_from_analysis(self, agent_response: str) -> Dict:
        """从智能体分析结果中提取关键字段"""
        try:
            # 默认值
            extracted = {
                "intent": "unknown",
                "urgency": "low", 
                "sentiment": "neutral",
                "key_entities": []
            }
            
            if not agent_response:
                return extracted
            
            response_text = str(agent_response).lower()
            
            # 简单的关键词匹配提取
            if any(word in response_text for word in ["approval", "审批", "批准"]):
                extracted["intent"] = "approval"
            elif any(word in response_text for word in ["meeting", "会议", "讨论"]):
                extracted["intent"] = "meeting"
            elif any(word in response_text for word in ["notification", "通知", "告知"]):
                extracted["intent"] = "notification"
            elif any(word in response_text for word in ["inquiry", "咨询", "询问"]):
                extracted["intent"] = "inquiry"
            
            if any(word in response_text for word in ["urgent", "紧急", "立即", "马上"]):
                extracted["urgency"] = "high"
            elif any(word in response_text for word in ["important", "重要"]):
                extracted["urgency"] = "medium"
            
            if any(word in response_text for word in ["positive", "good", "great", "excellent"]):
                extracted["sentiment"] = "positive"
            elif any(word in response_text for word in ["negative", "bad", "problem", "issue"]):
                extracted["sentiment"] = "negative"
            
            return extracted
            
        except Exception as e:
            logger.warning(f"⚠️ 字段提取失败: {e}")
            return {
                "intent": "unknown",
                "urgency": "low",
                "sentiment": "neutral", 
                "key_entities": []
            }
    
    async def test_server_connection(self):
        """测试服务器连接"""
        try:
            logger.info("🧠 测试服务器连接...")
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.server_url}/health") as response:
                    if response.status == 200:
                        health_data = await response.json()
                        agent_ready = health_data.get("agent_initialized", False)
                        if agent_ready:
                            logger.info("✅ 服务器连接成功，Agent已初始化")
                            return True
                        else:
                            logger.warning("⚠️ Agent未初始化，请稍等...")
                            return False
                    else:
                        logger.error(f"❌ 服务器响应异常: {response.status}")
                        return False
        except Exception as e:
            logger.error(f"❌ 服务器连接失败: {e}")
            logger.info("💡 请确保先运行: python main.py")
            return False
    
    async def process_single_email(self, entry_id: str, conversation_id: str, email_index: int = 0, total_emails: int = 0) -> Dict:
        """通过HTTP API处理单个邮件"""
        try:
            start_time = time.time()
            
            # 获取邮件数据用于显示
            email_data = self.fetch_email_by_entry_id(entry_id)
            if not email_data:
                return {
                    "entry_id": entry_id,
                    "conversation_id": conversation_id,
                    "status": "error",
                    "message": f"无法获取邮件数据: {entry_id}"
                }
            
            # 🌐 调用已启动服务器的API处理邮件
            api_url = f"{self.server_url}/api/process_email_by_entry_id"
            request_data = {"entry_id": entry_id}
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    api_url, 
                    json=request_data,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=600)  # 10分钟超时
                ) as response:
                    if response.status == 200:
                        api_result = await response.json()
                        total_time = time.time() - start_time
                        
                        return {
                            "entry_id": entry_id,
                            "conversation_id": conversation_id,
                            "status": "success",
                            "subject": email_data['subject'],
                            "sender": f"{email_data['sender_name']} ({email_data['sender_email']})",
                            "total_time": f"{total_time:.2f}s",
                            "api_result": api_result.get("status", "unknown"),
                            "api_message": api_result.get("message", "")
                        }
                    else:
                        error_text = await response.text()
                        total_time = time.time() - start_time
                        return {
                            "entry_id": entry_id,
                            "conversation_id": conversation_id,
                            "status": "error",
                            "message": f"API调用失败: {response.status}",
                            "total_time": f"{total_time:.2f}s"
                        }
            
        except Exception as e:
            total_time = time.time() - start_time
            return {
                "entry_id": entry_id,
                "conversation_id": conversation_id,
                "status": "error",
                "message": f"处理失败: {str(e)}",
                "total_time": f"{total_time:.2f}s"
            }
    
    async def batch_process_latest_emails(self):
        """批量处理特定类别对话邮件的主函数"""
        logger.info("🚀 开始批量处理邮件")
        
        # 测试服务器连接
        if not await self.test_server_connection():
            logger.error("❌ 服务器连接失败，退出处理")
            return
        
        # 获取特定类别的最新对话邮件列表
        try:
            latest_emails = self.fetch_latest_conversation_emails()
            if not latest_emails:
                logger.warning("⚠️ 没有找到符合条件的对话邮件")
                return
        except Exception as e:
            logger.error(f"❌ 获取邮件列表失败: {e}")
            return
        
        logger.info(f"📊 开始串行处理 {len(latest_emails)} 个邮件")
        
        # 串行处理所有邮件
        processed_results = []
        success_count = 0
        error_count = 0
        
        start_time = time.time()
        
        for i, email_info in enumerate(latest_emails):
            entry_id = email_info['entry_id']
            conversation_id = email_info['conversation_id']
            subject = email_info['subject']
            
            logger.info(f"\n📧 处理邮件 {i+1}/{len(latest_emails)}: {subject[:50]}...")
            
            try:
                result = await self.process_single_email(
                    entry_id=entry_id,
                    conversation_id=conversation_id,
                    email_index=i,
                    total_emails=len(latest_emails)
                )
                processed_results.append(result)
                
                if result['status'] == 'success':
                    success_count += 1
                    logger.info(f"✅ 处理成功: {result.get('total_time', 'N/A')}")
                else:
                    error_count += 1
                    logger.error(f"❌ 处理失败: {result.get('message', 'unknown error')}")
                      
            except Exception as e:
                error_count += 1
                logger.error(f"❌ 任务执行异常: {e}")
                processed_results.append({
                    "status": "error",
                    "message": f"任务执行异常: {str(e)}"
                })
        
        # 显示最终统计
        total_elapsed = time.time() - start_time
        avg_time_per_email = total_elapsed / len(latest_emails) if len(latest_emails) > 0 else 0
        
        logger.info(f"\n✅ 处理完成: {success_count}/{len(latest_emails)} 成功, 耗时: {total_elapsed/60:.1f}分钟")
        logger.info(f"📊 平均每封邮件处理时间: {avg_time_per_email:.2f}秒")
        
        # 保存失败详情到文件
        if error_count > 0:
            from datetime import datetime
            import os
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            failure_filename = f"failed_emails_{timestamp}.csv"
            failure_filepath = os.path.abspath(failure_filename)  # 获取绝对路径
            
            try:
                with open(failure_filename, 'w', encoding='utf-8') as f:
                    # CSV表头
                    f.write("序号,Entry_ID,Conversation_ID,处理时间,错误信息\n")
                    
                    failure_index = 1
                    for result in processed_results:
                        if result.get('status') == 'error':
                            entry_id = result.get('entry_id', 'unknown')
                            conversation_id = result.get('conversation_id', '')
                            processing_time = result.get('total_time', 'N/A')
                            message = result.get('message', 'unknown error')
                            
                            # 清理消息中的逗号和换行符
                            clean_message = message.replace(',', ';').replace('\n', ' ').replace('\r', '')
                            
                            f.write(f"{failure_index},{entry_id},{conversation_id},{processing_time},{clean_message}\n")
                            failure_index += 1
                
                logger.error(f"\n❌ {error_count}个失败邮件详情已保存到:")
                logger.info(f"📁 {failure_filepath}")
                
            except Exception as e:
                logger.error(f"\n❌ 保存失败详情文件时出错: {e}")
                # 如果文件保存失败，还是在控制台显示
                logger.error(f"\n❌ 失败详情 ({error_count}个):")
                for result in processed_results:
                    if result.get('status') == 'error':
                        entry_id = result.get('entry_id', 'unknown')[:20]
                        message = result.get('message', 'unknown error')
                        logger.error(f"   - {entry_id}...: {message}")
        else:
            logger.info("\n🎉 所有邮件处理成功！")
        

        return processed_results

def main():
    """主函数"""
    logger.info("🔥 批量邮件处理器")
    logger.info("🎯 类别: human_resources_approval + internal_business_communication")
    logger.info("📝 处理模式: 串行处理 (获取全部符合条件的邮件)")
    
    # 创建处理器实例
    processor = LatestEmailProcessor()
    
    # 运行批量处理
    try:
        results = asyncio.run(processor.batch_process_latest_emails())
        if results:
            success_count = len([r for r in results if r.get('status') == 'success'])
            logger.info(f"📊 最终统计: 成功处理 {success_count}/{len(results)} 个邮件")
            
    except KeyboardInterrupt:
        logger.warning("\n⚠️ 用户中断程序")
    except Exception as e:
        logger.error(f"\n❌ 程序执行失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
