import os
from dotenv import load_dotenv
import requests
from pathlib import Path
from typing import Optional

load_dotenv()


class AiopToolsParser:
    """解析文档文件通过调用远程API"""
    
    BASE_URL = os.getenv("AIOP_TOOLS_BASE_URL")
    
    def parse_file(self, file_path: str) -> Optional[str]:
        """
        解析文件并返回markdown内容
        
        Args:
            file_path: 文件路径
            
        Returns:
            markdown内容字符串，失败返回None
        """
        path = Path(file_path)
        ext = path.suffix.lower()
        
        if ext in ['.xlsx', '.xls']:
            return self._parse_excel(file_path)
        elif ext == '.csv':
            return self._parse_csv(file_path)
        elif ext in ['.doc', '.docx', '.ppt', '.pptx']:
            return self._parse_document(file_path)
        elif ext == '.pdf':
            return self._parse_pdf(file_path)
        else:
            raise ValueError(f"不支持的文件格式: {ext}")
    
    def _parse_excel(self, file_path: str) -> Optional[str]:
        """解析Excel文件"""
        url = f"{self.BASE_URL}/excel2md/upload"
        
        with open(file_path, 'rb') as f:
            files = {'file': f}
            data = {
                'merge_small_slices': 'true',
                'merge_threshold': '600',
                'header_rows': '20',
                'slice_size': '500',
                'content_density_threshold': '0.2',
                'max_header_rows': '20',
                'output_format': 'markdown',
                'min_merge_size': '400',
                'min_header_rows': '1'
            }
            
            response = requests.post(url, files=files, data=data)
            response.raise_for_status()
            return response.json().get('markdown')
    
    def _parse_csv(self, file_path: str) -> Optional[str]:
        """解析CSV文件"""
        url = f"{self.BASE_URL}/csv2md/upload"
        
        with open(file_path, 'rb') as f:
            files = {'file': f}
            data = {
                'merge_small_slices': 'true',
                'merge_threshold': '600',
                'header_rows': '20',
                'slice_size': '500',
                'content_density_threshold': '0.2',
                'max_header_rows': '20',
                'output_format': 'markdown',
                'min_merge_size': '400',
                'min_header_rows': '1'
            }
            
            response = requests.post(url, files=files, data=data)
            response.raise_for_status()
            return response.json().get('markdown')
    
    def _parse_document(self, file_path: str) -> Optional[str]:
        """解析Word/PPT文件"""
        url = f"{self.BASE_URL}/document-convert/document2md_by_file"
        
        with open(file_path, 'rb') as f:
            files = {'file': f}
            data = {'analyze_images': 'true'}
            
            response = requests.post(url, files=files, data=data)
            response.raise_for_status()
            result = response.json()
            return result.get('data', {}).get('markdown_content')
    
    def _parse_pdf(self, file_path: str) -> Optional[str]:
        """解析PDF文件"""
        url = f"{self.BASE_URL}/document-convert/pdf2md_by_file"
        
        with open(file_path, 'rb') as f:
            files = {'file': f}
            data = {'merge_pages': 'false'}
            
            response = requests.post(url, files=files, data=data)
            response.raise_for_status()
            result = response.json()
            return result.get('data')
