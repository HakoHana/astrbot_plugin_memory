# AstrBot Memory Plugin — 架构设计文档

> 基于 [TencentDB-Agent-Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory) 的分层记忆理念与 Hook 驱动架构，
> 结合 [astrbot_plugin_livingmemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory) 的原子生命周期管理，
> 重新设计的日记式记忆插件。

---

## 一、核心理念

### 双轨记忆模型

```
┌─────────────────────────────────────────────────────┐
│                  用户对话 (L0)                        │
└──────────────┬──────────────────────────────────────┘
               │ [Capturer] LLM 判断、过滤、提取
               ▼
┌─────────────────────────────────────────────────────┐
│  📔 日记 (人可读)                                    │
│  第一人称、带感情、按日归档的 .md 文件                  │
│  "今天Hako跟我告白啦！！"                             │
├─────────────────────────────────────────────────────┤
│  🧬 原子 (机器可搜)                                  │
│  结构化事实、SQLite、FTS5 全文索引                     │
│  {type: EPISODIC, content: "告白事件", importance: 0.95} │
├─────────────────────────────────────────────────────┤
│  👤 画像 (人格沉淀)                                   │
│  第一人称 Markdown，定期从日记+原子提炼                │
│  "我是她的 Bot，我喜欢她夸我聪明"                      │
└─────────────────────────────────────────────────────┘
```

**日记给情感，原子给检索，画像给气质。三者同步生成，互相关联。**

---

## 二、整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        MemoryPlugin (main.py)                     │
│  注册 Star，绑定事件代理                                           │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                       MemoryCore (门面)                           │
│  统一初始化、生命周期管理、模块装配                                  │
│  initialize() → 创建所有子模块                                   │
│  destroy() → 优雅关闭                                            │
│  on_message(event) → 消息处理入口                                 │
└──┬─────────┬─────────┬─────────┬──────────┬──────────┬──────────┘
   │         │         │         │          │          │
   ▼         ▼         ▼         ▼          ▼          ▼
┌──────┐ ┌─────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌──────────┐
│Adapt │ │Consolid │ │Capturer│ │Persona │ │Retriev │ │ 注入器   │
│(抽象)│ │Mgr(调度)│ │(抓取)  │ │(画像)  │ │(检索)  │ │Injector  │
│      │ │         │ │        │ │        │ │        │ │          │
│LLM   │ │暖启动    │ │Judege  │ │Generat │ │FTS     │ │位置选择  │
│Store │ │L1Runner │ │DiaryWr │ │        │ │[预留]  │ │模板+标签 │
│Ctx   │ │L3Runner │ │AtomExt │ │        │ │Hybrid  │ │          │
└──┬───┘ │状态持久  │ └────────┘ └────────┘ └────────┘ └──────────┘
   │     └─────────┘
   ▼
┌──────────────────────────────────────────────────────────────────┐
│                         Storage (存储层)                          │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐    │
│  │ AtomStore │  │ DiaryStore │  │PersonaStor│  │StateStore │    │
│  │ (SQLite)  │  │ (.md文件)  │  │ (.md文件) │  │ (SQLite)  │    │
│  └───────────┘  └───────────┘  └───────────┘  └───────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 三、模块职责

### 3.1 MemoryCore（门面）

统一管理所有模块的创建、初始化、销毁。对外提供简洁的 API。

```python
class MemoryCore:
    """核心门面"""
    async def initialize(self): ...
    async def destroy(self): ...
    async def on_message(self, event): ...  # 消息入口
```

### 3.2 Adapters（抽象层）

定义接口，隔离核心逻辑和 AstrBot 运行时。方便以后换 LLM / 换存储后端。

```python
class LLMProvider(ABC):
    """LLM 调用抽象。封装 AstrBot provider 调用。"""
    async def chat(self, system: str, user: str) -> str: ...

class MemoryStore(ABC):
    """存储抽象。AtomStore 实现此接口。"""
    async def search_fts(self, query: str, user_id: str, k: int) -> list[MemoryAtom]: ...
    # 预留向量搜索接口
    async def search_vector(self, query: str, user_id: str, k: int) -> list[MemoryAtom]: ...

class ContextProvider(ABC):
    """AstrBot 上下文抽象。提取事件信息、用户ID等。"""
    def get_user_id(self, event) -> str: ...
    def get_conversation_text(self, event) -> str: ...
```

### 3.3 ConsolidationManager（调度器 — 参考 TencentDB PipelineManager）

**核心调度器，管理 L1/L3 的触发逻辑。会话状态在这里维护。**

```python
class ConsolidationManager:
    """
    调度器 + 会话状态管理器
    
    借鉴 TencentDB PipelineManager 的设计：
    - L1Runner = Capturer（判断+写日记+提取原子）
    - L3Runner = PersonaEngine（画像更新）
    - 暖启动：新用户阈值从 1→2→4→8 指数增长
    - 空闲超时兜底：沉默一段时间后自动整理
    - 会话状态持久化：每次操作后写入数据库
    - 重试机制：LLM 失败后自动重试
    
    每个用户维护的状态 (PersistedSessionState)：
    {
        msg_count: 42,                    // 累计消息数
        warmup_threshold: 4,              // 暖启动当前阈值（0=已完成）
        last_consolidated_at: 1712345678, // 上次整理时间戳
        last_diary_date: "2026-06-05",    // 上次写日记日期
        diary_count: 3,                   // 日记总数
        diary_count_since_persona: 2,     // 上次画像更新后的日记数
        l1_retry_count: 0,               // L1 重试计数
    }
    """
    
    async def on_message(self, user_id: str):
        """每次消息调用：计数 + 判断是否需要触发"""
    
    async def check_and_consolidate(self, user_id: str, context):
        """检查触发条件：消息数阈值 / 时间阈值 / 空闲超时"""
    
    async def run_l1(self, user_id: str, context):
        """执行 L1：调用 Capturer 写日记+提取原子"""
    
    async def run_l3(self, user_id: str):
        """执行 L3：调用 PersonaEngine 更新画像"""
    
    async def save_state(self, user_id: str):
        """持久化会话状态到 StateStore"""
    
    async def load_state(self, user_id: str) -> PersistedSessionState:
        """从 StateStore 加载会话状态"""
```

### 3.4 Capturer（抓取能力 — L1Runner）

**核心业务模块：从对话中提取一切有价值的信息。**

```python
class Capturer:
    """
    抓取器 = Judge + DiaryWriter + AtomExtractor
    
    被 ConsolidationManager 调用，作为一个整体跑 L1 流水线。
    AtomExtractor 同时被 Capturer（写日记时）和 PersonaEngine（更新画像时）使用。
    """
    
    async def should_capture(self, session_context: str) -> CaptureJudgeResult:
        """LLM 轻量判断：这段对话值得记吗？"""
    
    async def capture(self, user_id: str, context: str) -> CaptureResult:
        """执行完整抓取：写日记 + 提取原子"""
    
    async def write_diary(self, user_id: str, judge_result: CaptureJudgeResult) -> str:
        """LLM 写日记，追加到当日 .md 文件"""
    
    async def extract_atoms(self, diary_content: str, user_id: str) -> list[MemoryAtom]:
        """LLM 从日记中提取原子。为 Capturer 和 PersonaEngine 共用。"""
```

### 3.5 PersonaEngine（画像引擎 — L3Runner）

```python
class PersonaEngine:
    """用户画像引擎"""
    
    async def get_persona(self, user_id: str) -> str | None:
        """读取用户画像 Markdown"""
    
    async def update_persona(self, user_id: str):
        """增量更新画像：旧画像 + 最新日记 + 最新原子 → 新画像"""
```

### 3.6 Retriever（检索引擎）

```python
class Retriever:
    """记忆检索引擎。MVP 只做 FTS5，以后扩展 Hybrid。"""
    
    async def recall(self, user_id: str, query: str, k: int = 5) -> list[MemoryAtom]:
        """搜索相关原子"""
    
    async def get_context_memories(self, user_id: str, query: str, k: int = 5) -> str:
        """生成供注入用的记忆文本"""
```

### 3.7 MemoryInjector（记忆注入器 — 你刚提的）

**控制记忆注入到提示词的什么位置、用什么格式、是否加标签。**

```python
class MemoryInjector:
    """
    记忆注入器
    
    用户可在 WebUI 中选择：
    - 系统提示词末尾（默认，最常用）
    - 用户消息之前
    - 用户消息之后
    - 知识库区域
    - 不注入，仅手动调用
    
    以及：
    - 自定义模板（可用 {{content}} {{user}}）
    - 是否用 <memory> 标签包裹
    """
    
    INJECTION_POSITIONS = {
        "system_prompt_suffix": "系统提示词末尾",
        "user_message_prefix":  "用户消息之前",
        "user_message_suffix":  "用户消息之后",
        "knowledge_section":    "知识库区域",
        "manual_only":          "不注入，仅手动调用",
    }
    
    def __init__(self, config: dict):
        self.position = config.get("injection_position", "system_prompt_suffix")
        self.template = config.get("injection_template", "")
        self.use_tag = config.get("injection_tag", True)
    
    def format_memory(self, memory_text: str, user_name: str) -> str:
        """格式化记忆内容：标签包裹 + 模板替换"""
    
    def inject(self, memory_text: str, persona_text: str, 
               system_prompt: str, user_message: str) -> tuple[str, str]:
        """
        注入记忆到指定位置
        返回 (modified_system_prompt, modified_user_message)
        """
    
    def build_memory_block(self, memories: list[MemoryAtom], 
                          persona: str | None) -> str:
        """根据配置的位置和模板，构建完整记忆文本块"""
```

### 3.8 CommandHandler（指令处理器）

```python
class CommandHandler:
    """处理用户指令"""
    
    async def diary(self, user_id: str, date: str | None): ...
    async def diary_list(self, user_id: str, year: str | None, month: str | None): ...
    async def memory(self, user_id: str): ...
    async def memory_search(self, user_id: str, query: str): ...
    async def memory_delete(self, user_id: str, atom_id: int): ...
    async def memory_stats(self, user_id: str): ...
```

### 3.9 Storage（存储层）

```python
class AtomStore(MemoryStore):
    """SQLite 原子存储。实现 MemoryStore 接口。"""
    async def insert(self, atom: MemoryAtom) -> int: ...
    async def insert_many(self, atoms: list[MemoryAtom]) -> list[int]: ...
    async def search_fts(self, query: str, user_id: str, k: int) -> list[MemoryAtom]: ...
    async def touch(self, atom_id: int): ...  # 更新访问时间
    async def delete(self, atom_id: int): ...
    async def get_by_user(self, user_id: str) -> list[MemoryAtom]: ...
    # 预留向量搜索
    async def search_vector(self, query: str, user_id: str, k: int) -> list[MemoryAtom]: ...


class DiaryStore:
    """Markdown 日记文件存储"""
    async def append(self, user_id: str, date: str, content: str): ...
    async def read(self, user_id: str, date: str) -> str | None: ...
    async def list_months(self, user_id: str) -> list[str]: ...
    async def list_dates(self, user_id: str, year: str, month: str) -> list[str]: ...


class PersonaStore:
    """画像文件存储"""
    async def read(self, user_id: str) -> str | None: ...
    async def write(self, user_id: str, content: str): ...


class StateStore:
    """会话状态持久化"""
    async def save(self, user_id: str, state: PersistedSessionState): ...
    async def load(self, user_id: str) -> PersistedSessionState | None: ...
    async def load_all(self) -> dict[str, PersistedSessionState]: ...  # 启动时恢复全量
```

---

## 四、数据模型

```python
class AtomType(str, Enum):
    EPISODIC = "episodic"       # 情景/事件
    FACTUAL = "factual"         # 事实
    PREFERENCE = "preference"   # 偏好
    PLANNED = "planned"         # 计划
    RELATIONAL = "relational"   # 关系
    UNKNOWN = "unknown"

class AtomStatus(str, Enum):
    ACTIVE = "active"
    DORMANT = "dormant"
    ARCHIVED = "archived"
    FORGOTTEN = "forgotten"

@dataclass
class MemoryAtom:
    atom_id: int = 0
    user_id: str
    diary_date: str                    # "2026-06-05"
    atom_type: AtomType = AtomType.UNKNOWN
    content: str = ""
    entities: list[str] = field(default_factory=list)
    importance: float = 0.5            # 0.0 ~ 1.0
    confidence: float = 0.7
    access_count: int = 0
    created_at: float = 0.0
    last_accessed_at: float | None = None
    ttl_days: float = 30.0
    status: AtomStatus = AtomStatus.ACTIVE
    session_id: str | None = None
    diary_ref: str | None = None       # 关联日记文件相对路径
    metadata: dict = field(default_factory=dict)

@dataclass
class CaptureJudgeResult:
    """LLM 判断结果"""
    should_remember: bool
    reason: str = ""
    importance: float = 0.0
    mood: str = ""
    context_summary: str = ""

@dataclass
class CaptureResult:
    """一次抓取的结果"""
    wrote_diary: bool
    diary_content: str = ""
    atoms: list[MemoryAtom] = field(default_factory=list)
    atom_count: int = 0

@dataclass
class PersistedSessionState:
    """持久化的会话状态，存入 StateStore"""
    user_id: str
    msg_count: int = 0
    warmup_threshold: int = 1          # 暖启动阈值（0=已完成）
    last_consolidated_at: float = 0.0  # 上次整理时间戳
    last_diary_date: str = ""          # 上次写日记日期
    diary_count: int = 0
    diary_count_since_persona: int = 0
    l1_retry_count: int = 0
```

---

## 五、数据库设计

### memory_atoms 表

```sql
CREATE TABLE memory_atoms (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT    NOT NULL,
    diary_date      TEXT    NOT NULL,               -- YYYY-MM-DD
    atom_type       TEXT    NOT NULL DEFAULT 'unknown',
    content         TEXT    NOT NULL,
    entities        TEXT    DEFAULT '[]',           -- JSON array
    importance      REAL    NOT NULL DEFAULT 0.5,
    confidence      REAL    NOT NULL DEFAULT 0.7,
    access_count    INTEGER NOT NULL DEFAULT 0,
    created_at      REAL    NOT NULL,
    last_accessed_at REAL,
    ttl_days        REAL    NOT NULL DEFAULT 30.0,
    status          TEXT    NOT NULL DEFAULT 'active',
    session_id      TEXT,
    diary_ref       TEXT,
    embedding       BLOB,                            -- 预留向量字段
    embedding_model TEXT,                            -- 预留
    metadata        TEXT    DEFAULT '{}'
);

CREATE INDEX idx_atoms_user        ON memory_atoms(user_id);
CREATE INDEX idx_atoms_type        ON memory_atoms(atom_type);
CREATE INDEX idx_atoms_status      ON memory_atoms(status);
CREATE INDEX idx_atoms_importance  ON memory_atoms(importance);
CREATE INDEX idx_atoms_diary_date  ON memory_atoms(diary_date);

CREATE VIRTUAL TABLE memory_atoms_fts USING fts5(
    content,
    atom_id UNINDEXED,
    user_id UNINDEXED,
    tokenize='unicode61'
);
```

### consolidation_state 表

```sql
CREATE TABLE consolidation_state (
    user_id                 TEXT PRIMARY KEY,
    msg_count               INTEGER DEFAULT 0,
    warmup_threshold        INTEGER DEFAULT 1,
    last_consolidated_at    REAL,
    last_diary_date         TEXT,
    diary_count             INTEGER DEFAULT 0,
    diary_count_since_persona INTEGER DEFAULT 0,
    l1_retry_count          INTEGER DEFAULT 0
);
```

---

## 六、文件结构

```
astrbot_plugin_memory/
├── main.py                          # 插件入口
├── metadata.yaml                    # 插件市场元数据
├── _conf_schema.json                # WebUI 配置 schema
├── requirements.txt                 # 依赖
├── logo.png                         # 256×256
│
├── models/
│   ├── __init__.py
│   └── memory_atom.py              # 数据模型
│
├── storage/
│   ├── __init__.py
│   ├── atom_store.py               # SQLite 原子存储（实现 MemoryStore）
│   ├── diary_store.py              # 日记文件存储
│   ├── persona_store.py            # 画像文件存储
│   └── state_store.py             # 会话状态持久化
│
├── core/
│   ├── __init__.py
│   ├── memory_core.py              # 门面
│   ├── adapters.py                 # 抽象层
│   ├── consolidation_manager.py    # 调度器 + 会话状态
│   ├── capturer.py                 # 抓取器 (Judge + DiaryWriter + AtomExtractor)
│   ├── persona_engine.py           # 画像引擎
│   ├── retriever.py                # 检索引擎
│   ├── memory_injector.py          # 记忆注入器（位置选择+模板+标签）
│   └── command_handler.py          # 指令处理器
│
├── prompts/
│   ├── judge.txt                   # 判断是否值得记
│   ├── diary.txt                   # 写日记
│   ├── atoms.txt                   # 提取原子
│   └── persona.txt                 # 更新画像
│
└── webui/
    └── settings/
        ├── index.html
        └── app.js
```

---

## 七、三大核心流程

### 流程一：消息处理（on_message 入口）

```
用户发消息
    │
    ▼
① MemoryCore.on_message(event)
    │
    ├──▶ [Retriever.recall()]
    │      搜索相关原子 → MemoryInjector 组装并注入
    │
    ├──▶ [ConsolidationManager.on_message()]
    │      计数 + 检查触发条件
    │      ├─ msg_count ≥ warmup_threshold → 触发 L1
    │      ├─ 空闲超时 (IdleTimeout) → 触发 L1
    │      └─ 即时捕捉（重要事件） → 立即触发 L1
    │
    └──▶ [CommandHandler] 如果是指令则处理
```

### 流程二：L1 整理流水线（Capturer）

```
[ConsolidationManager 触发 L1]
    │
    ▼
① Capturer.should_capture() — LLM 轻量判断
    ├─ [{should_remember: false}] → 跳过，更新状态
    └─ [{should_remember: true}] ↓
    │
    ▼
② Capturer.write_diary()
    └─ LLM 写日记 → DiaryStore.append() 追加到当日 .md
    │
    ▼
③ Capturer.extract_atoms()
    └─ LLM 提取原子 → AtomStore.insert_many() 批量写入
    │
    ▼
④ ConsolidationManager 检查 L3
    └─ diary_count_since_persona ≥ 阈值？
       ├─ 是 → 触发 L3 (PersonaEngine.update_persona())
       └─ 否 → 跳过
    │
    ▼
⑤ ConsolidationManager 更新状态
    └─ msg_count = 0, diary_count++, 暖启动阈值 ×2, 持久化
```

### 流程三：记忆注入（Retriever → MemoryInjector）

```
[用户发消息]
    │
    ▼
① Retriever.recall()
    └─ FTS5 搜索 → 按重要性排序 → 取 top K
    │
    ▼
② PersonaEngine.get_persona()
    └─ 读取当前用户画像
    │
    ▼
③ MemoryInjector.build_memory_block()
    └─ 组装记忆文本块
    │
    ▼
④ MemoryInjector.inject()
    ├─ 按用户配置的位置
    ├─ 模板替换（{{content}} {{user}}）
    └─ 标签包裹开关
    │
    ▼
⑤ 注入后的 system_prompt / user_message 发给 LLM
```

---

## 八、WebUI 配置项

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `llm_provider` | string | `""` | 记忆用 LLM（空=使用 AstrBot 默认） |
| `trigger_msg_count` | int | `10` | 每 N 轮消息触发整理 |
| `trigger_time_minutes` | int | `360` | 每 N 分钟触发（0=禁用） |
| `immediate_capture` | bool | `true` | 即时捕捉重要事件 |
| `warmup_enabled` | bool | `true` | 暖启动（阈值指数增长） |
| `idle_timeout_minutes` | int | `30` | 空闲超时兜底 |
| `max_diary_tokens` | int | `500` | 日记最大 Token |
| `recall_count` | int | `5` | 每次召回条数 |
| `recall_max_tokens` | int | `500` | 注入文本最大 Token |
| `persona_update_interval` | int | `10` | 每 N 篇日记更新画像 |
| **`injection_position`** | string | `system_prompt_suffix` | 记忆注入位置 |
| **`injection_template`** | string | `""` | 自定义注入模板 |
| **`injection_use_tag`** | bool | `true` | 是否用标签包裹 |

---

## 九、指令

| 指令 | 功能 |
|------|------|
| `/日记 [日期]` | 查看日记，默认今天 |
| `/日记 列表 [年月]` | 列出日记日期 |
| `/记忆` | 查看画像/记忆摘要 |
| `/记忆 搜索 <关键词>` | 搜索相关记忆 |
| `/记忆 删除 <id>` | 删除原子 |
| `/记忆 统计` | 统计概览 |

---

## 十、未来预留

| 功能 | 预留位置 |
|------|---------|
| 向量搜索 | `AtomStore.embedding` 字段，`Retriever.hybrid_search()` 接口 |
| 热温冷分层 + 衰减 | `AtomStatus` 流转，`ConsolidationManager` 扩展 L2 |
| 短期任务压缩 | 独立模块，通过 `Capturer` 接口协作 |
| 导出/导入 | `DiaryStore.export_all()`，`AtomStore.import_atoms()` |
| Agent Memory Tools | 注册 `recall_long_term_memory` / `memorize_long_term_memory` |
