#!/usr/bin/env python3
"""
初始化所有默认 agents 的脚本

这个脚本会创建所有在 AGENT_CONFIGS 中定义的默认 agents，
类似于旧版本的自动初始化功能。

使用方法:
    python scripts/initialize_default_agents.py
    或者
    python -m scripts.initialize_default_agents
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from mirix import LLMConfig, EmbeddingConfig
from mirix.agent.agent_configs import AGENT_CONFIGS
from mirix.prompts import gpt_system
from mirix.schemas.agent import AgentType, CreateAgent
from mirix.schemas.block import CreateBlock
from mirix.server.server import SyncServer


def initialize_default_agents(
    server: SyncServer = None,
    client_id: str = None,
    llm_config: LLMConfig = None,
    embedding_config: EmbeddingConfig = None,
    system_prompts_folder: str = None,
):
    """
    初始化所有默认 agents
    
    Args:
        server: SyncServer 实例，如果为 None 则创建新实例
        client_id: 客户端 ID，如果为 None 则使用默认客户端
        llm_config: LLM 配置，如果为 None 则使用默认配置
        embedding_config: Embedding 配置，如果为 None 则使用默认配置
        system_prompts_folder: 系统提示文件夹路径，如果为 None 则使用默认路径
    
    Returns:
        dict: 创建的 agents 字典，键为 agent name，值为 AgentState
    """
    if server is None:
        server = SyncServer()
    
    if client_id is None:
        client = server.default_client
    else:
        client = server.client_manager.get_client_by_id(client_id)
    
    if llm_config is None:
        llm_config = LLMConfig.default_config("gpt-4o-mini")
    
    if embedding_config is None:
        embedding_config = EmbeddingConfig.default_config("text-embedding-004")
    
    # 确定系统提示文件夹
    if system_prompts_folder is None:
        system_prompts_folder = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "mirix",
            "prompts",
            "system",
            "base"
        )
    
    # 加载默认系统提示
    default_system_prompts = {}
    if os.path.exists(system_prompts_folder):
        for filename in os.listdir(system_prompts_folder):
            if filename.endswith(".txt"):
                agent_name = filename[:-4]  # Strip .txt suffix
                prompt_file = os.path.join(system_prompts_folder, filename)
                try:
                    with open(prompt_file, "r", encoding="utf-8") as f:
                        default_system_prompts[agent_name] = f.read()
                except Exception as e:
                    print(f"Warning: Failed to load prompt for {agent_name}: {e}")
    
    created_agents = {}
    
    print("开始初始化默认 agents...")
    print(f"将创建 {len(AGENT_CONFIGS)} 个 agents")
    print("-" * 60)
    
    for config in AGENT_CONFIGS:
        agent_name = config["name"]
        agent_type = config.get("agent_type")
        include_base_tools = config.get("include_base_tools", False)
        
        # 获取系统提示
        system_prompt = None
        if agent_name in default_system_prompts:
            system_prompt = default_system_prompts[agent_name]
        else:
            # 尝试从 gpt_system 加载
            try:
                system_prompt = gpt_system.get_system_text(f"base/{agent_name}")
            except Exception:
                # 如果都失败，使用默认提示
                system_prompt = f"You are a {agent_name}."
                print(f"Warning: Using default prompt for {agent_name}")
        
        try:
            # 检查 agent 是否已存在
            existing_agents = server.agent_manager.list_agents(actor=client, limit=1000)
            existing_agent = None
            for agent in existing_agents:
                if agent.name == agent_name:
                    existing_agent = agent
                    break
            
            if existing_agent:
                print(f"✓ {agent_name} 已存在，跳过创建 (ID: {existing_agent.id})")
                created_agents[agent_name] = existing_agent
                continue
            
            # 创建 agent
            if agent_name == "chat_agent":
                # chat_agent 需要 memory blocks (persona 和 human)
                memory_blocks = [
                    CreateBlock(
                        label="persona",
                        value="You are a helpful personal assistant who can help the user remember things.",
                    ),
                    CreateBlock(
                        label="human",
                        value="",
                    ),
                ]
                agent_create = CreateAgent(
                    name=agent_name,
                    system=system_prompt,
                    llm_config=llm_config,
                    embedding_config=embedding_config,
                    include_base_tools=include_base_tools,
                    memory_blocks=memory_blocks,
                )
            else:
                agent_create = CreateAgent(
                    name=agent_name,
                    agent_type=agent_type,
                    system=system_prompt,
                    llm_config=llm_config,
                    embedding_config=embedding_config,
                    include_base_tools=include_base_tools,
                )
            
            agent_state = server.create_agent(
                request=agent_create,
                actor=client,
            )
            
            created_agents[agent_name] = agent_state
            print(f"✓ 创建 {agent_name} 成功 (ID: {agent_state.id})")
            
        except Exception as e:
            print(f"✗ 创建 {agent_name} 失败: {e}")
            import traceback
            traceback.print_exc()
    
    print("-" * 60)
    print(f"初始化完成！成功创建 {len(created_agents)} 个 agents")
    
    return created_agents


def main():
    """主函数"""
    print("=" * 60)
    print("Mirix 默认 Agents 初始化脚本")
    print("=" * 60)
    print()
    
    try:
        created_agents = initialize_default_agents()
        
        print()
        print("创建的 agents 列表:")
        for name, agent_state in created_agents.items():
            print(f"  - {name}: {agent_state.id}")
        
        print()
        print("✅ 初始化完成！")
        
    except Exception as e:
        print()
        print(f"❌ 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

