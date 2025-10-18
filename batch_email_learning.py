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
from mirix import Mirix
import base64
import hashlib
import binascii
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

# 导入 MIRIX 相关模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

"""
🚀 邮件批处理脚本 (优化版)

性能改进：
- ✅ 直接使用 agent.send_message() 替代 HTTP API 调用
- ✅ 零网络开销，处理速度提升 2-3倍
- ✅ 复用完整的 Redis 机制和记忆学习流程
- ✅ memorizing=True 启用自动记忆学习

工作流程：
1. ChatAgent 分析邮件内容
2. Redis 临时消息累加器存储对话
3. 达到阈值时异步触发记忆吸收
4. Meta Memory Agent 协调各记忆Agent进行学习

使用方法：
1. 确保 MIRIX 系统正常运行
2. 配置 .env 文件中的数据库连接
3. 运行: python batch_process_latest_emails_aliyun.py
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

# 启用 MIRIX 内部的详细日志，以便看到工具调用
logging.getLogger('Mirix').setLevel(logging.DEBUG)  # MIRIX 主日志
logging.getLogger('mirix').setLevel(logging.DEBUG)  # mirix 所有模块
logging.getLogger('mirix.agent').setLevel(logging.DEBUG)  # Agent 相关
logging.getLogger('mirix.agent.agent').setLevel(logging.DEBUG)  # Agent 日志（包括工具调用）
logging.getLogger('mirix.services').setLevel(logging.DEBUG)  # 服务层日志
logging.getLogger('mirix.server').setLevel(logging.DEBUG)  # 服务器日志

# 打印日志文件位置
log_dir = os.path.abspath(os.path.dirname(log_filename))
print(f"📝 日志将同步保存到以下文件:")
print(f"   1️⃣ 标准日志: {os.path.abspath(log_filename)}")
print(f"   2️⃣ 控制台输出: {os.path.abspath(log_filename.replace('.log', '_print.log'))}")
print(f"   📂 日志目录: {log_dir}")
logger.info(f"日志文件: {log_filename}")

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

            print(f"[解密前] 密文: {encrypted_text[:50]}{'...' if len(encrypted_text) > 50 else ''}")
            print(f"[解密前] 密钥: {key}")

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

            print(f"[解密后] 原文: {decrypted_text[:50]}{'...' if len(decrypted_text) > 50 else ''}")
            return decrypted_text
        except Exception as e:
            print(f"解密失败: {str(e)}")
            print(f"解密失败的密文: {encrypted_text[:50]}{'...' if len(encrypted_text) > 50 else ''}")
            return encrypted_text  # 解密失败时返回空字符串，与C#实现保持一致

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
                # 打印原始数据库查询结果，用于调试
                print(f"📊 邮件 {entry_id} 数据库查询结果:")
                print(
                    f"   - subject: {row.get('subject', 'NULL')[:100] if row.get('subject') else 'NULL'}{'...' if len(str(row.get('subject', ''))) > 100 else ''}")
                print(
                    f"   - content_text: {row.get('content_text', 'NULL')[:100] if row.get('content_text') else 'NULL'}{'...' if len(str(row.get('content_text', ''))) > 100 else ''}")
                print(f"   - content_text is None: {row.get('content_text') is None}")
                print(f"   - content_text length: {len(str(row.get('content_text', '')))}")

                # 解密密钥
                encryption_key = "item-ai-agent-999"

                # 处理subject（需要解密）和content_text（不需要解密）
                encrypted_subject = row.get('subject', '')
                content_text = row.get('content_text', '')  # content_text不需要解密，直接使用原始值

                print(f"🔓 开始解密邮件 {entry_id} 的subject")
                print(f"   - 加密的subject长度: {len(encrypted_subject) if encrypted_subject else 0}")
                print(f"   - content_text长度: {len(content_text) if content_text else 0}")

                # 解密subject
                decrypted_subject = AesEncryptionHelper.decrypt_from_hex(encrypted_subject,
                                                                         encryption_key) if encrypted_subject else ''
                print(f"   - 解密后subject: {decrypted_subject[:100]}{'...' if len(decrypted_subject) > 100 else ''}")

                # content_text直接使用原始值
                print(
                    f"   - content_text预览: {content_text[:200] if content_text else ''}{'...' if len(str(content_text)) > 200 else ''}")

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
                                   total_emails: int = 0, memory_agent=None) -> Dict:
        """通过HTTP API处理单个邮件"""
        try:
            start_time = time.time()

            if memory_agent is None:
                return {
                    "entry_id": entry_id,
                    "conversation_id": conversation_id,
                    "status": "error",
                    "message": "memory_agent 未提供"
                }

            # 获取邮件数据用于显示
            email_data = self.fetch_email_by_entry_id(entry_id)
            if not email_data:
                return {
                    "entry_id": entry_id,
                    "conversation_id": conversation_id,
                    "status": "error",
                    "message": f"无法获取邮件数据: {entry_id}"
                }

            # # 🌐 调用已启动服务器的API处理邮件
            # # api_url = f"{self.server_url}/pams/api/process_mysql_email"
            # request_data = {"email_data": email_data, "user_id": self.user_id}
            #
            # email_data = request['email_data']
            # user_id = request.get('user_id', None)  # 获取可选的user_id参数

            # 验证邮件数据字段（检查字段存在且值不为空）
            required_fields = ['id', 'subject', 'content_text', 'sent_date_time']
            for field in required_fields:
                if field not in email_data or not email_data[field]:
                    return {
                        "status": "error",
                        "message": f"邮件数据缺少或为空的必需字段: {field}"
                    }

            # 构建统一的数据格式以适配现有处理逻辑
            email_id = str(email_data['id'])
            conversation_id = str(email_data.get('conversation_id', ''))

            # 直接使用传递过来的邮箱账户信息（已在SQL中JOIN获取）
            user_email_account = email_data.get('user_email_account', '未知邮箱账户')

            # 加载提示词并构造消息
            # email_analysis_prompt = gpt_system.get_system_text("base/meta_memory_agent")
            # 构建参与者信息
            participants_info = []
            if email_data.get('senders'):
                participants_info.append(f"发件人: {email_data['senders']}")
            if email_data.get('froms'):
                participants_info.append(f"来源: {email_data['froms']}")
            if email_data.get('recipients'):
                participants_info.append(f"收件人: {email_data['recipients']}")
            if email_data.get('cc_recipients'):
                participants_info.append(f"抄送: {email_data['cc_recipients']}")
            if email_data.get('bcc_recipients'):
                participants_info.append(f"密送: {email_data['bcc_recipients']}")
            if email_data.get('reply_to'):
                participants_info.append(f"回复地址: {email_data['reply_to']}")

            participants_text = '\n'.join(participants_info) if participants_info else '参与者信息不完整'

            email_content_message = f"""
            邮件内容分析请求：

            📧 邮件基本信息：
            - 邮箱账户: {user_email_account}
            - 主题: {email_data.get('subject', '无主题')}
            - 时间: {email_data.get('sent_date_time', '未知时间')}
            - 邮件类型: {email_data.get('mail_type', '未知')}
            - 邮件分类: {email_data.get('category_name', '未分类')}
            - 是否有附件: {email_data.get('has_attachments', False)}

            👥 参与者信息：
            {participants_text}

            📝 邮件正文：
            {email_data.get('content_text', '无内容')}

            🎯 请根据上述邮件内容，作为Meta Memory Manager进行分析并协调相应的记忆管理器。

            ⚠️ [重要提示] 当前数据的来源分类是 "{email_data.get('category_name', '未分类')}"，如果你从这些内容中提取了工作流程并保存到程序记忆体，请将此来源分类添加到 email_tag 字段中。
            """

            # 记录发送给 Mirix 的内容
            # logger.info("=" * 80)
            # logger.info(f"🚀 向 Mirix 发送邮件内容分析请求")
            # logger.info(f"📧 邮件ID: {entry_id}")
            # logger.info(f"📩 对话ID: {conversation_id}")
            # logger.info(f"👤 用户ID: user-0ff6f5b1-2cc1-46bf-b5bc-d4fa40cb7784")
            # logger.info(f"📨 邮件主题: {email_data.get('subject', '无主题')[:100]}{'...' if len(email_data.get('subject', '')) > 100 else ''}")
            # logger.info("📝 发送给 Mirix 的完整消息内容:")
            # logger.info("-" * 60)
            # logger.info(email_content_message)

            print(f"🔄 正在向 Mirix 发送邮件 {entry_id} 的分析请求...")
            print(email_content_message)

            # 使用底层方法，可以指定user_id或设为None
            print("\n" + "="*80)
            print(f"📤 发送邮件 {entry_id} 到 MIRIX 进行学习...")
            print("="*80)
            
            response = memory_agent._agent.send_message(
                message=email_content_message,
                memorizing=True,
                force_absorb_content=True,
                user_id="user-0ff6f5b1-2cc1-46bf-b5bc-d4fa40cb7784"  # 所有记忆数据保存到此用户下
            )

            print("\n" + "="*80)
            print(f"📥 MIRIX 处理结果:")
            print("="*80)
            if response:
                # 打印响应中的关键信息
                if hasattr(response, 'messages'):
                    messages = response.messages
                elif isinstance(response, dict) and 'messages' in response:
                    messages = response['messages']
                else:
                    messages = []
                
                if messages:
                    print(f"📝 生成了 {len(messages)} 条消息\n")
                    # 遍历所有消息，找出工具调用
                    tool_call_count = 0
                    for i, msg in enumerate(messages):
                        msg_dict = msg if isinstance(msg, dict) else (msg.to_dict() if hasattr(msg, 'to_dict') else None)
                        if msg_dict:
                            role = msg_dict.get('role', 'unknown')
                            
                            # 检查 tool_calls (新格式)
                            if 'tool_calls' in msg_dict and msg_dict['tool_calls']:
                                for tool_call in msg_dict['tool_calls']:
                                    tool_call_count += 1
                                    if isinstance(tool_call, dict):
                                        func_name = tool_call.get('function', {}).get('name', 'unknown')
                                        func_args = tool_call.get('function', {}).get('arguments', '')
                                    else:
                                        func_name = tool_call.function.name if hasattr(tool_call, 'function') else 'unknown'
                                        func_args = tool_call.function.arguments if hasattr(tool_call, 'function') else ''
                                    
                                    print(f"🔧 工具调用 #{tool_call_count}: {func_name}")
                                    print(f"   参数: {func_args[:300]}{'...' if len(func_args) > 300 else ''}\n")
                            
                            # 检查 function_call (旧格式)
                            elif 'function_call' in msg_dict and msg_dict['function_call']:
                                tool_call_count += 1
                                func_call = msg_dict['function_call']
                                func_name = func_call.get('name', 'unknown') if isinstance(func_call, dict) else func_call.name
                                func_args = func_call.get('arguments', '') if isinstance(func_call, dict) else func_call.arguments
                                print(f"🔧 工具调用 #{tool_call_count}: {func_name}")
                                print(f"   参数: {func_args[:300]}{'...' if len(func_args) > 300 else ''}\n")
                            
                            # 打印工具返回结果
                            elif role == 'tool' and 'content' in msg_dict:
                                content = msg_dict['content']
                                print(f"✅ 工具返回: {content[:200]}{'...' if len(str(content)) > 200 else ''}\n")
                    
                    if tool_call_count == 0:
                        print("⚠️ 未检测到工具调用\n")
                    else:
                        print(f"📊 共调用了 {tool_call_count} 个工具\n")
                
                # 打印 token 使用情况
                usage = None
                if hasattr(response, 'usage'):
                    usage = response.usage
                elif isinstance(response, dict) and 'usage' in response:
                    usage = response['usage']
                
                if usage:
                    if isinstance(usage, dict):
                        print(f"📊 Token使用: {usage}")
                    else:
                        print(f"📊 Token使用: {usage}")
                
                print(f"✅ 处理完成")
            else:
                print("⚠️ 未返回响应数据")
            print("="*80 + "\n")

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

    async def batch_process_latest_emails(self, page_size: int = 10, memory_agent=None):
        """批量处理最新对话邮件 - 分页查询直到全部处理完毕"""
        logger.info("🚀 开始批量处理邮件 (分页查询，每页10条)")

        if memory_agent is None:
            logger.error("memory_agent 未提供，无法继续处理")
            return []

        # 🎯 可选：仍然测试服务器连接以确保系统健康状态
        print("🔄 测试服务器连接...")
        try:
            server_connected = await self.test_server_connection()
            if not server_connected:
                print("⚠️ 服务器连接测试失败，但继续使用直接Agent调用")
                logger.warning("⚠️ 服务器连接测试失败，但继续使用直接Agent调用")
            else:
                print("✅ 服务器连接测试成功")
                logger.info("服务器连接测试成功")
        except Exception as conn_error:
            print(f"❌ 服务器连接测试异常: {conn_error}")
            logger.error(f"服务器连接测试异常: {conn_error}")
            # 继续处理，不退出

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
                        # 🚀 注意：process_single_email 现在是异步方法，需要传入 memory_agent
                        result = await self.process_single_email(
                            entry_id=entry_id,
                            conversation_id=conversation_id,
                            email_index=current_index,
                            total_emails=0,  # 总数未知，设为0
                            memory_agent=memory_agent  # 传递 memory_agent
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
        # 在main函数内初始化 memory_agent
        print("🔄 正在初始化 Mirix...")
        logger.info("正在初始化 Mirix...")

        # 设置环境变量启用调试模式
        os.environ['DEBUG'] = 'true'
        
        memory_agent = Mirix(
            config_path="mirix/configs/mirix_gpt5.yaml",
            api_key=os.getenv("OPENAI_API_KEY"))
        
        # 启用 CLIInterface 的详细输出
        if hasattr(memory_agent._agent, 'client') and hasattr(memory_agent._agent.client, 'interface'):
            # 让 interface 显示更多信息
            print("✅ 已启用详细日志输出模式")
            logger.info("已启用详细日志输出模式")

        print("✅ Mirix 初始化成功")
        logger.info("Mirix 初始化成功")

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
        results = asyncio.run(processor.batch_process_latest_emails(memory_agent=memory_agent))
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
