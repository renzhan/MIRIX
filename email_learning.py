import os
import sys
import mysql.connector
import asyncio
import aiohttp
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional
import time
from dotenv import load_dotenv
import requests  # 用于调用 HTTP API
import base64
import hashlib
import binascii
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

"""
🚀 邮件批处理脚本 (HTTP API 版本)

架构改进：
- ✅ 调用 FastAPI 服务的 /api/process_mysql_email 接口
- ✅ 先启动 python main.py，然后运行此脚本
- ✅ 接口内部直接调用 Meta Memory Agent 处理邮件
- ✅ 每封邮件独立调用 API 并返回触发的 memory agents

工作流程：
1. 读取邮件数据（MySQL）
2. 调用 HTTP API: POST /api/process_mysql_email
3. Meta Memory Agent 分析并触发对应的 memory agents
4. 记录处理状态、触发的 agents 和处理时间

使用方法：
1. 先启动 MIRIX 服务: python main.py
2. 配置 .env 文件中的数据库连接和 API 地址
3. 运行: python email_learning.py
"""

# 加载.env文件中的环境变量
load_dotenv()

# 创建日志文件名（带时间戳）
log_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
log_filename = f"batch_email_learning_{log_timestamp}.log"

# 配置日志 - 同时输出到控制台和文件
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        # 控制台处理器
        logging.StreamHandler(sys.stdout),
        # 文件处理器（同步写入）
        logging.FileHandler(log_filename, mode='w', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# HTTP API 模式不需要额外的 MIRIX 内部日志配置
# 所有日志将从 FastAPI 服务端输出

# 打印日志文件位置
log_dir = os.path.abspath(os.path.dirname(log_filename))
print(f"📝 日志将同步保存到以下文件:")
print(f"   1️⃣ 标准日志: {os.path.abspath(log_filename)}")
print(f"   2️⃣ 控制台输出: {os.path.abspath(log_filename.replace('.log', '_print.log'))}")
print(f"   📂 日志目录: {log_dir}")
logger.info(f"日志文件: {log_filename}")

# MIRIX API 配置
MIRIX_API_URL = os.getenv("MIRIX_API_URL", "http://localhost:47283")
MIRIX_USER_ID = os.getenv("MIRIX_USER_ID", "user-43a92772-e76b-4e5d-a1bd-3d32992580f9")

print(f"🌐 MIRIX API: {MIRIX_API_URL}")
print(f"👤 User ID: {MIRIX_USER_ID}\n")

# 创建一个同时输出到控制台和日志的打印函数
def log_print(message):
    """同时打印到控制台和日志文件"""
    print(message)
    # 使用 INFO 级别记录打印内容
    logger.info(message)


# Tee 类：同时输出到多个流（控制台 + 日志文件）
class TeeOutput:
    """将输出同时写入到多个文件对象"""
    def __init__(self, *files):
        self.files = files
    
    def write(self, data):
        for f in self.files:
            f.write(data)
            f.flush()  # 立即刷新，确保同步写入
    
    def flush(self):
        for f in self.files:
            f.flush()


# 打开日志文件用于 print 输出
print_log_file = open(log_filename.replace('.log', '_print.log'), 'w', encoding='utf-8')

# 重定向 print 输出到 Tee（同时输出到控制台和文件）
original_stdout = sys.stdout
sys.stdout = TeeOutput(original_stdout, print_log_file)


# 清理函数：在程序退出时恢复 stdout 并关闭日志文件
def cleanup_logging():
    """恢复标准输出并关闭日志文件"""
    global original_stdout, print_log_file
    try:
        sys.stdout = original_stdout
        if print_log_file and not print_log_file.closed:
            print_log_file.close()
            print(f"📝 所有日志已保存完毕")
    except Exception as e:
        print(f"清理日志时出错: {e}")


# 注册清理函数，确保程序退出时执行
import atexit
atexit.register(cleanup_logging)



class AesEncryptionHelper:
    """AES加密解密辅助类"""

    @staticmethod
    def encrypt_to_hex_ecb(plain_text: str, key: str) -> str:
        """
        使用ECB模式加密为十六进制字符串，与C#实现兼容

        Args:
            plain_text: 明文字符串
            key: 加密密钥

        Returns:
            加密后的十六进制字符串
        """
        try:
            if not plain_text:
                return ""

            print(f"[加密前] 原文: {plain_text[:50]}{'...' if len(plain_text) > 50 else ''}")
            print(f"[加密前] 密钥: {key}")

            # 使用MD5哈希处理密钥，与C#实现保持一致
            md5 = hashlib.md5()
            md5.update(key.encode('utf-8'))
            key_bytes = md5.digest()  # 16字节(128位)密钥

            # 将明文转换为字节并填充
            plain_bytes = plain_text.encode('utf-8')
            padded_data = pad(plain_bytes, AES.block_size)

            # 使用ECB模式加密
            cipher = AES.new(key_bytes, AES.MODE_ECB)
            encrypted_data = cipher.encrypt(padded_data)

            # 转换为十六进制字符串，与C#的BitConverter.ToString()保持一致
            hex_str = binascii.hexlify(encrypted_data).decode('ascii').upper()

            print(f"[加密后] 密文: {hex_str[:50]}{'...' if len(hex_str) > 50 else ''}")
            return hex_str
        except Exception as e:
            print(f"加密失败: {str(e)}")
            return ""

    @staticmethod
    def decrypt_from_hex_ecb(encrypted_text: str, key: str) -> str:
        """
        使用ECB模式从十六进制字符串解密，与C#实现兼容

        Args:
            encrypted_text: 加密后的十六进制字符串
            key: 加密密钥

        Returns:
            解密后的字符串
        """
        try:
            if not encrypted_text:
                return ""

            # 静默解密，不打印详情

            # 使用MD5哈希处理密钥，与C#实现保持一致
            md5 = hashlib.md5()
            md5.update(key.encode('utf-8'))
            key_bytes = md5.digest()  # 16字节(128位)密钥

            # 将十六进制字符串转换为字节
            # 处理可能的C#格式（带连字符）
            encrypted_text = encrypted_text.replace("-", "")
            encrypted_bytes = bytes.fromhex(encrypted_text)

            # 使用ECB模式解密
            cipher = AES.new(key_bytes, AES.MODE_ECB)
            decrypted_padded = cipher.decrypt(encrypted_bytes)

            # 移除填充
            decrypted_bytes = unpad(decrypted_padded, AES.block_size)

            # 转换为字符串
            decrypted_text = decrypted_bytes.decode('utf-8')

            return decrypted_text
        except Exception as e:
            logger.error(f"解密失败: {str(e)}")
            return encrypted_text  # 解密失败时返回原文

    @staticmethod
    def decrypt_from_hex(encrypted_text: str, key: str) -> str:
        """
        从十六进制字符串解密 - 为了兼容性，现在直接调用ECB模式解密

        Args:
            encrypted_text: 加密后的十六进制字符串
            key: 加密密钥

        Returns:
            解密后的字符串
        """
        # 直接使用ECB模式解密，与C#实现保持一致
        return AesEncryptionHelper.decrypt_from_hex_ecb(encrypted_text, key)

    @staticmethod
    def encrypt_to_hex(plain_text: str, key: str) -> str:
        """
        加密为十六进制字符串 - 为了兼容性，现在直接调用ECB模式加密

        Args:
            plain_text: 明文字符串
            key: 加密密钥

        Returns:
            加密后的十六进制字符串
        """
        # 直接使用ECB模式加密，与C#实现保持一致
        return AesEncryptionHelper.encrypt_to_hex_ecb(plain_text, key)


class LatestEmailProcessor:
    def __init__(self, server_url: str = None, user_id: str = None):
        """初始化处理器"""
        logger.info("🔄 初始化处理器...")
        self.server_url = (server_url or os.getenv('PAMS_SERVER_URL', 'http://localhost:47283')).rstrip('/')
        self.email_user_id = "1952974833739087873"
        self.user_id = user_id

    def get_company_email_db_connection(self):
        """获取公司邮件数据库连接"""
        conn_params = {
            'host': os.getenv('MYSQL_EMAIL_HOST', 'ec2-54-189-142-24.us-west-2.compute.amazonaws.com'),
            'port': int(os.getenv('MYSQL_EMAIL_PORT', '3306')),
            'database': os.getenv('MYSQL_EMAIL_DATABASE', 'email'),
            'user': os.getenv('MYSQL_EMAIL_USERNAME', 'email'),
            'password': os.getenv('MYSQL_EMAIL_PASSWORD', 'email!@#')
            # 'host': 'ec2-54-189-142-24.us-west-2.compute.amazonaws.com',
            # 'port': '3306',
            # 'database': 'email',
            # 'user': 'email',
            # 'password': 'email!@#'
        }
        print(f"🔄 正在连接数据库: {conn_params['host']}:{conn_params['port']}/{conn_params['database']}")
        logger.info(f"正在连接数据库: {conn_params['host']}:{conn_params['port']}/{conn_params['database']}")

        try:
            conn = mysql.connector.connect(**conn_params)
            print("✅ 数据库连接成功")
            logger.info("数据库连接成功")
            return conn
        except Exception as db_error:
            print(f"❌ 数据库连接失败: {db_error}")
            logger.error(f"数据库连接失败: {db_error}")
            raise db_error

    def fetch_latest_conversation_emails(self, page_size: int = 100, offset: int = 0) -> List[Dict]:
        """
        从email_basic和email_body表获取用户的最新对话邮件

        流程：
        1. 使用WITH语句获取每个conversation_id的最新邮件
        2. 返回邮件列表

        参数：
        - page_size: 每页数量，默认100
        - offset: 偏移量，用于分页
        """
        print(f"🔄 开始获取邮件数据: page_size={page_size}, offset={offset}, user_id={self.email_user_id}")
        logger.info(f"开始获取邮件数据: page_size={page_size}, offset={offset}, user_id={self.email_user_id}")

        try:
            conn = self.get_company_email_db_connection()
            cursor = conn.cursor(dictionary=True)

            # 获取最新对话邮件并关联用户账户和分类
            query = """
                    WITH RankedEmails AS (SELECT e.id, \
                                                 e.conversation_id, \
                                                 e.mail_type, \
                                                 e.has_attachments, \
                                                 e.sent_date_time, \
                                                 e.subject, \
                                                 e.user_id, \
                                                 e.category_id, \
                                                 ua.email as  user_email, \
                                                 ua.user_name, \
                                                 ua.phone_number, \
                                                 uc.name as category_name, \
                                                 ROW_NUMBER() OVER (
                        PARTITION BY e.conversation_id 
                        ORDER BY e.sent_date_time DESC, e.id DESC
                    ) AS rn \
                                          FROM email_basic AS e \
                                                   LEFT JOIN \
                                               user_account AS ua ON e.user_id = ua.user_id \
                                                   LEFT JOIN \
                                               user_category AS uc ON e.category_id = uc.id \
                                          WHERE e.user_id = %s),
                         FilteredRankedEmails AS (SELECT id, \
                                                         conversation_id, \
                                                         mail_type, \
                                                         has_attachments, \
                                                         sent_date_time, \
                                                         subject, \
                                                         user_id, \
                                                         category_id, \
                                                         user_email, \
                                                         user_name, \
                                                         phone_number, \
                                                         category_name \
                                                  FROM RankedEmails \
                                                  WHERE rn = 1)
                    SELECT fre.id, \
                           fre.conversation_id, \
                           fre.mail_type, \
                           fre.has_attachments, \
                           fre.sent_date_time, \
                           fre.subject, \
                           fre.user_id, \
                           fre.category_id, \
                           fre.category_name, \
                           eb.content_text, \
                           fre.user_email, \
                           fre.user_name, \
                           fre.phone_number, \
                           GROUP_CONCAT(CASE \
                                            WHEN ep.participant_type = 'sender' THEN \
                                                CONCAT(COALESCE(ua_p.user_name, ep.name), ' <', ep.address, '>') END SEPARATOR '; ') as senders, \
                           GROUP_CONCAT(CASE \
                                            WHEN ep.participant_type = 'from' THEN \
                                                CONCAT(COALESCE(ua_p.user_name, ep.name), ' <', ep.address, '>') END SEPARATOR '; ') as froms, \
                           GROUP_CONCAT(CASE \
                                            WHEN ep.participant_type = 'to' THEN \
                                                CONCAT(COALESCE(ua_p.user_name, ep.name), ' <', ep.address, '>') END SEPARATOR '; ') as recipients, \
                           GROUP_CONCAT(CASE \
                                            WHEN ep.participant_type = 'cc' THEN \
                                                CONCAT(COALESCE(ua_p.user_name, ep.name), ' <', ep.address, '>') END SEPARATOR '; ') as cc_recipients, \
                           GROUP_CONCAT(CASE \
                                            WHEN ep.participant_type = 'bcc' THEN \
                                                CONCAT(COALESCE(ua_p.user_name, ep.name), ' <', ep.address, '>') END SEPARATOR '; ') as bcc_recipients, \
                           GROUP_CONCAT(CASE \
                                            WHEN ep.participant_type = 'replyTo' THEN \
                                                CONCAT(COALESCE(ua_p.user_name, ep.name), ' <', ep.address, '>') END SEPARATOR '; ') as reply_to
                    FROM FilteredRankedEmails fre \
                             LEFT JOIN \
                         email_participants ep ON fre.id = ep.email_basic_id \
                             LEFT JOIN \
                         user_account ua_p ON ep.address = ua_p.email \
                             LEFT JOIN \
                         email_body eb ON eb.email_basic_id = fre.id
                    GROUP BY fre.id, fre.conversation_id, fre.mail_type, fre.has_attachments, fre.sent_date_time, \
                             fre.subject, fre.user_id, fre.category_id, fre.category_name, eb.content_text, fre.user_email, fre.user_name, fre.phone_number
                    ORDER BY fre.sent_date_time DESC
                        LIMIT %s \
                    OFFSET %s \
                    """

            cursor.execute(query, (self.email_user_id, page_size, offset))
            email_rows = cursor.fetchall()

            if not email_rows:
                cursor.close()
                conn.close()
                return []

            logger.info(f"获取到 {len(email_rows)} 个邮件记录")

            all_emails = []
            for row in email_rows:
                # 构建邮箱账户显示信息
                user_email_account = row.get('user_email', '未知邮箱')
                if row.get('user_name'):
                    user_email_account = f"{row['user_name']} ({row['user_email']})"

                all_emails.append({
                    "entry_id": str(row['id']),  # 使用id作为entry_id
                    "conversation_id": str(row['conversation_id']) if row['conversation_id'] else None,
                    "mail_time": row['sent_date_time'].isoformat() if row['sent_date_time'] else None,
                    "subject": row['subject'],
                    "sender_name": "",  # 新表结构中暂无此字段
                    "mail_type": row['mail_type'],
                    "has_attachments": row['has_attachments'],
                    "content_text": row['content_text'],
                    "user_id": row['user_id'],
                    "category_id": row.get('category_id', ''),
                    "category_name": row.get('category_name', '未分类'),
                    "user_email": row.get('user_email', ''),
                    "user_name": row.get('user_name', ''),
                    "user_email_account": user_email_account,  # 用于显示的完整邮箱账户信息
                    "phone_number": row.get('phone_number', ''),
                    # 邮件参与者信息
                    "senders": row.get('senders', ''),
                    "froms": row.get('froms', ''),
                    "recipients": row.get('recipients', ''),
                    "cc_recipients": row.get('cc_recipients', ''),
                    "bcc_recipients": row.get('bcc_recipients', ''),
                    "reply_to": row.get('reply_to', '')
                })

            cursor.close()
            conn.close()

            return all_emails

        except Exception as e:
            logger.error(f"获取邮件失败: {e}")
            raise e

    def fetch_email_by_entry_id(self, entry_id: str) -> Optional[Dict]:
        """根据entry_id获取邮件详情（从email_basic和email_body表）"""
        try:
            conn = self.get_company_email_db_connection()
            cursor = conn.cursor(dictionary=True)

            # 从email_basic和email_body表获取邮件详情，并关联user_account、user_category和email_participants获取完整信息
            print(f"🗄️ 执行SQL查询获取邮件 {entry_id} 的完整信息（包括email_body表数据）")
            cursor.execute("""
                           SELECT e.id,
                                  e.conversation_id,
                                  e.mail_type,
                                  e.has_attachments,
                                  e.sent_date_time,
                                  e.subject,
                                  e.user_id,
                                  e.category_id,
                                  uc.name                                                                                                                   as category_name,
                                  eb.content_text                                                                                                           as content_text,
                                  ua.email                                                                                                                  as user_email,
                                  ua.user_name,
                                  ua.phone_number,
                                  GROUP_CONCAT(DISTINCT CASE WHEN ep.participant_type = 'sender' THEN 
                        CONCAT(COALESCE(ua_p.user_name, ep.name), ' <', ep.address, '>') END SEPARATOR '; ') as senders,
                                  GROUP_CONCAT(DISTINCT CASE WHEN ep.participant_type = 'from' THEN 
                        CONCAT(COALESCE(ua_p.user_name, ep.name), ' <', ep.address, '>') END SEPARATOR '; ') as froms,
                                  GROUP_CONCAT(DISTINCT CASE WHEN ep.participant_type = 'to' THEN 
                        CONCAT(COALESCE(ua_p.user_name, ep.name), ' <', ep.address, '>') END SEPARATOR '; ') as recipients,
                                  GROUP_CONCAT(DISTINCT CASE WHEN ep.participant_type = 'cc' THEN 
                        CONCAT(COALESCE(ua_p.user_name, ep.name), ' <', ep.address, '>') END SEPARATOR '; ') as cc_recipients,
                                  GROUP_CONCAT(DISTINCT CASE WHEN ep.participant_type = 'bcc' THEN 
                        CONCAT(COALESCE(ua_p.user_name, ep.name), ' <', ep.address, '>') END SEPARATOR '; ') as bcc_recipients,
                                  GROUP_CONCAT(DISTINCT CASE WHEN ep.participant_type = 'replyTo' THEN 
                        CONCAT(COALESCE(ua_p.user_name, ep.name), ' <', ep.address, '>') END SEPARATOR '; ') as reply_to
                           FROM email_basic e
                                    LEFT JOIN user_account ua ON e.user_id = ua.user_id
                                    LEFT JOIN user_category uc ON e.category_id = uc.id
                                    LEFT JOIN email_participants ep ON e.id = ep.email_basic_id
                                    LEFT JOIN user_account ua_p ON ep.address = ua_p.email
                                    LEFT JOIN email_body eb ON eb.email_basic_id = e.id
                           WHERE e.id = %s
                             AND e.user_id = %s
                           GROUP BY e.id, e.conversation_id, e.mail_type, e.has_attachments, e.sent_date_time,
                                    e.subject,
                                    e.user_id, e.category_id, uc.name, eb.content_text, ua.email, ua.user_name, ua.phone_number
                           """, (entry_id, self.email_user_id))

            row = cursor.fetchone()
            cursor.close()
            conn.close()

            if row:
                # 简化数据库查询结果输出
                print(f"📊 邮件 {entry_id}: 主题长度={len(str(row.get('subject', '')))} | 内容长度={len(str(row.get('content_text', '')))}")

                # 解密密钥
                encryption_key = "item-ai-agent-999"

                # 处理subject（需要解密）和content_text（不需要解密）
                encrypted_subject = row.get('subject', '')
                content_text = row.get('content_text', '')  # content_text不需要解密，直接使用原始值

                print(f"🔓 解密主题...", end=" ")
                # 解密subject
                decrypted_subject = AesEncryptionHelper.decrypt_from_hex(encrypted_subject,
                                                                         encryption_key) if encrypted_subject else ''
                print(f"完成: {decrypted_subject[:50]}..." if len(decrypted_subject) > 50 else f"完成: {decrypted_subject}")

                # content_text直接使用原始值（不再打印预览）

                print(f"✅ 邮件 {entry_id} 处理完成")

                # 构建邮箱账户显示信息
                user_email_account = row.get('user_email', '未知邮箱')
                if row.get('user_name'):
                    user_email_account = f"{row['user_name']} ({row['user_email']})"

                return {
                    "id": row['id'],
                    "entry_id": str(row['id']),  # 使用id作为entry_id
                    "subject": decrypted_subject,  # 使用解密后的subject
                    "content": content_text,  # 使用原始的content_text（不解密）
                    "content_text": content_text,  # 使用原始的content_text（不解密）
                    "sender_email": "",  # 新表结构中暂无此字段
                    "sender_name": "",  # 新表结构中暂无此字段
                    "recipients": "",  # 新表结构中暂无此字段
                    "mail_time": row['sent_date_time'].isoformat() if row['sent_date_time'] else None,
                    "sent_date_time": row['sent_date_time'].isoformat() if row['sent_date_time'] else None,  # 保持与新API兼容
                    "conversation_id": str(row['conversation_id']) if row['conversation_id'] else None,
                    "category": "",  # 新表结构中暂无此字段
                    "category_id": row.get('category_id', ''),
                    "category_name": row.get('category_name', '未分类'),
                    "account_email": "",  # 新表结构中暂无此字段
                    "mail_type": row['mail_type'],
                    "has_attachments": row['has_attachments'],
                    "user_id": row['user_id'],
                    "user_email": row.get('user_email', ''),
                    "user_name": row.get('user_name', ''),
                    "user_email_account": user_email_account,  # 用于显示的完整邮箱账户信息
                    "phone_number": row.get('phone_number', ''),
                    # 邮件参与者信息
                    "senders": row.get('senders', ''),
                    "froms": row.get('froms', ''),
                    "recipients": row.get('recipients', ''),
                    "cc_recipients": row.get('cc_recipients', ''),
                    "bcc_recipients": row.get('bcc_recipients', ''),
                    "reply_to": row.get('reply_to', '')
                }
            else:
                logger.warning(f"未找到邮件: {entry_id}")
                return None

        except Exception as e:
            logger.error(f"获取邮件失败: {e}")
            raise e

    async def test_server_connection(self):
        """测试服务器连接"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.server_url}/pams/health") as response:
                    if response.status == 200:
                        health_data = await response.json()
                        agent_ready = health_data.get("agent_initialized", False)
                        if agent_ready:
                            return True
                        else:
                            return False
                    else:
                        return False
        except Exception as e:
            return False

    async def process_single_email(self, entry_id: str, conversation_id: str, email_index: int = 0,
                                   total_emails: int = 0) -> Dict:
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

            # 验证邮件数据字段（检查字段存在且值不为空）
            required_fields = ['id', 'subject', 'content_text', 'sent_date_time']
            for field in required_fields:
                if field not in email_data or not email_data[field]:
                    return {
                        "status": "error",
                        "message": f"邮件数据缺少或为空的必需字段: {field}"
                    }

            # 直接使用传递过来的邮箱账户信息（已在SQL中JOIN获取）
            user_email_account = email_data.get('user_email_account', '未知邮箱账户')
            
            # 简化日志输出
            print(f"🔄 正在向 MIRIX API 发送邮件 {entry_id} 的分析请求...")
            subject = email_data.get('subject', '无主题')
            subject_preview = subject[:50] + "..." if len(subject) > 50 else subject
            print(f"📧 主题: {subject_preview}")
            print(f"👤 账户: {user_email_account}")

            # 调用 HTTP API - 使用 /api/process_mysql_email 接口
            print(f"📤 API调用: {MIRIX_API_URL}/api/process_mysql_email")
            
            # 构建请求数据
            api_request = {
                "email_data": email_data,
                "user_id": MIRIX_USER_ID
            }
            
            # 发送 HTTP 请求
            try:
                api_response = requests.post(
                    f"{MIRIX_API_URL}/api/process_mysql_email",
                    json=api_request,
                    timeout=120  # 2分钟超时
                )
                
                if api_response.status_code == 200:
                    result = api_response.json()
                    status = result.get("status", "error")
                    agents_triggered = result.get("agents_triggered", 0)
                    triggered_memory_types = result.get("triggered_memory_types", [])
                    processing_time = result.get("processing_time", "N/A")
                    
                    if status == "success":
                        print(f"✅ API调用成功 - 触发{agents_triggered}个Agent")
                        if triggered_memory_types:
                            print(f"📊 Memory Types: {', '.join(triggered_memory_types)}")
                        print(f"⏱️ 处理时间: {processing_time}")
                        response = "success"
                    else:
                        print(f"⚠️ 处理状态: {status}")
                        response = None
                else:
                    print(f"❌ API返回错误: {api_response.status_code}")
                    print(f"错误详情: {api_response.text[:200]}...")
                    response = None
            except requests.exceptions.Timeout:
                print(f"⏱️ API 请求超时")
                response = None
            except Exception as api_error:
                print(f"❌ API 调用失败: {api_error}")
                response = None

            print("-" * 80)

            logger.info(f"✅ 邮件 {entry_id} 已成功处理")
            print(f"✅ 邮件 {entry_id} 处理完毕")

            total_time = time.time() - start_time
            return {
                "entry_id": entry_id,
                "conversation_id": conversation_id,
                "status": "success",
                "message": "邮件处理成功",
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

    async def batch_process_latest_emails(self, page_size: int = 10):
        """批量处理最新对话邮件 - 分页查询直到全部处理完毕"""
        logger.info("🚀 开始批量处理邮件 (分页查询，每页10条)")

        # 移除了 memory_agent 参数，直接使用 HTTP API
        print("🔄 使用 HTTP API 模式处理邮件...")

        # 分页处理所有邮件
        processed_results = []
        success_count = 0
        error_count = 0
        total_processed = 0
        page = 0

        start_time = time.time()

        while True:
            try:
                # 获取当前页的邮件
                offset = page * page_size
                print(f"📄 正在获取第 {page + 1} 页邮件，偏移量: {offset}")
                logger.info(f"正在获取第 {page + 1} 页邮件，偏移量: {offset}")

                try:
                    latest_emails = self.fetch_latest_conversation_emails(page_size=page_size, offset=offset)
                    print(f"📧 获取到 {len(latest_emails) if latest_emails else 0} 个邮件")
                except Exception as fetch_error:
                    print(f"❌ 获取邮件数据失败: {fetch_error}")
                    logger.error(f"获取邮件数据失败: {fetch_error}")
                    import traceback
                    traceback.print_exc()
                    break

                if not latest_emails:
                    logger.info(f"📄 第 {page + 1} 页没有更多邮件，处理完毕")
                    break

                logger.info(f"📄 处理第 {page + 1} 页，共 {len(latest_emails)} 个邮件")
                logger.info(f"🔧 处理方式: ChatAgent分析 → Redis累积 → 异步记忆学习")
                logger.info(f"👤 目标用户: {self.user_id}")

                # 处理当前页的所有邮件
                for i, email_info in enumerate(latest_emails):
                    entry_id = email_info['entry_id']
                    conversation_id = email_info['conversation_id']
                    subject = email_info['subject']
                    user_email_account = email_info.get('user_email_account', '未知邮箱')

                    current_index = total_processed + i
                    logger.info(f"处理邮件 {current_index + 1}: {user_email_account}")

                    try:
                        # 🚀 调用 HTTP API 处理邮件
                        result = await self.process_single_email(
                            entry_id=entry_id,
                            conversation_id=conversation_id,
                            email_index=current_index,
                            total_emails=0  # 总数未知，设为0
                        )
                        processed_results.append(result)

                        if result['status'] == 'success':
                            success_count += 1
                            logger.info(f"处理成功: {result.get('total_time', 'N/A')}")
                        else:
                            error_count += 1
                            logger.error(f"处理失败: {result.get('message', 'unknown error')}")

                    except Exception as e:
                        error_count += 1
                        logger.error(f"任务执行异常: {e}")
                        processed_results.append({
                            "status": "error",
                            "message": f"任务执行异常: {str(e)}"
                        })

                # 更新总处理数量
                total_processed += len(latest_emails)
                page += 1

                # 如果当前页的邮件数量少于page_size，说明已经是最后一页
                if len(latest_emails) < page_size:
                    logger.info(f"📄 第 {page} 页只有 {len(latest_emails)} 个邮件，处理完毕")
                    break

                logger.info(f"✅ 第 {page} 页处理完成，累计处理 {total_processed} 个邮件")

            except Exception as e:
                logger.error(f"获取第 {page + 1} 页邮件失败: {e}")
                break

        # 显示最终统计
        total_elapsed = time.time() - start_time
        avg_time_per_email = total_elapsed / total_processed if total_processed > 0 else 0

        logger.info(f"🎯 分页处理完成: 共处理 {total_processed} 个邮件，{success_count} 成功，{error_count} 失败")
        logger.info(f"⏱️ 总耗时: {total_elapsed / 60:.1f}分钟 | 平均每邮件: {avg_time_per_email:.2f}秒")
        logger.info(f"📄 分页信息: 每页 {page_size} 条，共 {page} 页")
        logger.info(f"🚀 性能优势: 直接Agent调用 + Redis批量记忆学习")

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

                logger.error(f"{error_count}个失败邮件详情已保存到: {failure_filepath}")

            except Exception as e:
                logger.error(f"保存失败详情文件时出错: {e}")
                logger.error(f"失败详情 ({error_count}个):")
                for result in processed_results:
                    if result.get('status') == 'error':
                        entry_id = result.get('entry_id', 'unknown')[:20]
                        message = result.get('message', 'unknown error')
                        logger.error(f"   - {entry_id}...: {message}")
        else:
            logger.info("所有邮件处理成功！")

        return processed_results


def main():
    """主函数"""
    print("🚀 程序开始执行")
    logger.info("启动批量邮件处理器")
    
    # 直接指定用户ID - 所有记忆数据将保存到此用户下
    user_id = "user-0ff6f5b1-2cc1-46bf-b5bc-d4fa40cb7784"
    logger.info(f"使用指定用户ID: {user_id}")

    try:
        # 检查 MIRIX API 是否可用
        print(f"🔄 正在检查 MIRIX API 连接: {MIRIX_API_URL}")
        logger.info(f"检查 MIRIX API: {MIRIX_API_URL}")
        
        try:
            response = requests.get(f"{MIRIX_API_URL}/health", timeout=5)
            if response.status_code == 200:
                print("✅ MIRIX API 连接成功")
                logger.info("MIRIX API 连接成功")
            else:
                print(f"⚠️ MIRIX API 返回状态码: {response.status_code}")
        except Exception as e:
            print(f"❌ 无法连接到 MIRIX API: {e}")
            print(f"💡 请先运行: python main.py")
            logger.error(f"API 连接失败: {e}")
            return

    except Exception as e:
        print(f"❌ Mirix 初始化失败: {e}")
        logger.error(f"Mirix 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # 创建处理器实例
    print("🔄 正在创建处理器实例...")
    try:
        processor = LatestEmailProcessor(user_id=user_id)
        print("✅ 处理器实例创建成功")
    except Exception as e:
        print(f"❌ 处理器实例创建失败: {e}")
        logger.error(f"处理器实例创建失败: {e}")
        return

    # 运行批量处理
    try:
        print("🔄 开始批量处理邮件...")
        print("🔧 正在调用 asyncio.run...")
        results = asyncio.run(processor.batch_process_latest_emails())
        print(f"📊 批量处理完成，获得结果: {len(results) if results else 0} 个")

        if results:
            success_count = len([r for r in results if r.get('status') == 'success'])
            logger.info(f"最终统计: 成功处理 {success_count}/{len(results)} 个邮件")
            print(f"🎯 处理完成: 成功处理 {success_count}/{len(results)} 个邮件")
        else:
            print("⚠️ 没有获得任何处理结果")
            logger.warning("没有获得任何处理结果")

    except KeyboardInterrupt:
        logger.warning("用户中断程序")
        print("⚠️ 用户中断程序")
    except Exception as e:
        logger.error(f"程序执行失败: {e}")
        print(f"❌ 程序执行失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
