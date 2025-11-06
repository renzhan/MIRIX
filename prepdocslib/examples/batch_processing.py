"""
示例：批量处理多个文档
"""
import asyncio
import os
from pathlib import Path
from prepdocslib.parser_factory import get_parser_for_file


async def process_file(file_path: Path) -> dict:
    """处理单个文件并返回结果"""
    try:
        ext = file_path.suffix
        parser = get_parser_for_file(ext)
        
        if parser is None:
            return {
                "file": str(file_path),
                "status": "skipped",
                "reason": f"不支持的文件类型: {ext}"
            }
        
        pages = []
        with open(file_path, 'rb') as f:
            async for page in parser.parse(f):
                pages.append({
                    "page_num": page.page_num,
                    "text": page.text,
                    "offset": page.offset
                })
        
        return {
            "file": str(file_path),
            "status": "success",
            "pages": len(pages),
            "content": pages
        }
    
    except Exception as e:
        return {
            "file": str(file_path),
            "status": "error",
            "error": str(e)
        }


async def batch_process(directory: str, extensions: list = None):
    """批量处理目录中的文档"""
    if extensions is None:
        extensions = ['.pdf', '.html', '.htm', '.xlsx', '.xls']
    
    # 查找所有支持的文件
    path = Path(directory)
    files = [f for f in path.rglob('*') if f.suffix.lower() in extensions]
    
    print(f"找到 {len(files)} 个文件待处理")
    
    # 并发处理所有文件
    tasks = [process_file(f) for f in files]
    results = await asyncio.gather(*tasks)
    
    # 统计结果
    success = sum(1 for r in results if r['status'] == 'success')
    errors = sum(1 for r in results if r['status'] == 'error')
    skipped = sum(1 for r in results if r['status'] == 'skipped')
    
    print(f"\n处理完成:")
    print(f"  成功: {success}")
    print(f"  错误: {errors}")
    print(f"  跳过: {skipped}")
    
    # 显示错误详情
    if errors > 0:
        print("\n错误详情:")
        for r in results:
            if r['status'] == 'error':
                print(f"  {r['file']}: {r['error']}")
    
    return results


async def main():
    # 处理当前目录下的所有文档
    directory = "."
    results = await batch_process(directory)
    
    # 保存结果到文件
    import json
    with open("processing_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print("\n结果已保存到 processing_results.json")


if __name__ == "__main__":
    asyncio.run(main())
