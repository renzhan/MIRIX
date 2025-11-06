import csv
import json
from collections.abc import AsyncGenerator
from io import BufferedReader
from typing import IO, cast

import pandas as pd               # 只用来判断 NaN，可移除

from .page import Page
from .parser import Parser


class CsvParser(Parser):
    """
    解析 CSV 文件流，每一行 => Page：
    page.page_text == '{"列1":"值1", "列2":"值2", ...}'
    """

    async def parse(self, content: IO, encoding: str = "utf-8") -> AsyncGenerator[Page, None]:
        # -------- 1) 把输入统一为 str -------------- #
        if isinstance(content, (bytes, bytearray)):
            text = bytes(content).decode(encoding)
        elif isinstance(content, BufferedReader) or hasattr(content, "read"):
            text = cast(BufferedReader, content).read().decode(encoding)
        else:
            raise TypeError("Unsupported content type")

        # -------- 2) 用 csv.DictReader 直接拿到列名映射 -------- #
        reader = csv.DictReader(text.splitlines())

        offset, page_idx = 0, 0
        for row in reader:
            # row 是 dict，空值为 ''；用 pandas 判断 NaN 更保险
            kv = {k: v for k, v in row.items() if v and pd.notna(v)}
            if not kv:
                continue

            json_str = json.dumps(kv, ensure_ascii=False)
            yield Page(page_idx, offset, json_str)

            offset += len(json_str) + 1   # +1 for newline
            page_idx += 1


