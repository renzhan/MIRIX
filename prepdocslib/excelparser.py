import logging
import json
from collections.abc import AsyncGenerator
from typing import IO
import pandas as pd
from openpyxl import load_workbook
from .page import Page
from .parser import Parser

logger = logging.getLogger("scripts")


class ExcelParser(Parser):
    """
    将 Excel (xls / xlsx) 文件流解析为 Page 对象
    - 读取所有 sheet
    - 取第一行做表头
    - 输出格式: {"列名":"行值", ...}
    """

    async def parse(self, content: IO) -> AsyncGenerator[Page, None]:
        logger.info("Extracting data from '%s' using pandas/openpyxl", content.name)
        
        wb = load_workbook(content, data_only=True)
        offset, page_idx = 0, 0
        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]

            data = sheet.values
            try:
                cols = next(data)
            except StopIteration:
                continue
            df = pd.DataFrame(data, columns=cols)

            df.dropna(how='all', inplace=True)
            for index, row in df.iterrows():
                page_content = {}
                for col_index, (k, v) in enumerate(row.items()):
                    if pd.notna(v):
                        cell = sheet.cell(row=index + 2, column=col_index + 1)
                        if cell.hyperlink:
                            value = f"[{v}]({cell.hyperlink.target})"
                            page_content[k] = value
                        else:
                            page_content[k] = str(v)
                json_str = json.dumps(page_content) + ";"
                yield Page(page_idx, offset, json_str)
                offset += len(page_content) + 1
                page_idx += 1