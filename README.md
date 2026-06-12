# memori — 长期记忆内核

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-225%20passing-brightgreen.svg)](tests/)

**memori** 是一个纯净的 Python 长期记忆内核，通过两个抽象接口（`LLMProvider` + `ContextProvider`）接入各种 Agent 框架。

## 特性

- **📝 日记式记忆** — LLM 以第一人称写日记，记录对话中的重要时刻
- **🔍 原子事实** — 结构化事实提取（episodic / factual / preference / planned / relational），FTS5 全文检索
- **📖 日记溯源** — 原子→日记回溯，注入时附带相关原文段落
- **🕸️ 实体关系图** — `nodes` + `edges` 简化的实体关系图，不做检索，只做关联发现
- **🧠 用户画像** — 标签 + 一句话摘要（SQLite），节省 token
- **🔀 双路检索 + RRF 融合** — BM25 文档路 + 图路实体补充
- **🎯 jieba 中文分词** — 精确词级匹配 + 字符二元组降级
- **⚡ 异步后台处理** — 不阻塞主流程
- **💡 合并 LLM 调用** — 一次调用同时输出日记 + 原子事实，减少 50% 昂贵模型调用
- **🧩 规则基原子分类** — 正则匹配原子类型，无需额外 LLM 调用
- **🔍 三层去重** — 提前去重（FTS+Jaccard）/ 写入去重 / 语义去重
- **🔄 记忆生命周期** — 去重 → 强化 → 衰减 → 遗忘 → 归档 → 清理，统一 LifecycleManager
- **🛡️ 质量校验** — 自动检测泛化词、空摘要，日志告警不拒写
- **🔧 JSON 自动修复** — 未闭合引号/括号/尾部逗号自动修复
- **🏗️ 接口驱动设计** — 遵循 DIP / SRP / OCP / ISP / LoD 五大原则

## 快速开始

### 安装

```bash
pip install memori
# 或 HTTP 服务版
pip install "memori[server]"
```

### 接入任意框架

```python
from memori import MemoryCore
from memori.core.adapters import LLMProvider, ContextProvider

class MyLLM(LLMProvider):
    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        # 调用你的 LLM
        return ...

class MyCtx(ContextProvider):
    def get_user_id(self, event) -> str:
        return event.user_id
    def get_conversation_text(self, event) -> str:
        return event.text

core = MemoryCore(
    llm_provider=MyLLM(),
    context_provider=MyCtx(),
    data_dir="./data",
)
await core.initialize()
await core.process_message(user_id="user1", message_text="今天测试辛苦了")
```

### 独立 HTTP 服务

```bash
python -m memori --port 8765
```

```http
POST /api/v1/events
{
    "user_id": "123",
    "text": "今天测试辛苦了",
    "sender_name": "Hako"
}
```

API 文档：`http://localhost:8765/docs`

## 目录结构

```
memori/
├── memori/
│   ├── core/                  # 门面 + 接口定义
│   │   ├── adapters.py             # LLMProvider / ContextProvider 抽象
│   │   ├── interfaces.py           # 9 个 ABC 接口
│   │   ├── memory_core.py          # 统一门面
│   │   ├── retriever.py            # 检索引擎（双路 RRF）
│   │   ├── memory_injector.py      # 记忆注入器
│   │   └── hot_cache.py            # 热消息缓存
│   │
│   ├── pipeline/              # 处理流水线
│   │   ├── capturer.py             # 抓取器：Judge→去重→Capture
│   │   ├── capture_step.py         # 策略链基类 + 4 个内置步骤
│   │   ├── memory_uow.py           # 存储门面
│   │   ├── atom_classifier.py      # 规则基原子分类
│   │   ├── quality_validator.py    # 输出质量校验
│   │   ├── consolidation_manager.py# 调度器
│   │   └── warm_processor.py       # 异步队列消费者
│   │
│   ├── lifecycle/             # 记忆生命周期管理
│   │   ├── manager.py              # LifecycleManager 统一入口
│   │   ├── dedup.py                # jieba Jaccard + 语义去重
│   │   ├── decay.py                # 重要性衰减
│   │   ├── cleanup.py              # 孤立原子清理
│   │   └── archiver.py             # 冷存储归档
│   │
│   ├── features/              # 领域特性
│   │   ├── graph_engine.py         # 实体关系图引擎（简化版）
│   │   ├── persona_engine.py       # 用户画像引擎
│   │   └── command_handler.py      # 指令处理
│   │
│   ├── retrieval/             # 检索系统
│   │   ├── dual_route_retriever.py # 双路四模式编排
│   │   ├── bm25_retriever.py       # FTS5 + LIKE 文档路
│   │   ├── graph_keyword_retriever.py # 图路关键词
│   │   ├── graph_vector_retriever.py  # 图路向量
│   │   ├── vector_retriever.py     # 文档向量路
│   │   ├── graph_utils.py          # 图路共享工具
│   │   └── rrf_fusion.py           # RRF 融合
│   │
│   ├── storage/               # 存储层（每类数据库独立文件）
│   │   ├── atom_store.py           # FTS5 + 原子 CRUD
│   │   ├── diary_store.py          # 日记存储 + FTS
│   │   ├── graph_store.py          # 图谱 nodes/edges
│   │   ├── persona_store.py        # 旧版画像（已弃用）
│   │   ├── conversation_store.py   # 对话存储
│   │   ├── state_store.py          # 状态存储
│   │   ├── base_store.py           # 连接池 + 锁
│   │   ├── db_migration.py         # 数据库迁移
│   │   └── write_op_log.py         # 写操作日志
│   │
│   ├── models/                # 数据模型
│   │   ├── memory_atom.py          # MemoryAtom / AtomType
│   │   └── graph_models.py         # GraphNode
│   │
│   ├── api/                   # HTTP 服务（可选）
│   ├── utils/                 # 工具函数
│   └── prompts/               # LLM 提示词模板
│
├── tests/                     # 225 个测试
│
├── adapters/                  # 框架适配器
│   └── astrbot/               # AstrBot 平台适配
│
├── docs/                      # 文档
├── webui/                     # Web 仪表盘
├── main.py                    # 服务入口
└── pyproject.toml
```

## 数据库

每个数据域独立 SQLite 文件，互不影响：

| 数据库 | 文件 | 内容 |
|--------|------|------|
| memory.db | 记忆 | memory_atoms, atomic_facts, user_persona |
| diaries.db | 日记 | diary_entries, diary_fts |
| graph.db | 图谱 | nodes, edges |
| conversations.db | 对话 | sessions, messages |
| state.db | 状态 | consolidation_state |

## 架构设计原则

| 原则 | 实现方式 |
|------|----------|
| **DIP** 依赖倒置 | `core/interfaces.py` 定义 9 个 ABC，各模块依赖接口而非具体类 |
| **SRP** 单一职责 | core / pipeline / lifecycle / features 四包分离 |
| **OCP** 开闭原则 | `CaptureStep` 策略链，新增步骤不修改已有代码 |
| **ISP** 接口隔离 | `MemoryCoreOptions` 配置对象，调用方只传关心的字段 |
| **LoD** 最少知识 | `MemoryUnitOfWork` 门面，Capturer 不直接操作 3 个 Store |

## 核心数据流

```
消息累积 → ConsolidationManager (调度)
  │ msg_count >= 阈值
  ▼
WarmProcessor (后台异步)
  │
  ├── 1. Judge（便宜 LLM）→ 值不值得记？
  │
  ├── 2. 提前去重（FTS + Jaccard ≥ 0.85）
  │       └── 命中 → 强化旧记忆，跳过昂贵模型
  │
  ├── 3. Capture（昂贵模型，合并调用）
  │     ├── 策略链：QualityCheck → AtomClassify → DiaryFill → Truncate
  │     └── 输出 diary + atoms
  │
  ├── 4. 原子落库 + lifecycle 去重强化
  │
  ├── 5. 图谱索引（index_diary，异步 fire-and-forget）
  │
  └── 6. 画像更新（每 N 次日记）
```

## 检索路径

```
用户消息 → Retriever.recall()
  │
  ├── BM25 文档路（FTS5 + LIKE → memory_atoms）
  │     └── 主检索引擎，关键词全文匹配
  │
  ├── Graph 图路（节点名匹配 → mention edges → diary → atoms）
  │     └── 实体链接补充：当 query 包含已知实体时追加关联结果
  │
  └── RRF 融合 → 排序 → MemoryInjector → system_prompt
```

图谱**不是**平行检索引擎，而是做 BM25 搜不到的实体关联补充。
要回答"用户和谁一起喝过咖啡"才需要图；回答"用户喜欢什么咖啡豆"时 BM25 就够了。

## 记忆注入（Hybrid Injection）

```
召回 top-5 原子
  │
  ├── 画像部分: 标签 + 一句话摘要（~50 tokens）
  │
  ├── 原子列表: 5 条事实，每条带类型和日期标签
  │
  ├── 日记溯源: 最高相关原子 → 回溯关联日记 → 最佳段落（可配置 N）
  │
  └── 搜索提示: 引导 Agent 使用 search_memories / read_diary 工具
```

## 依赖

| 包 | 用途 |
|-----|------|
| `aiosqlite` | 异步 SQLite 驱动 |
| `cachetools` | TTL 缓存 |
| `jieba` | 中文分词 |
| `httpx` | HTTP 客户端 |
| `fastapi` + `uvicorn` | HTTP 服务（可选） |

## 更多文档

- [完整接口文档](INTERFACES.md) — 所有 ABC、数据模型、接入模板
- [架构设计](docs/ARCHITECTURE.md) — 分层、流程、设计决策
- [配置说明](docs/configuration.md) — 全部配置项

## License

MIT
