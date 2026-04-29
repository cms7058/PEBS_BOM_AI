# 四层数据架构与 Core API

> 日期：2026-04-28
> 关联：[03-产品架构与下一步.md](03-产品架构与下一步.md)、[04-SKU智能映射设计.md](04-SKU智能映射设计.md)、[06-Skill规范.md](06-Skill规范.md)
> 定位：03 文档讲"做什么、什么时候做"（产品节奏）；本文讲"代码层面怎么分层"（技术骨架）。两者互补，不冲突。

## 一、为什么要再画一层数据架构

03 文档定义了**产品分层**（引擎 / 核心钩子 / 自有 skill / 第三方 skill），那是**业务视角**。
要让 skill 化真正落地，还需要一层**数据视角**的骨架，回答三个问题：

1. skill 从哪里读数据、写到哪里？
2. 客户的数据五花八门，怎么进系统？
3. 标准件这种公共知识谁来沉淀、放在哪？

如果不先把这层定下来，每写一个 skill 就要重新接一遍数据，三个 skill 之后就乱了。

## 二、四层数据架构图

```
┌──────────────────────────────────────────────────────────────┐
│  L4：Skill 插件层（详见 06-Skill规范.md）                      │
│  ┌──────────┬──────────┬──────────┬──────────┬──────────┐  │
│  │ SKU 映射 │ 风险预警 │ 历史沉淀 │ 比价询价 │ 报告导出 │  │
│  │   skill  │  skill   │  skill   │   skill  │   skill  │  │
│  └─────┬────┴─────┬────┴─────┬────┴─────┬────┴─────┬────┘  │
│        └──────────┴──────────┴──────────┴──────────┘        │
│                          │                                   │
│              ┌───────────▼────────────┐                      │
│              │  Skill Bus（运行时）   │                      │
│              │  · 注册 / 发现 / 鉴权 │                      │
│              │  · 触发 / 编排 / 计费 │                      │
│              └───────────┬────────────┘                      │
└──────────────────────────┼───────────────────────────────────┘
                           │ Core API（唯一入口，读写 L2）
┌──────────────────────────┼───────────────────────────────────┐
│                          ▼                                    │
│                ┌──────────────────┐                           │
│  L3 数据接入   │  L2 统一 BOM     │   L1 国标标准件库         │
│  ──────────→   │     模型         │   ←──────────             │
│                │ (Canonical Model)│                           │
│                └──────────────────┘                           │
│                                                               │
│  L3：客户数据进系统的适配器        L1：跨客户共享的公共知识   │
│  L2：你的产品的数据契约 = 客户私有 BOM 数据                   │
└──────────────────────────────────────────────────────────────┘
```

## 三、L1：国标标准件知识库

### 定位
**跨客户共享、公司一次建设、长期资产**。所有客户都能复用，不属于任何一个客户。

### 范围（按优先级）

| 优先级 | 类目 | 国标号举例 | 覆盖率 |
|---|---|---|---|
| P0 | 螺栓 / 螺母 / 垫圈 / 销 | GB/T 5782、GB/T 6170、GB/T 97 | 占非标行业 BOM 的 30-40% |
| P0 | 轴承（深沟球 / 圆锥滚 / 推力）| GB/T 276、GB/T 297 | 占 5-10% |
| P1 | 键 / 销 / 挡圈 | GB/T 1096、GB/T 119、GB/T 894 | 占 5% |
| P1 | 直线导轨 / 滚珠丝杠（型号化） | 厂家通用型号体系 | 占 10-15% |
| P2 | 密封件 / 弹簧 / 联轴器 | 各类 GB/HG | 占 5% |

P0 + P1 加起来已经覆盖 BOM 中超过 50% 的标准件，这是 1-2 周可以做出种子库的量。

### 数据 schema 草案

```python
class StandardPart:
    std_id: str          # 主键，如 "GB/T 5782-M8x30-8.8"
    standard: str        # "GB/T 5782" / "ISO 4762" / "DIN 912"
    category: str        # "fastener.bolt.hex" 三段式分类
    name_zh: str         # "六角头螺栓"
    name_en: str         # "Hex Head Bolt"
    spec: dict           # {"M": 8, "L": 30, "thread": "M8x1.25"}
    material_grade: str  # "8.8" / "A2-70" / "304"
    surface: str         # "本色" / "镀锌" / "发黑"
    aliases: list[str]   # 工程师常用的非标准叫法（用于映射）
    cross_refs: dict     # 跨标准映射 {"ISO": "ISO 4014", "DIN": "DIN 931"}
```

### 与 SKU 映射的关系

L1 是 04 文档里 SKU 智能映射的**底库**。映射就是把客户写的"M8x30 内六角"对到 `GB/T 70.1-M8x30-8.8` 这个 std_id 上。

## 四、L2：统一 BOM 模型（Canonical Model）

### 定位
**你的产品的数据契约**。对内是所有 skill 唯一可见的数据形态，对外是 import/export 的标准格式。客户的数据进系统后，必须落到这个 schema 里。

### 核心实体

```python
class BomItem:
    item_id: str           # 客户内部唯一 ID（不是 std_id）
    project_id: str
    parent_id: str | None  # 装配树父节点
    level: int             # 层级深度
    seq: int               # 同级序号

    # 命名 / 描述
    name: str              # 工程师写的名字（原始）
    spec: str              # 工程师写的规格（原始）

    # 标准化结果（由 SKU 映射 skill 填）
    std_ref: str | None    # 指向 L1.std_id；非标件为 null
    canonical_name: str | None
    canonical_spec: dict | None

    # 物料属性
    qty: float
    unit: str              # "件" / "kg" / "m"
    material: str | None
    surface: str | None
    is_custom: bool        # 是否非标件

    # 业务字段（被各 skill 写）
    supplier_refs: list[SupplierRef]   # 比价 skill 写
    history_refs: list[HistoryRef]     # 历史沉淀 skill 写
    risk_flags: list[RiskFlag]         # 风险预警 skill 写

    # 审计
    source: str            # "step_parser" / "excel_import" / "agent_edit"
    created_at, updated_at
```

### 关键设计原则

1. **原始字段保留**：`name` / `spec` 永远保留工程师原话，不被覆盖。标准化结果写到 `canonical_*` 字段。这样 SKU 映射 skill 可以反复重跑、可被人工修正。
2. **业务字段开放**：`supplier_refs` / `history_refs` / `risk_flags` 是 list，不同 skill 可以追加自己的记录而不互相覆盖。
3. **版本化**：`updated_at` + 编辑历史（已有 `audit.py`）保证可追溯。

## 五、L3：数据接入适配层

### 定位
**让客户五花八门的数据能进 L2**。每个客户、每种数据源一个适配器。

### 适配器类型

| 类型 | 实现方式 | 工作量 |
|---|---|---|
| Excel / CSV | LLM 自动识别列含义 → 生成映射 → 人工确认 | 单客户 < 30 分钟 |
| SolidWorks / PDM | 现成插件（SW 自带 BOM 导出 API） | 一次性开发 |
| Teamcenter / Pro/E | 标准 BOM 导出 + 解析 | 一次性开发 |
| ERP（SAP / 用友 / 金蝶 / Oracle） | 厂商标准 API | 各 1-2 周 |
| 客户自建数据库 | LLM 看 5 行样本 → 生成映射 SQL | 单客户 1-2 小时 |
| 纸质 / PDF 报价单 | OCR + LLM 抽取 | 一次性开发 |

### LLM 辅助的"半自动 schema 映射"流程

```
1. 用户上传 Excel/CSV 样本（前 20 行）
2. 系统调 LLM：「这是一份 BOM，把每一列对应到 L2 schema 的哪个字段？」
3. LLM 返回映射 JSON：
   {
     "A": "seq", "B": "level", "C": "name",
     "D": "spec", "E": "qty", "F": "unit", ...
   }
4. UI 展示映射，用户拖拽修改
5. 确认后保存为该客户该模板的"导入规则"，下次同格式直接复用
```

这一步是 03 文档里说的"客户数据五花八门"的真正解法——**LLM 让一次性 schema 映射的边际成本降到可忽略**。

## 六、Core API：唯一入口

所有 L4 skill **不允许直接读 L1/L2/L3 的数据库**，必须通过 Core API。这是为了：
- skill 不依赖数据库实现，未来切 PG/Mongo 都不用改 skill
- 鉴权、审计、计费在 API 层统一拦截
- skill 可被沙箱化运行（甚至跨进程）

### 接口分组（草案）

```python
# === BOM 数据访问 ===
core.bom.get(project_id) -> Bom
core.bom.list_items(project_id, filter) -> list[BomItem]
core.bom.get_item(item_id) -> BomItem
core.bom.update_item(item_id, patch) -> BomItem    # 受权限控制
core.bom.append_field(item_id, field, value)       # 业务字段追加（如 supplier_refs）

# === 标准件库访问 ===
core.std.search(query) -> list[StandardPart]
core.std.get(std_id) -> StandardPart
core.std.match(name, spec) -> list[(StandardPart, confidence)]  # 模糊匹配

# === 历史数据访问 ===
core.history.search_by_canonical(std_ref | canonical_spec) -> list[HistoryRecord]

# === 事件 / Hook 系统（skill 间联动）===
core.events.publish(event_type, payload)
core.events.subscribe(event_type, handler)        # skill 注册时声明

# === LLM 网关（统一计费 / 限流）===
core.llm.call(prompt, model_pref) -> LLMResponse
```

### 权限模型

每个 skill 的 manifest 声明它需要的能力（详见 06）。Core API 拿到调用时：
1. 检查 skill 是否被该客户启用
2. 检查 skill 是否声明了所需 capability
3. 检查当前用户角色是否允许（写 ERP 需管理员）
4. 通过则执行，并记录调用日志

## 七、与现有 PEBS_BOM 代码的对接路径

03 文档里"Skill 架构改造（4-6 周后再考虑）"对应的就是这条路：

| 现有代码 | 改造方向 | 优先级 |
|---|---|---|
| `excel_parser.py` / `step_parser.py` | 收敛到 L3 适配器接口 | 中 |
| `audit.py` | 接入 Core API 统一审计 | 中 |
| `agent_tools.py` | 拆成 N 个内置 skill（BOM 编辑 / SKU 映射 / 历史查询）| **高** |
| `agent.py` | LLM 网关 → 走 `core.llm.call` | 中 |
| 数据库层 | 抽出 Repository，仅 Core API 可访问 | **高** |

**改造顺序建议**：

1. **先定 L2 schema 并落到代码**（不动现有逻辑，只新增 dataclass / pydantic 模型）
2. **再加 Core API 薄壳**（先包一层，内部还是直连数据库）
3. **agent_tools.py 拆 skill**（这是最有价值的一步）
4. 之后随业务自然引入 L1/L3 完整能力

不要试图一次性把 L1/L2/L3/L4 全建起来，**自顶向下骨架 + 自底向上随业务填肉**才是务实路径。

## 八、风险与开放问题

| 问题 | 现状 | 应对 |
|---|---|---|
| 国标库谁来录入？ | 没有公开 API | 第一批 P0 标准件人工 + LLM 辅助录入，约 500-1000 条 |
| 客户数据隔离 | SQLite 单库 | L2 表加 `tenant_id`，未来切 PG 时按 schema 隔 |
| Core API 性能 | 暂无瓶颈 | 先功能正确，3-5 客户后再优化 |
| skill 的 LLM 成本归属 | 未设计 | Core LLM 网关按 skill_id 记账，便于后续按 skill 计费 |

---

下一步：见 [06-Skill规范.md](06-Skill规范.md)，定义 skill 的具体形态。
