"""
Parser factory for selecting appropriate parser based on file type.
"""
from typing import Optional
from .parser import Parser
from .pdfparser import LocalPdfParser
from .htmlparser import LocalHTMLParser
from .excelparser import ExcelParser


def get_parser_for_file(file_extension: str) -> Optional[Parser]:
    """Get appropriate parser for file extension"""
    ext = file_extension.lower()
    
    if ext == '.pdf':
        return LocalPdfParser()
    elif ext in ['.html', '.htm']:
        return LocalHTMLParser()
    elif ext in ['.xlsx', '.xls']:
        return ExcelParser()
    
    return None
