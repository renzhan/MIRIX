"""Attachment parsing utilities shared across services."""

from __future__ import annotations

import logging
import mimetypes
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse, unquote

import requests

from .aioptools_parser import AiopToolsParser
from .parser_factory import get_parser_for_file

logger = logging.getLogger(__name__)

MAX_ZIP_FILES = 10
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
OSS_ATTACHMENT_BASE_URL = os.getenv(
    "OSS_ATTACHMENT_BASE_URL",
    "http://share-item.oss-cn-shanghai.aliyuncs.com/aiop/ai-lab/",
)


def build_download_url(key: str) -> str:
    if not key:
        return ""
    if key.startswith("http://") or key.startswith("https://"):
        return key
    base = OSS_ATTACHMENT_BASE_URL or ""
    if not base.endswith('/'):
        base += '/'
    return base + key.lstrip('/')


def _detect_extension(url: str, original_filename: Optional[str], detected_ext: Optional[str]) -> str:
    candidates = []
    if detected_ext:
        candidates.append(detected_ext.lower())

    parsed_path = unquote(urlparse(url).path)
    suffix = Path(parsed_path).suffix.lower()
    if suffix:
        candidates.append(suffix)

    if original_filename:
        orig_suffix = Path(original_filename).suffix.lower()
        if orig_suffix:
            candidates.append(orig_suffix)

    for ext in candidates:
        if ext:
            return ext
    return ""


def download_file(url: str, original_filename: Optional[str] = None) -> Tuple[bytes, str]:
    response = requests.get(url, timeout=30)
    response.raise_for_status()

    content_type = response.headers.get('Content-Type', '')
    detected_ext = mimetypes.guess_extension(content_type)

    ext = _detect_extension(url, original_filename, detected_ext)
    return response.content, ext


def extract_zip_safely(zip_path: str) -> str:
    extracted_contents = []

    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            file_list = zip_ref.namelist()[:MAX_ZIP_FILES]

            for index, file_name in enumerate(file_list):
                file_info = zip_ref.getinfo(file_name)

                if file_info.file_size > MAX_FILE_SIZE:
                    extracted_contents.append(
                        f"[跳过: 附件{index+1}: {file_name} - 文件过大 ({file_info.file_size / 1024 / 1024:.1f}MB)]"
                    )
                    continue

                if file_name.startswith('/') or '..' in file_name:
                    continue

                if not file_info.is_dir():
                    file_ext = Path(file_name).suffix.lower()

                    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
                        tmp.write(zip_ref.read(file_name))
                        tmp_path = tmp.name

                    try:
                        aiop_parser = AiopToolsParser()
                        try:
                            content = aiop_parser.parse_file(tmp_path)
                            if content:
                                extracted_contents.append(
                                    f"📄 附件{index+1}: {file_name}:\n{content[:1000]}"
                                )
                                continue
                        except Exception as e:
                            logger.warning(f"AiopTools解析失败: {file_name}, 错误: {str(e)}")

                        parser = get_parser_for_file(file_ext)
                        if parser:
                            parsed_text = []
                            with open(tmp_path, 'rb') as f:
                                for page in parser.parse(f):
                                    parsed_text.append(page.text)
                            extracted_contents.append(
                                f"📄 附件{index+1}: {file_name}:\n{''.join(parsed_text)[:1000]}"
                            )
                        else:
                            extracted_contents.append(f"[不支持: 附件{index+1}: {file_name}]")
                    finally:
                        Path(tmp_path).unlink(missing_ok=True)

            if len(zip_ref.namelist()) > MAX_ZIP_FILES:
                extracted_contents.append(
                    f"[仅显示前{MAX_ZIP_FILES}个附件，共{len(zip_ref.namelist())}个]"
                )

    except zipfile.BadZipFile:
        return "[ZIP附件损坏]"
    except Exception as e:
        return f"[ZIP附件解压失败: {str(e)}]"

    return "\n\n".join(extracted_contents) if extracted_contents else "[ZIP附件为空]"


def extract_tar_safely(tar_path: str) -> str:
    import tarfile
    extracted_contents = []
    extract_dir = tempfile.mkdtemp()

    try:
        with tarfile.open(tar_path, 'r:*') as tar:
            members = tar.getmembers()[:MAX_ZIP_FILES]

            for index, member in enumerate(members):
                if member.name.startswith('/') or '..' in member.name:
                    continue
                if member.isdir():
                    continue
                if member.size > MAX_FILE_SIZE:
                    extracted_contents.append(
                        f"[跳过: 附件{index+1}: {member.name} - 文件过大 ({member.size / 1024 / 1024:.1f}MB)]"
                    )
                    continue

                file_ext = Path(member.name).suffix.lower()
                with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
                    f = tar.extractfile(member)
                    if f:
                        tmp.write(f.read())
                        tmp_path = tmp.name

                        try:
                            aiop_parser = AiopToolsParser()
                            try:
                                content = aiop_parser.parse_file(tmp_path)
                                if content:
                                    extracted_contents.append(f"📄 附件{index+1}: {member.name}:\n{content[:1000]}")
                                    continue
                            except Exception as e:
                                logger.warning(f"AiopTools解析失败: {member.name}, 错误: {str(e)}")

                            parser = get_parser_for_file(file_ext)
                            if parser:
                                parsed_text = []
                                with open(tmp_path, 'rb') as pf:
                                    for page in parser.parse(pf):
                                        parsed_text.append(page.text)
                                extracted_contents.append(
                                    f"📄 附件{index+1}: {member.name}:\n{''.join(parsed_text)[:1000]}"
                                )
                            else:
                                extracted_contents.append(f"[不支持: 附件{index+1}: {member.name}]")
                        finally:
                            Path(tmp_path).unlink(missing_ok=True)

            if len(tar.getmembers()) > MAX_ZIP_FILES:
                extracted_contents.append(
                    f"[仅显示前{MAX_ZIP_FILES}个附件，共{len(tar.getmembers())}个]"
                )

    except Exception as e:
        return f"[TAR附件解析失败: {str(e)}]"
    finally:
        import shutil
        shutil.rmtree(extract_dir, ignore_errors=True)

    return "\n\n".join(extracted_contents) if extracted_contents else "[TAR附件为空]"


def extract_7z_safely(archive_path: str) -> str:
    try:
        import py7zr
    except ImportError:
        return "[7Z附件解析失败: 缺少py7zr库，请执行: pip install py7zr]"

    extracted_contents = []
    extract_dir = tempfile.mkdtemp()

    try:
        with py7zr.SevenZipFile(archive_path, 'r') as archive:
            archive.extractall(extract_dir)

        files = []
        for root, _, filenames in os.walk(extract_dir):
            for filename in filenames:
                files.append(os.path.join(root, filename))

        files = files[:MAX_ZIP_FILES]

        for index, file_path in enumerate(files):
            file_name = os.path.relpath(file_path, extract_dir)
            file_size = os.path.getsize(file_path)

            if file_size > MAX_FILE_SIZE:
                extracted_contents.append(
                    f"[跳过: 附件{index+1}: {file_name} - 文件过大 ({file_size / 1024 / 1024:.1f}MB)]"
                )
                continue

            try:
                aiop_parser = AiopToolsParser()
                try:
                    content = aiop_parser.parse_file(file_path)
                    if content:
                        extracted_contents.append(
                            f"📄 附件{index+1}: {file_name}:\n{content[:1000]}"
                        )
                        continue
                except Exception as e:
                    logger.warning(f"AiopTools解析失败: {file_name}, 错误: {str(e)}")

                file_ext = Path(file_name).suffix.lower()
                parser = get_parser_for_file(file_ext)
                if parser:
                    parsed_text = []
                    with open(file_path, 'rb') as f:
                        for page in parser.parse(f):
                            parsed_text.append(page.text)
                    extracted_contents.append(
                        f"📄 附件{index+1}: {file_name}:\n{''.join(parsed_text)[:1000]}"
                    )
                else:
                    extracted_contents.append(f"[不支持: 附件{index+1}: {file_name}]")
            except Exception as e:
                logger.warning(f"解析文件失败: {file_name}, 错误: {str(e)}")

        total_files = len([f for root, _, filenames in os.walk(extract_dir) for f in filenames])
        if total_files > MAX_ZIP_FILES:
            extracted_contents.append(
                f"[仅显示前{MAX_ZIP_FILES}个附件，共{total_files}个]"
            )

    except Exception as e:
        return f"[7Z附件解析失败: {str(e)}]"
    finally:
        import shutil
        shutil.rmtree(extract_dir, ignore_errors=True)

    return "\n\n".join(extracted_contents) if extracted_contents else "[7Z附件为空]"


def extract_rar_safely(rar_path: str) -> str:
    try:
        import rarfile
    except ImportError:
        return "[RAR附件解析失败: 缺少rarfile库，请执行: pip install rarfile]"

    extracted_contents = []
    try:
        with rarfile.RarFile(rar_path) as rf:
            names = rf.namelist()[:MAX_ZIP_FILES]

            for index, name in enumerate(names):
                info = rf.getinfo(name)

                if name.startswith('/') or '..' in name:
                    continue

                if info.isdir():
                    continue

                if info.file_size > MAX_FILE_SIZE:
                    extracted_contents.append(
                        f"[跳过: 附件{index+1}: {name} - 文件过大 ({info.file_size / 1024 / 1024:.1f}MB)]"
                    )
                    continue

                file_ext = Path(name).suffix.lower()
                with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
                    with rf.open(info) as src:
                        tmp.write(src.read())
                    tmp_path = tmp.name

                try:
                    aiop_parser = AiopToolsParser()
                    try:
                        content = aiop_parser.parse_file(tmp_path)
                        if content:
                            extracted_contents.append(f"📄 附件{index+1}: {name}:\n{content[:1000]}")
                            continue
                    except Exception as e:
                        logger.warning(f"AiopTools解析失败: {name}, 错误: {str(e)}")

                    parser = get_parser_for_file(file_ext)
                    if parser:
                        parsed_text = []
                        with open(tmp_path, 'rb') as f:
                            for page in parser.parse(f):
                                parsed_text.append(page.text)
                        extracted_contents.append(
                            f"📄 附件{index+1}: {name}:\n{''.join(parsed_text)[:1000]}"
                        )
                    else:
                        extracted_contents.append(f"[不支持: 附件{index+1}: {name}]")
                finally:
                    Path(tmp_path).unlink(missing_ok=True)

            if len(rf.namelist()) > MAX_ZIP_FILES:
                extracted_contents.append(
                    f"[仅显示前{MAX_ZIP_FILES}个附件，共{len(rf.namelist())}个]"
                )

    except rarfile.NotRarFile:
        return "[RAR附件损坏或非RAR格式]"
    except rarfile.RarCannotExec as e:
        return f"[RAR附件解析失败: 未找到解压后端（unar/unrar/bsdtar）。{e}]"
    except Exception as e:
        return f"[RAR附件解析失败: {str(e)}]"

    return "\n\n".join(extracted_contents) if extracted_contents else "[RAR附件为空]"


def parse_attachment_from_url(url: str, original_filename: Optional[str] = None) -> str:
    try:
        file_content, _ = download_file(url, original_filename)

        if len(file_content) > MAX_FILE_SIZE:
            return f"[文件过大: {len(file_content) / 1024 / 1024:.1f}MB，限制{MAX_FILE_SIZE / 1024 / 1024}MB]"

        # 优先使用 original_filename 的扩展名，如果没有再从 URL 中提取
        suffix = ''
        if original_filename:
            suffix = Path(original_filename).suffix.lower()
        if not suffix:
            suffix = Path(unquote(urlparse(url).path)).suffix.lower()
        if not suffix:
            suffix = '.bin'

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name

        try:
            ext_lower = suffix.lower()
            if ext_lower == '.zip':
                return extract_zip_safely(tmp_path)
            if ext_lower == '.rar':
                return extract_rar_safely(tmp_path)
            if ext_lower in ['.tar', '.gz', '.bz2', '.xz', '.tgz', '.tbz2', '.txz']:
                return extract_tar_safely(tmp_path)
            if ext_lower == '.7z':
                return extract_7z_safely(tmp_path)

            # 尝试使用AiopToolsParser
            aiop_parser = AiopToolsParser()
            try:
                content = aiop_parser.parse_file(tmp_path)
                if content:
                    return content
            except Exception as e:
                logger.warning(f"AiopTools解析失败: {url}, 错误: {str(e)}")

            parser_ext = suffix
            parser = get_parser_for_file(parser_ext)
            if not parser:
                return f"[不支持的文件类型: {parser_ext or '未知'}]"

            parsed_text = []
            with open(tmp_path, 'rb') as f:
                for page in parser.parse(f):
                    parsed_text.append(page.text)
            return "\n\n".join(parsed_text)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    except Exception as e:
        logger.error(f"解析附件失败: {url}, 错误: {str(e)}")
        print(f"解析附件失败:error {url}, 错误: {str(e)}")
        return f"[附件解析失败: {str(e)}]"
