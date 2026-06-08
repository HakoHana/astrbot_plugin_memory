"""修复：将 atom 均匀分配到同日期的各篇日记上"""
import sqlite3
from collections import defaultdict

db = sqlite3.connect("/home/hako/data/plugin_data/Memory/memory.db")
db.row_factory = sqlite3.Row

# 1. 读取所有日记（按日期分组，按创建时间排序）
diaries = db.execute("""
    SELECT id, date, created_at FROM diary_entries ORDER BY date, created_at ASC
""").fetchall()

# 2. 读取所有原子（按日期分组，按创建时间排序）
atoms = db.execute("""
    SELECT id, diary_date, created_at FROM memory_atoms ORDER BY diary_date, created_at ASC
""").fetchall()

# 3. 按日期分组
diaries_by_date = defaultdict(list)
for d in diaries:
    diaries_by_date[d["date"]].append(d)

atoms_by_date = defaultdict(list)
for a in atoms:
    atoms_by_date[a["diary_date"]].append(a)

# 4. 分配：轮询分配，第 N 个 atom → 第 N % count 个 diary
linked = 0
for date_str, day_atoms in atoms_by_date.items():
    day_diaries = diaries_by_date.get(date_str, [])
    if not day_diaries:
        continue
    for i, atom in enumerate(day_atoms):
        diary = day_diaries[i % len(day_diaries)]
        db.execute("UPDATE memory_atoms SET diary_id = ? WHERE id = ?", (diary["id"], atom["id"]))
        linked += 1

db.commit()

# 5. 统计
stats = db.execute("""
    SELECT
        CASE WHEN cnt = 0 THEN '0'
             WHEN cnt = 1 THEN '1'
             WHEN cnt = 2 THEN '2'
             WHEN cnt >= 3 THEN '3+'
        END as grp,
        COUNT(*) as diaries
    FROM (
        SELECT de.id, COUNT(ma.id) as cnt
        FROM diary_entries de
        LEFT JOIN memory_atoms ma ON ma.diary_id = de.id
        GROUP BY de.id
    ) GROUP BY grp ORDER BY grp
""").fetchall()

print(f"已链接: {linked} 条原子")
print("日记分布:")
for s in stats:
    print(f"  每篇 {s['grp']} 条原子: {s['diaries']} 篇")

# 最终校验
zero = db.execute("SELECT COUNT(*) FROM memory_atoms WHERE diary_id = 0").fetchone()[0]
print(f"未链接: {zero} 条")

db.close()
