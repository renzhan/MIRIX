import os
from dotenv import load_dotenv
load_dotenv()

# ✅ 配置 Redis 连接（使用密码）
os.environ["MIRIX_REDIS_HOST"] = "localhost"
os.environ["MIRIX_REDIS_PORT"] = "6380"
os.environ["MIRIX_REDIS_PASSWORD"] = "aiop123456"  # ← Redis 密码

from mirix import Mirix

print("=" * 60)
print("📌 正在初始化 Mirix (Redis: localhost:6380)...")
print("=" * 60)

# 初始化 Mirix
agent = Mirix(
    config_path="mirix/configs/mirix_gpt4o-mini.yaml",
    api_key=os.getenv("OPENAI_API_KEY"),  # 确保设置了 OPENAI_API_KEY
)

# 发送消息
print("=" * 60)
print("📤 发送消息...")
print("=" * 60)

response = agent.chat(
    message="你是谁",
    user_id="user-a5835351-d045-47ff-9268-9a85395a6ed5"
)

print("=" * 60)
print("💬 AI 回复：")
print("=" * 60)
print(response)
print()