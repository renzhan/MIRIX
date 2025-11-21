import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from ticket_api import TicketAPI

async def main():
    api = TicketAPI()
    
    print("Fetching 10 tickets...")
    # 直接只获取10个
    res = await api.fetch_ticket_list(page=1, size=10, display_status_ids=[2])
    
    if res.get("code") == 200:
        records = res.get("data", {}).get("records", [])
        
        # 提取ID列表
        id_list = [ticket['id'] for ticket in records]
        
        print("\n=== Ticket ID List (10) ===")
        print(id_list)
        print("===========================")
        
        # 为了方便您复制，也逐行打印一下
        for idx, tid in enumerate(id_list, 1):
            print(f"{idx}. {tid}")
            
    else:
        print("Failed:", res)

if __name__ == "__main__":
    asyncio.run(main())
