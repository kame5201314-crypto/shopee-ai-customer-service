#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知識庫載入器
支援多種檔案格式：.csv, .xlsx, .txt, .pdf

設計給非技術背景用戶使用：
- 只需將檔案放入 knowledge_base/ 資料夾
- 系統自動識別格式並載入
- 點擊「重整知識庫」即可更新
"""

import os
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# 預設知識庫資料夾
DEFAULT_KNOWLEDGE_FOLDER = "knowledge_base"


class KnowledgeLoader:
    """
    知識庫載入器

    自動掃描資料夾中的檔案並載入內容
    支援格式：.csv, .xlsx, .txt, .pdf
    """

    SUPPORTED_EXTENSIONS = {'.csv', '.xlsx', '.xls', '.txt', '.pdf'}

    def __init__(self, folder_path: str = DEFAULT_KNOWLEDGE_FOLDER):
        """
        初始化知識庫載入器

        Args:
            folder_path: 知識庫資料夾路徑
        """
        self.folder_path = Path(folder_path)
        self.loaded_files: Dict[str, dict] = {}  # 已載入的檔案資訊
        self.knowledge_content: str = ""  # 合併後的知識內容
        self.last_refresh: Optional[datetime] = None

        # 確保資料夾存在
        self.folder_path.mkdir(parents=True, exist_ok=True)

    def _read_csv(self, filepath: Path) -> str:
        """讀取 CSV 檔案"""
        try:
            import pandas as pd
            df = pd.read_csv(filepath, encoding='utf-8')
            # 轉換為易讀格式
            content = f"【{filepath.stem}】\n"
            content += df.to_string(index=False)
            return content
        except UnicodeDecodeError:
            # 嘗試其他編碼
            try:
                import pandas as pd
                df = pd.read_csv(filepath, encoding='big5')
                content = f"【{filepath.stem}】\n"
                content += df.to_string(index=False)
                return content
            except Exception as e:
                logger.error(f"讀取 CSV 失敗 ({filepath}): {e}")
                return f"[無法讀取 {filepath.name}]"
        except Exception as e:
            logger.error(f"讀取 CSV 失敗 ({filepath}): {e}")
            return f"[無法讀取 {filepath.name}]"

    def _read_excel(self, filepath: Path) -> str:
        """讀取 Excel 檔案 (.xlsx, .xls)"""
        try:
            import pandas as pd
            # 讀取所有工作表
            xlsx = pd.ExcelFile(filepath)
            content_parts = [f"【{filepath.stem}】"]

            for sheet_name in xlsx.sheet_names:
                df = pd.read_excel(filepath, sheet_name=sheet_name)
                content_parts.append(f"\n--- {sheet_name} ---")
                content_parts.append(df.to_string(index=False))

            return "\n".join(content_parts)
        except Exception as e:
            logger.error(f"讀取 Excel 失敗 ({filepath}): {e}")
            return f"[無法讀取 {filepath.name}]"

    def _read_txt(self, filepath: Path) -> str:
        """讀取純文字檔案"""
        encodings = ['utf-8', 'big5', 'gbk', 'cp950', 'latin-1']

        for encoding in encodings:
            try:
                with open(filepath, 'r', encoding=encoding) as f:
                    content = f.read()
                return f"【{filepath.stem}】\n{content}"
            except UnicodeDecodeError:
                continue
            except Exception as e:
                logger.error(f"讀取 TXT 失敗 ({filepath}): {e}")
                return f"[無法讀取 {filepath.name}]"

        return f"[無法識別 {filepath.name} 的編碼]"

    def _read_pdf(self, filepath: Path) -> str:
        """讀取 PDF 檔案"""
        try:
            import PyPDF2

            content_parts = [f"【{filepath.stem}】"]

            with open(filepath, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page_num, page in enumerate(reader.pages, 1):
                    text = page.extract_text()
                    if text.strip():
                        content_parts.append(f"\n--- 第 {page_num} 頁 ---")
                        content_parts.append(text.strip())

            return "\n".join(content_parts)
        except Exception as e:
            logger.error(f"讀取 PDF 失敗 ({filepath}): {e}")
            return f"[無法讀取 {filepath.name}]"

    def _read_file(self, filepath: Path) -> tuple[str, int]:
        """
        根據副檔名讀取檔案

        Returns:
            (內容, 字元數)
        """
        ext = filepath.suffix.lower()
        content = ""

        if ext == '.csv':
            content = self._read_csv(filepath)
        elif ext in {'.xlsx', '.xls'}:
            content = self._read_excel(filepath)
        elif ext == '.txt':
            content = self._read_txt(filepath)
        elif ext == '.pdf':
            content = self._read_pdf(filepath)
        else:
            content = f"[不支援的格式: {filepath.name}]"

        return content, len(content)

    def scan_files(self) -> List[dict]:
        """
        掃描資料夾中的所有支援檔案

        Returns:
            檔案資訊列表
        """
        files_info = []

        if not self.folder_path.exists():
            logger.warning(f"知識庫資料夾不存在: {self.folder_path}")
            return files_info

        for filepath in self.folder_path.iterdir():
            if filepath.is_file() and filepath.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                stat = filepath.stat()
                files_info.append({
                    "name": filepath.name,
                    "path": str(filepath),
                    "extension": filepath.suffix.lower(),
                    "size": stat.st_size,
                    "size_display": self._format_size(stat.st_size),
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
                })

        # 依檔名排序
        files_info.sort(key=lambda x: x["name"])
        return files_info

    def _format_size(self, size: int) -> str:
        """格式化檔案大小"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def load_all(self, force_reload: bool = False) -> dict:
        """
        載入所有知識庫檔案

        Args:
            force_reload: 是否強制重新載入

        Returns:
            載入結果
        """
        files_info = self.scan_files()

        if not files_info:
            self.knowledge_content = "[知識庫為空，請將檔案放入 knowledge_base 資料夾]"
            self.loaded_files = {}
            return {
                "success": True,
                "message": "知識庫為空",
                "files_count": 0,
                "total_chars": 0,
                "files": []
            }

        loaded_contents = []
        loaded_files = {}
        total_chars = 0
        errors = []

        for file_info in files_info:
            filepath = Path(file_info["path"])

            try:
                content, char_count = self._read_file(filepath)
                loaded_contents.append(content)
                total_chars += char_count

                loaded_files[file_info["name"]] = {
                    **file_info,
                    "char_count": char_count,
                    "status": "loaded"
                }

                logger.info(f"已載入: {file_info['name']} ({char_count} 字元)")

            except Exception as e:
                error_msg = f"載入失敗: {file_info['name']} - {str(e)}"
                errors.append(error_msg)
                logger.error(error_msg)

                loaded_files[file_info["name"]] = {
                    **file_info,
                    "char_count": 0,
                    "status": "error",
                    "error": str(e)
                }

        # 合併所有內容
        self.knowledge_content = "\n\n" + "="*50 + "\n\n".join(loaded_contents)
        self.loaded_files = loaded_files
        self.last_refresh = datetime.now()

        return {
            "success": len(errors) == 0,
            "message": f"已載入 {len(loaded_files) - len(errors)}/{len(files_info)} 個檔案",
            "files_count": len(loaded_files),
            "total_chars": total_chars,
            "files": list(loaded_files.values()),
            "errors": errors if errors else None,
            "last_refresh": self.last_refresh.isoformat()
        }

    def get_knowledge_content(self) -> str:
        """取得知識庫內容（用於 AI 提示）"""
        if not self.knowledge_content:
            self.load_all()
        return self.knowledge_content

    def get_status(self) -> dict:
        """取得知識庫狀態"""
        files_info = self.scan_files()

        return {
            "folder": str(self.folder_path),
            "folder_exists": self.folder_path.exists(),
            "files_count": len(files_info),
            "loaded_count": len(self.loaded_files),
            "total_chars": len(self.knowledge_content),
            "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
            "supported_formats": list(self.SUPPORTED_EXTENSIONS),
            "files": files_info
        }


# 全域實例
_knowledge_loader: Optional[KnowledgeLoader] = None


def get_knowledge_loader(folder_path: str = DEFAULT_KNOWLEDGE_FOLDER) -> KnowledgeLoader:
    """取得知識庫載入器單例"""
    global _knowledge_loader

    if _knowledge_loader is None or str(_knowledge_loader.folder_path) != folder_path:
        _knowledge_loader = KnowledgeLoader(folder_path)

    return _knowledge_loader


def load_knowledge_base(folder_path: str = DEFAULT_KNOWLEDGE_FOLDER, force_reload: bool = False) -> dict:
    """
    載入知識庫（便捷函式）

    Args:
        folder_path: 知識庫資料夾路徑
        force_reload: 是否強制重新載入

    Returns:
        載入結果
    """
    loader = get_knowledge_loader(folder_path)
    return loader.load_all(force_reload)


def get_knowledge_content(folder_path: str = DEFAULT_KNOWLEDGE_FOLDER) -> str:
    """取得知識庫內容（便捷函式）"""
    loader = get_knowledge_loader(folder_path)
    return loader.get_knowledge_content()


def get_knowledge_status(folder_path: str = DEFAULT_KNOWLEDGE_FOLDER) -> dict:
    """取得知識庫狀態（便捷函式）"""
    loader = get_knowledge_loader(folder_path)
    return loader.get_status()
