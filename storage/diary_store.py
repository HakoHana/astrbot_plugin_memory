"""日记文件存储 — Markdown 文件操作"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


class DiaryStore:
    """日记存储：按用户/年/月 组织的 Markdown 文件"""

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir) / "diaries"

    def _user_dir(self, user_id: str) -> Path:
        return self.base_dir / user_id

    def _file_path(self, user_id: str, date_str: str) -> Path:
        """获取日记文件路径，如 diaries/hako/2026/06/05.md"""
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"日期格式错误，需要 YYYY-MM-DD: {date_str}")
        return self._user_dir(user_id) / str(dt.year) / f"{dt.month:02d}" / f"{dt.day:02d}.md"

    async def append(self, user_id: str, date_str: str, content: str):
        """追加内容到当日日记文件（不存在则创建）"""
        path = self._file_path(user_id, date_str)
        path.parent.mkdir(parents=True, exist_ok=True)

        header = ""
        if not path.exists():
            header = (
                f"---\ndate: {date_str}\nuser_id: {user_id}\n---\n\n"
            )

        mode = "a" if path.exists() else "w"
        with open(path, mode, encoding="utf-8") as f:
            if header:
                f.write(header)
            # 追加时间标记
            now = datetime.now().strftime("%H:%M")
            f.write(f"\n## {now}\n\n{content.strip()}\n")

    async def read(self, user_id: str, date_str: str) -> str | None:
        """读取某天的日记"""
        path = self._file_path(user_id, date_str)
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    async def list_months(self, user_id: str) -> list[dict[str, str]]:
        """列出所有有日记的年月"""
        user_dir = self._user_dir(user_id)
        if not user_dir.exists():
            return []
        months = []
        for year_dir in sorted(user_dir.iterdir(), reverse=True):
            if not year_dir.is_dir() or not year_dir.name.isdigit():
                continue
            for month_dir in sorted(year_dir.iterdir(), reverse=True):
                if not month_dir.is_dir() or not month_dir.name.isdigit():
                    continue
                months.append({
                    "year": year_dir.name,
                    "month": month_dir.name,
                })
        return months

    async def list_dates(self, user_id: str, year: str, month: str) -> list[dict]:
        """列出某个月份所有日记日期"""
        user_dir = self._user_dir(user_id)
        month_path = user_dir / year / month
        if not month_path.exists():
            return []
        dates = []
        for f in sorted(month_path.iterdir(), reverse=True):
            if f.suffix == ".md" and f.stem.isdigit():
                dates.append({
                    "date": f"{year}-{month}-{f.stem}",
                    "file": str(f),
                })
        return dates

    async def delete_date(self, user_id: str, date_str: str) -> bool:
        """删除某天的日记文件"""
        path = self._file_path(user_id, date_str)
        if path.exists():
            path.unlink()
            return True
        return False

    async def get_all_user_ids(self) -> list[str]:
        """获取所有有日记的用户 ID"""
        if not self.base_dir.exists():
            return []
        return [d.name for d in self.base_dir.iterdir() if d.is_dir()]
