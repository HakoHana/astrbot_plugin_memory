"""索引一致性检查 — 启动时检测数据完整性并自动修复"""

from __future__ import annotations

from .base_store import BaseDbStore


class IndexValidator(BaseDbStore):
    """检查各表与索引的一致性"""

    async def validate_all(self) -> dict[str, dict]:
        """运行全部检查，返回每项的检查结果"""
        results = {}

        results["atoms_fts"] = await self._check_atoms_fts()
        results["atom_ids"] = await self._check_atom_ids()
        results["graph_integrity"] = await self._check_graph_integrity()
        # 孤立原子检查已移至 memory_core 层（分库后需跨 storage.py 查询）

        results["summary"] = {
            "all_passed": all(r.get("passed", False) for r in results.values() if isinstance(r, dict)),
            "total_issues": sum(len(r.get("issues", [])) for r in results.values() if isinstance(r, dict)),
        }

        return results

    async def _check_atoms_fts(self) -> dict:
        """检查 memory_atoms 与 FTS5 索引是否一致"""
        result = {"name": "原子FTS索引", "passed": True, "issues": []}
        async with self._connect() as db:
            atoms_count = (await db.execute_fetchall("SELECT COUNT(*) FROM memory_atoms"))[0][0]
            fts_count = (await db.execute_fetchall("SELECT COUNT(*) FROM memory_atoms_fts"))[0][0]

        if atoms_count != fts_count:
            result["passed"] = False
            result["issues"].append(f"原子表 {atoms_count} 行 vs FTS {fts_count} 行")
            # 自动修复
            await self._rebuild_fts()
            result["fixed"] = True
        return result

    async def _check_atom_ids(self) -> dict:
        """检查原子的 ID 连续性"""
        result = {"name": "原子ID", "passed": True, "issues": []}
        async with self._connect() as db:
            max_id = (await db.execute_fetchall("SELECT MAX(id) FROM memory_atoms"))[0][0]
            total = (await db.execute_fetchall("SELECT COUNT(*) FROM memory_atoms"))[0][0]
        if max_id and total:
            expected_max = total
            if max_id > expected_max * 1.5:
                result["issues"].append(f"ID 不连续: max={max_id}, count={total}")
        return result

    async def _check_graph_integrity(self) -> dict:
        """图谱完整性检查 — 旧表已移除（graph_nodes/graph_edges 在 graph.db）"""
        return {"name": "图谱完整性", "passed": True, "issues": []}

    async def _fix_dangling_edges(self):
        pass

    async def _check_orphan_atoms(self) -> dict:
        """检查没有关联日记的原子"""
        result = {"name": "孤立原子", "passed": True, "issues": []}
        async with self._connect() as db:
            orphans = await db.execute_fetchall("""
                SELECT COUNT(*) FROM memory_atoms a
                WHERE a.diary_date NOT IN (SELECT date FROM diary_entries)
            """)
        if orphans and orphans[0][0] > 0:
            result["issues"].append(f"未关联日记的原子: {orphans[0][0]} 条")
        return result

    async def _rebuild_fts(self):
        """重建 FTS5 索引"""
        async with self._connect() as db:
            await db.execute("DELETE FROM memory_atoms_fts")
            atoms = await db.execute_fetchall(
                "SELECT id, content, user_id FROM memory_atoms"
            )
            for a in atoms:
                await db.execute(
                    "INSERT INTO memory_atoms_fts(atom_id, content, user_id) VALUES (?,?,?)",
                    (a[0], a[1], a[2]),
                )
            await db.commit()
