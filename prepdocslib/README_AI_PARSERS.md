# AI-Powered Document Parsers

## 概述

这个库提供了基于 OpenAI 模型的文档解析器，优先使用 AI 模型来理解文档内容。

## 支持的文档类型

### 1. PDF 文档 (AIPdfParser)
- **模型**: GPT-4o (Vision)
- **方法**: 将 PDF 每页转换为图像，使用视觉模型提取文本
- **优势**: 可以处理扫描文档、复杂布局、图表等
- **备选**: LocalPdfParser (PyPDF2) - 用于纯文本 PDF

### 2. HTML 文档 (AIHTMLParser)
- **模型**: GPT-4o-mini
- **方法**: 使用 BeautifulSoup 清理 HTML，然后用 AI 提取结构化内容
- **优势**: 智能去除导航、广告等无关内容，提取主要内容
- **备选**: LocalHTMLParser (BeautifulSoup) - 基础文本提取

### 3. Excel 文档 (AIExcelParser)
- **模型**: GPT-4o (Vision)
- **方法**: 将 Excel 表格转换为图像，使用视觉模型理解表格结构
- **优势**: 可以理解复杂的表格结构、合并单元格、公式等
- **备选**: ExcelParser (pandas/openpyxl) - 基础数据提取

## 使用方法

### 基础使用

```python
from prepdocslib.parser_factory import get_parser_for_file
import os

# 设置 OpenAI API Key
openai_api_key = os.environ.get("OPENAI_API_KEY")

# 获取 PDF 解析器 (自动使用 AI 解析器)
parser = get_parser_for_file('.pdf', openai_api_key=openai_api_key)

# 解析文件
with open('document.pdf', 'rb') as f:
    async for page in parser.parse(f):
        print(f"Page {page.page_num}: {page.text}")
```

### 手动选择解析器

```python
from prepdocslib.parser_factory import create_pdf_parser, create_html_parser, create_excel_parser

# PDF 解析器
pdf_parser = create_pdf_parser(
    openai_api_key="your-api-key",
    use_ai=True,
    model="gpt-4o"
)

# HTML 解析器
html_parser = create_html_parser(
    openai_api_key="your-api-key",
    use_ai=True,
    model="gpt-4o-mini"
)

# Excel 解析器
excel_parser = create_excel_parser(
    openai_api_key="your-api-key",
    use_ai=True,
    model="gpt-4o"
)
```

### 禁用 AI 解析器

```python
# 使用传统解析器
parser = get_parser_for_file('.pdf', openai_api_key=None, use_ai=False)
# 或
parser = create_pdf_parser(use_ai=False)
```

## 依赖项

需要安装以下额外依赖：

```bash
pip install pdf2image pillow openai
```

对于 pdf2image，还需要安装 poppler:
- **Windows**: 下载 poppler 并添加到 PATH
- **macOS**: `brew install poppler`
- **Linux**: `apt-get install poppler-utils`

## 配置

### 环境变量

```bash
export OPENAI_API_KEY="your-api-key"
```

### 模型选择

- **PDF**: 推荐 `gpt-4o` (视觉能力强)
- **HTML**: 推荐 `gpt-4o-mini` (成本效益高)
- **Excel**: 推荐 `gpt-4o` (理解复杂表格)

## 成本考虑

虽然不考虑成本，但了解使用情况有助于优化：

- **GPT-4o**: $2.50/1M input tokens, $10.00/1M output tokens
- **GPT-4o-mini**: $0.15/1M input tokens, $0.60/1M output tokens

对于大量文档处理，建议：
1. 使用 GPT-4o-mini 处理简单文档
2. 使用 GPT-4o 处理复杂文档
3. 批量处理以提高效率

## 性能优化

### 1. 异步处理

```python
import asyncio

async def process_documents(files):
    tasks = []
    for file_path in files:
        parser = get_parser_for_file(
            os.path.splitext(file_path)[1],
            openai_api_key=api_key
        )
        with open(file_path, 'rb') as f:
            tasks.append(parser.parse(f))
    
    results = await asyncio.gather(*tasks)
    return results
```

### 2. 缓存结果

```python
import hashlib
import json

def get_cache_key(file_path):
    with open(file_path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()

# 使用缓存避免重复解析
cache = {}
cache_key = get_cache_key(file_path)
if cache_key in cache:
    return cache[cache_key]
```

## 故障排除

### PDF 转图像失败
- 确保安装了 poppler
- 检查 PDF 文件是否损坏

### OpenAI API 错误
- 检查 API key 是否有效
- 检查是否超过速率限制
- 检查网络连接

### 内存不足
- 对于大文件，考虑分批处理
- 减少图像分辨率

## 示例

查看 `examples/` 目录获取更多使用示例。
