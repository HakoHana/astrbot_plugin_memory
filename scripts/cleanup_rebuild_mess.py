"""清理：删除被 /记忆重构 污染的日记条目（正文为原子列表的）"""
import sqlite3

db = sqlite3.connect("/home/hako/data/plugin_data/Memory/memory.db")

# 找出被污染的日记（正文以 atom 列表开头）
contaminated = db.execute("""
    SELECT id FROM diary_entries
    WHERE content LIKE '%- [%'
""").fetchall()

if not contaminated:
    print("✅ 没有需要清理的条目")
    db.close()
    exit(0)

ids = [r[0] for r in contaminated]
placeholders = ",".join("?" * len(ids))

# 删除这些日记关联的原子
del_atoms = db.execute(
    f"SELECT COUNT(*) FROM memory_atoms WHERE diary_id IN ({placeholders})", ids
).fetchone()[0]
db.execute(
    f"UPDATE memory_atoms SET status='forgotten' WHERE diary_id IN ({placeholders})", ids
)

# 删除 FTS 中关联的原子
db.execute(
    f"DELETE FROM memory_atoms_fts WHERE atom_id IN (SELECT id FROM memory_atoms WHERE diary_id IN ({placeholders}))", ids
)

# 删除污染日记
db.execute(
    f"DELETE FROM diary_entries WHERE id IN ({placeholders})", ids
)

db.commit()

# 统计
remaining_diaries = db.execute("SELECT COUNT(*) FROM diary_entries").fetchone()[0]
remaining_atoms = db.execute("SELECT COUNT(*) FROM memory_atoms WHERE status='active'").fetchone()[0]
orphan_atoms = db.execute("SELECT COUNT(*) FROM memory_atoms WHERE status='active' AND diary_id NOT IN (SELECT id FROM diary_entries)").fetchone()[0]

print(f"🧹 已清理 {len(ids)} 条污染日记")
print(f"🗑️  标记 {del_atoms} 条原子为 forgotten")
print(f"📊 剩余日记: {remaining_diaries}")
print(f"📊 剩余活跃原子: {remaining_atoms}")
print(f"⚠️  孤立原子（无关联日记）: {orphan_atoms}")

db.close()
