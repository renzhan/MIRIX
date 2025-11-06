"""
示例：使用 AI 解析器处理不同类型的文档
"""
import asyncio
import os
from prepdocslib.parser_factory import get_parser_for_file


async def parse_document(file_path: str):
    """解析单个文档"""
    # 获取文件扩展名
    _, ext = os.path.splitext(file_path)
    
    # 获取合适的解析器
    parser = get_parser_for_file(ext)
    
    if parser is None:
        print(f"不支持的文件类型: {ext}")
        return
    
    print(f"\n解析文件: {file_path}")
    print(f"使用解析器: {parser.__class__.__name__}")
    print("-" * 80)
    
    # 解析文档
    with open(file_path, 'rb') as f:
        async for page in parser.parse(f):
            print(f"\n页面 {page.page_num}:")
            print(page.text[:500])  # 只显示前500个字符
            print("...")


async def main():
    # 示例文件列表
    files = [
        "example.pdf",
        "example.html",
        "example.xlsx"
    ]
    
    # 解析每个文件
    for file_path in files:
        if os.path.exists(file_path):
            await parse_document(file_path)
        else:
            print(f"文件不存在: {file_path}")


if __name__ == "__main__":
    asyncio.run(main())
