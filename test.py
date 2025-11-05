# test_ace_basic.py
from ace import LiteLLMClient, Generator, Playbook
import os
from dotenv import load_dotenv

load_dotenv('.env')

api_key = os.getenv("OPENAI_API_KEY")
print(f"[DEBUG] API Key loaded: {api_key[:20] + '...' if api_key else 'None'}")

# 检查 API Key
if api_key:
    client = LiteLLMClient(model="gpt-4o-mini")
    generator = Generator(client)
    playbook = Playbook()
    
    output = generator.generate(
        question="测试：1+1等于多少？",
        context="简单测试",
        playbook=playbook
    )
    print(f"[SUCCESS] ACE generated: {output.final_answer}")
else:
    print("[WARNING] Please set OPENAI_API_KEY in env file")