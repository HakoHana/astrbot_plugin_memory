# Memori 架构文档

**版本**: 0.1.0
**更新日期**: 2026-06-11

---

## 概述

Memori 是一个纯净的 Python 长期记忆内核。通过两个抽象接口（`LLMProvider` + `ContextProvider`）接入各类 Agent 框架，提供从对话中提取、存储、检索记忆的完整流水线。

### 设计目标

- **零框架依赖** — 核心纯 Python，`adapters` 模块定义接口由外部实现
- **异步非阻塞** — 记忆整理在后台队列执行，不阻塞消息实时响应
- **分级存储** — 日记（叙事）+ 原子（结构化事实）+ 图谱（实体关联）三层
- **双路检索** — BM25 文档路 + GraphEntity 图路 + RRF 融合

---

## 目录结构

```
memori/
├── memori/
│   ├── core/              # 业务逻辑核心
│   │   ├── adapters.py          # 抽象接口（LLMProvider / ContextProvider）
│   │   ├── memory_core.py       # 门面：统一初始化与模块装配
│   │   ├── capturer.py          # 抓取器：Judge→去重→合并 Capture
│   │   ├── warm_processor.py    # 异步队列消费者
│   │   ├── consolidation_manager.py  # 调度器：消息计数、触发条件、会话状态
│   │   ├── retriever.py         # 检索引擎：双路检索 + 内容去重
│   │   ├── memory_injector.py   # 记忆注入器：控制注入位置与模板
│   │   ├── atom_classifier.py   # 规则基原子分类器
│   │   ├── quality_validator.py # 输出质量校验
│   │   ├── graph_engine.py      # 知识图谱引擎
│   │   ├── persona_engine.py    # 用户画像引擎
│   │   ├── diary_helper.py      # 日记格式化（frontmatter + Markdown）
│   │   ├── context_formatter.py # 时间标签格式化
│   │   ├── hot_cache.py         # 热消息缓存（Ring Buffer）
│   │   ├── archiver.py          # 日记归档
│   │   ├── command_handler.py   # 指令处理
│   │   └── page_api.py          # WebUI 后台 API
│   │
│   ├── models/             # 数据模型
│   │   └── memory_atom.py       # MemoryAtom / AtomType / CaptureResult / RecallResult
│   │   └── graph_models.py      # GraphNode / GraphEdge / GraphEntry
│   │
│   ├── retrieval/          # 检索系统
│   │   ├── dual_route_retriever.py  # 双路检索编排
│   │   ├── bm25_retriever.py        # BM25 文档路
│   │   ├── graph_entity_retriever.py # GraphEntity 图路
│   │   └── rrf_fusion.py            # RRF 融合
│   │
│   ├── storage/            # 存储层
│   │   ├── atom_store.py         # 原子存储（FTS5 + 生命周期）
│   │   ├── diary_store.py        # 日记存储
│   │   ├── graph_store.py        # 图谱存储
│   │   ├── persona_store.py      # 画像存储
│   │   ├── conversation_store.py # 会话存储
│   │   ├── state_store.py        # 会话状态持久化
│   │   ├── base_store.py         # 基础 SQLite 操作
│   │   ├── db_migration.py       # 数据库迁移
│   │   ├── write_op_log.py       # 写操作日志（崩溃恢复）
│   │   └── index_validator.py    # 索引一致性验证
│   │
│   ├── api/                # HTTP 服务（可选）
│   │   ├── routes.py
│   │   ├── schemas.py
│   │   └── deps.py
│   │
│   └── prompts/            # LLM 提示词模板
│       ├── judge.txt             # 便宜模型：判断是否值得记
│       ├── merged.txt            # 昂贵模型：合并输出日记+原子
│       ├── diary.txt             # 写日记（备用）
│       ├── atoms.txt             # 提取原子（备用）
│       ├── private_chat.txt      # 私聊日记
│       ├── group_chat.txt        # 群聊日记
│       └── persona.txt           # 画像更新
│
├── docs/
│   ├── ARCHITECTURE.md           # 本文档
│   ├── configuration.md          # 配置说明
│   └── sylphid-echo-integration.md
│
├── webui/                 # Web 仪表盘
├── main.py                # 独立服务入口
├── setup.py / pyproject.toml
└── README.md
```

---

## 分层架构

```
┌─────────────────────────────────────────────────┐
│                  外部框架层                        │
│   AstrBot / NoneBot / 自有框架                     │
│   实现 LLMProvider + ContextProvider 接口           │
└──────────────────┬──────────────────────────────┘
                   │ 事件
┌──────────────────▼──────────────────────────────┐
│              MemoryCore (门面)                    │
│     process_message() / trigger_capture()         │
├─────────┬─────────────────────┬──────────────────┤
│ 检索路径  │     捕获路径        │   存储路径         │
│ (同步)   │    (后台异步)       │                   │
├─────────┼─────────────────────┼──────────────────┤
│Retriever│ ConsolidationMgr   │ AtomStore         │
│Injector │  → WarmProcessor   │ DiaryStore        │
│         │    → Judge         │ GraphStore        │
│         │    → 提前去重       │ PersonaStore      │
│         │    → Capture       │ ConversationStore │
│         │    → PersonaUpdate  │ StateStore        │
└─────────┴─────────────────────┴──────────────────┘
```

---

## 核心流程

### 消息处理路径（同步，阻塞回复）

```
用户消息
  │
  ├── HotCache.push()            ── 写入热缓存
  │
  ├── Retriever.get_context_memories()
  │     ├── recall()             ── 双路检索 + RRF 融合 + 内容去重
  │     │   ├── BM25Retriever    ── FTS5 全文检索（英文）+ LIKE（中文）
  │     │   ├── GraphEntityRetriever ── 实体匹配 → 边遍历 → 日记→事实
  │     │   └── RRF 融合         ──  Reciprocal Rank Fusion
  │     │
  │     ├── _dedup_atoms()       ── 内容级去重（精确匹配 + 二元组 Jaccard）
  │     └── 组装记忆文本          ── 【我记忆中最近的事】列表
  │
  ├── MemoryInjector.inject()    ── 注入到 system_prompt / user_message
  │
  └── 返回修改后的消息 → LLM 响应
```

### 记忆捕获路径（后台异步，不阻塞回复）

```
消息累积（ConsolidationManager）
  │ msg_count >= 阈值 或 超时
  ▼
WarmProcessor._process_one()
  │
  ├── 1. Judge（便宜 LLM）
  │     └── should_remember? → 否则结束
  │
  ├── 2. ★ 提前去重
  │     _apply_reinforcement(threshold=0.85)
  │     ├── FTS 检索 + 二元组 Jaccard 比对
  │     └── 命中 → 强化旧记忆（importance↑、expires_at↑），跳过昂贵模型
  │
  ├── 3. Capture（昂贵模型）
  │     _merged_capture()  ← 一次调用输出 diary + atoms
  │     ├── JSON 自动修复（_fix_json）
  │     ├── 质量校验（quality_validator，仅日志不拒写）
  │     ├── 规则基原子分类（atom_classifier）
  │     └── 返回 diary_body + atoms
  │
  ├── 4. 原子落库 + 去重强化
  │     _reinforce_if_duplicate(threshold=0.6)
  │     ├── 调用 _apply_reinforcement 做核心去重
  │     └── forgotten 清理
  │
  ├── 5. 图谱索引（GraphEngine，异步）
  │
  └── 6. 画像更新（PersonaEngine，每 10 次日记触发）
```

---

## 数据模型

### MemoryAtom（记忆原子）

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | str | 事实陈述，第三人称客观 |
| `atom_type` | AtomType | episodic / factual / preference / planned / relational |
| `entities` | list[str] | 涉及实体名 |
| `importance` | float | 0.0 ~ 1.0 |
| `confidence` | float | 0.0 ~ 1.0 |
| `diary_snippet` | str | 溯源原文 |
| `diary_id` | int | 关联日记 ID |
| `status` | AtomStatus | active / dormant / archived / forgotten |
| `expires_at` | float | 过期时间戳 |
| `decay_type` | DecayType | exponential / linear / step |

**TTL 策略**：

| 类型 | 基础 TTL | 衰减方式 |
|------|----------|----------|
| EPISODIC（事件） | 30 天 | 指数衰减 |
| FACTUAL（事实） | 180 天 | 指数衰减 |
| PREFERENCE（偏好） | 60 天 | 指数衰减 |
| PLANNED（计划） | 7 天 | 阶梯衰减 |
| RELATIONAL（关系） | 90 天 | 线性衰减 |

### Diary（日记）

```
---
date: 2026-06-11
mood: 开心
importance: 0.8
topics: ["用户分享重要信息"]
---

今天 Hako 告诉我他在学钢琴，每周三晚上上课。感觉他挺享受这个过程的。
```

### CaptureResult（抓取结果）

| 字段 | 类型 | 说明 |
|------|------|------|
| `wrote_diary` | bool | 是否写入了日记 |
| `diary_content` | str | 日记全文（含 frontmatter） |
| `atoms` | list[MemoryAtom] | 提取的原子列表 |

---

## 关键设计决策

### 1. 合并 LLM 调用

**问题**：旧流程中写日记和提取原子是两次独立昂贵模型调用。

**方案**：`merged.txt` prompt 让模型一次输出 JSON `{"diary": "...", "atoms": [...]}`。

**效果**：昂贵模型调用次数减少约 50%。

### 2. 规则基原子分类

**问题**：让 LLM 输出 `type`/`importance`/`confidence` 浪费 token，且分类不一致。

**方案**：LLM 只输出 `content` + `entities` + `diary_snippet`，类型由 `atom_classifier.py` 的正则模式匹配确定。

**匹配规则**：

| 模式 | 分类 | 置信度 |
|------|------|--------|
| 未来时间词 + 行动动词 | PLANNED | 0.85 |
| 过去时间词 + 行动动词 | EPISODIC | 0.80 |
| 偏好关键词 | PREFERENCE | 0.82 |
| 关系关键词 | RELATIONAL | 0.80 |
| 状态性描述（是/有） | FACTUAL | 0.78 |
| 仅有行动动词 | EPISODIC | 0.75 |
| 无匹配 | FACTUAL | 0.65 |

### 3. 三级去重策略

| 层级 | 位置 | 阈值 | 目的 |
|------|------|------|------|
| L0 | 检索时 `_dedup_atoms` | Jaccard ≥ 0.6 | 召回结果去重 |
| L1 | Capture 前提前去重 | Jaccard ≥ 0.85 | 跳过昂贵模型 |
| L2 | Capture 后 `_reinforce_if_duplicate` | Jaccard ≥ 0.6 | 兜底强化 |

### 4. 强化策略

- **步长递减**：首次 +0.05，第 N 次 `0.05 / log2(n+2)`，收敛到 0.01
- **融合 Judge**：`boosted = max(step_boosted, judge*0.7 + old*0.3)`
- **TTL 延长**：`expires_at` 延长 30%
- **日记同步**：`UPDATE diary_entries SET importance = MAX(importance, ?)`

### 5. 双路检索 + RRF

```
BM25 文档路（memory_atoms FTS5）
  ── ASCII 词 → FTS5 全文检索
  ── CJK 词   → LIKE %kw%（需 jieba 分词）
  
GraphEntity 图路（graph_nodes → graph_edges → diary → facts）
  ── 关键词匹配实体节点
  ── 沿 mentions / has_meta 边找到关联日记
  ── 日记拆解为原子事实

RRF 融合：
  score = Σ 1 / (k + rank)  对两路结果重排序
```

---

## 抽象接口

### LLMProvider

```python
class LLMProvider(ABC):
    async def chat(self, system_prompt: str, user_prompt: str) -> str: ...
    async def chat_with_judge(self, system_prompt: str, user_prompt: str) -> str: ...
```

`chat_with_judge` 默认等同 `chat`，可重写为使用更便宜的模型。

### ContextProvider

```python
class ContextProvider(ABC):
    def get_user_id(self, event) -> str: ...
    def get_conversation_text(self, event) -> str: ...
    def get_sender_name(self, event) -> str: ...
```

---

## 配置体系

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `trigger_msg_count` | 10 | 触发整理的累积消息数 |
| `trigger_time_minutes` | 360 | 最长时间间隔触发（分钟） |
| `recall_count` | 5 | 每次召回的记忆条数 |
| `recall_max_tokens` | 500 | 注入记忆的最大 token 预算 |
| `enable_rule_classifier` | True | 启用规则基原子分类 |
| `enable_quality_check` | True | 启用输出质量校验 |
| `enable_json_repair` | True | 启用 JSON 自动修复 |
| `enable_dual_summary` | False | 启用双通道摘要（需 schema 迁移） |
| `decay_enabled` | True | 启用重要度衰减 |
| `decay_rate` | 0.99 | 每日衰减系数 |
| `persona_update_interval` | 10 | 每 N 次日记更新一次画像 |

---

## 依赖关系

```
core/memory_core.py (门面)
  ├── core/retriever.py
  │     ├── retrieval/dual_route_retriever.py
  │     │     ├── retrieval/bm25_retriever.py
  │     │     ├── retrieval/graph_entity_retriever.py
  │     │     └── retrieval/rrf_fusion.py
  │     └── storage/atom_store.py
  │
  ├── core/consolidation_manager.py
  │     └── core/warm_processor.py
  │           ├── core/capturer.py
  │           │     ├── core/atom_classifier.py
  │           │     └── core/quality_validator.py
  │           ├── core/graph_engine.py
  │           └── core/persona_engine.py
  │
  ├── core/memory_injector.py
  │
  └── core/command_handler.py

storage/ → 全部依赖 SQLite + aiosqlite
```

---

## 扩展指南

### 接入新框架

```python
from memori import MemoryCore, LLMProvider, ContextProvider

class MyLLM(LLMProvider):
    async def chat(self, system, user) -> str: ...
    async def chat_with_judge(self, system, user) -> str: ...  # 可选：便宜模型

class MyCtx(ContextProvider):
    def get_user_id(self, event) -> str: ...
    def get_conversation_text(self, event) -> str: ...

core = MemoryCore(
    config={"bot_name": "Hana"},
    llm_provider=MyLLM(),
    context_provider=MyCtx(),
)
await core.initialize()
```

### 添加新提示词模板

1. 在 `memori/prompts/` 创建 `.txt` 文件
2. 在 `capturer.py` 的 `__init__` 加载列表中添加文件名
3. 在对应方法中使用 `self._prompts["name"]`

### 添加新检索器

1. 在 `memori/retrieval/` 创建新文件
2. 实现 `retrieve(keywords, user_ids, k) -> list[MemoryAtom]` 接口
3. 在 `dual_route_retriever.py` 中注册
