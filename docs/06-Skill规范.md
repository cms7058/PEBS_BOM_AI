# Skill 规范（SKILL Specification）

> 日期：2026-04-28
> 关联：[03-产品架构与下一步.md](03-产品架构与下一步.md)、[05-四层数据架构与CoreAPI.md](05-四层数据架构与CoreAPI.md)
> 定位：定义 PEBS_BOM 中 skill 的目录结构、manifest 字段、生命周期、调用契约。

## 一、Skill 是什么

**一个 skill = 一个独立目录 + 一份声明文件 + 业务代码**。

- 自包含：自己带提示词、配置、测试
- 通过 Core API 读写数据，不直接操作数据库
- 通过 manifest 向系统声明自己的能力、依赖、触发方式
- 可以被启用 / 禁用 / 升级 / 卸载，不影响其他 skill

> 设计参考了 Claude Code 的 SKILL.md 模型 —— 让 LLM 能基于 description 自主判断"该不该调用我"，这是 AI Agent 时代的标准做法。

## 二、目录结构

```
skills/
└── sku-mapping/                      ← skill 的目录名 = skill_id
    ├── SKILL.md                      ← 人类 + LLM 可读的能力描述（必需）
    ├── manifest.json                 ← 机器可读的元数据（必需）
    ├── handlers/                     ← 业务代码
    │   ├── __init__.py
    │   ├── on_bom_imported.py       ← 事件钩子
    │   └── map.py                   ← 主逻辑
    ├── prompts/                      ← skill 自带的 LLM 提示词
    │   └── matcher.txt
    ├── config/
    │   └── default.yaml             ← 默认配置（客户可覆盖）
    ├── tests/
    │   └── test_map.py
    └── README.md                     ← 给开发者看的实现说明
```

约定：
- `SKILL.md` 给 **LLM 决策**用 —— 内容是"我能做什么、什么时候该调用我"
- `README.md` 给 **开发者**用 —— 内容是"我的代码怎么组织、依赖什么"
- 两者职责不同，不要合并

## 三、SKILL.md 字段规范

```markdown
---
name: sku-mapping
version: 0.1.0
description: 把工程师乱写的零件名映射到国标 SKU。出现在 BOM 入库后或用户主动要求"统一命名"时调用。
type: built-in                       # built-in | first-party | third-party
trigger:
  - on_event: bom.imported            # 事件触发
  - on_command: "/normalize"          # 用户命令
  - by_agent: true                    # 模型自主决策（基于 description）
inputs:
  - bom: Bom                          # 引用 L2 schema
outputs:
  - mapped_count: int
  - unmapped_items: list[BomItem]
requires_capability:
  - core.bom.read
  - core.bom.update_item
  - core.std.search
  - core.std.match
  - core.llm.call
requires_external:
  - none                              # 无外网调用
pricing:
  model: included                     # included | per-call | subscription
  per_call_cost: 0
ui:
  panel: "right-sidebar"              # 是否在前端有面板
  command_palette: "标准化命名"
---

## 我能做什么

把 BOM 里 `name` / `spec` 杂乱的写法对到 `std_ref`，输出标准化的 `canonical_name` / `canonical_spec`。
对未识别的件返回"不确定"列表交给人工。

## 什么时候该调用我

- 用户刚导入 / 刚生成一份 BOM（最佳时机）
- 用户主动说"统一命名"、"标准化"、"识别国标"
- 在做比价 / 历史查询前，没有 std_ref 的件需要先经过我

## 什么时候不该调用我

- BOM 已全部映射过且未发生编辑
- 客户明确禁用了 SKU 映射功能
```

字段说明：

| 字段 | 必填 | 含义 |
|---|---|---|
| `name` | ✅ | skill 唯一标识，与目录名一致 |
| `version` | ✅ | semver |
| `description` | ✅ | LLM 判断是否调用的关键依据 —— 写得**具体、有动作、有触发条件** |
| `type` | ✅ | `built-in`（系统自带）/ `first-party`（你做的）/ `third-party`（第三方） |
| `trigger` | ✅ | 至少有一种触发方式 |
| `inputs` / `outputs` | ✅ | 严格的 schema，便于 skill 间编排 |
| `requires_capability` | ✅ | 用到的 Core API，运行时校验 |
| `requires_external` | ✅ | 外部依赖（外网、写文件、调外部服务）—— 客户配置时显式授权 |
| `pricing` | ✅ | 计费模型（为后续生态商业化留口子） |
| `ui` | ❌ | 是否在前端展示入口 |

## 四、manifest.json（机器可读版）

供 Skill Bus 启动时读取：

```json
{
  "name": "sku-mapping",
  "version": "0.1.0",
  "type": "built-in",
  "entry": "handlers/map.py:run",
  "event_handlers": {
    "bom.imported": "handlers/on_bom_imported.py:handle"
  },
  "command_handlers": {
    "/normalize": "handlers/map.py:run"
  },
  "schema": {
    "inputs": { "$ref": "schemas/inputs.json" },
    "outputs": { "$ref": "schemas/outputs.json" }
  },
  "capabilities": [
    "core.bom.read",
    "core.bom.update_item",
    "core.std.search",
    "core.std.match",
    "core.llm.call"
  ],
  "external": [],
  "min_core_version": "0.1.0"
}
```

`SKILL.md` 是人写人看的，`manifest.json` 是程序读的。两者由构建工具校验一致。

## 五、Skill Bus 必须解决的事

| 问题 | 设计 |
|---|---|
| **发现机制** | 启动时扫描 `skills/` 目录 → 读 manifest → 注册到 Bus |
| **触发分发** | 事件触发：Bus 维护订阅表；命令触发：UI 暴露命令面板；Agent 触发：把所有 skill 的 description 喂给 LLM 让它决策 |
| **权限校验** | 每次 Core API 调用前检查 `requires_capability` 是否声明、客户是否启用 |
| **数据隔离** | skill 拿到的 BomItem 是带权限的视图（如比价 skill 不能看其他客户的数据） |
| **编排能力** | skill A 的 outputs 类型匹配 skill B 的 inputs → 可链式调用 |
| **版本管理** | skill 升级时，新版本可以"灰度"启用，老版本可回滚 |
| **观测与计费** | 每次调用记录 `(skill_id, customer_id, duration, llm_tokens, cost)` |

## 六、内置 skill 第一批（与 03 文档的 Layer 1 对齐）

按落地顺序：

| skill | 价值 | 实现复杂度 | 落地节点 |
|---|---|---|---|
| `bom-edit` | BOM 增删改查（拆自 `agent_tools.py`） | 低 | **第一批，必做** |
| `sku-mapping` | 见 [04-SKU智能映射设计.md](04-SKU智能映射设计.md) | 中 | **第一批** |
| `history-deposit` | 项目完成 → 沉淀到客户知识库 | 中 | **第一批** |
| `risk-warning` | 单源件 / 非标件 / 长货期 / 无历史件预警 | 低 | 第二批 |
| `report-export` | BOM → Excel / PDF / 采购单格式 | 低 | 第二批（最实用、最简单的对外展示） |
| `eco-impact` | 零件改动影响下游分析 | 中 | 第三批 |

## 七、外部 skill / 第三方 skill 何时考虑

03 文档已经给出原则：**ARR 1000 万之前不开放第三方**。但内部"first-party 但相对独立"的 skill 可以更早试，例如：

| skill | 性质 | 开放优先级 |
|---|---|---|
| `price-compare`（立创 / 京东工业） | 依赖外部 API，独立性强 | 立创审核通过后立即做 |
| `pdf-quote-parse` | 完全独立，无外部依赖 | 比价 skill 跑通前的过渡方案 |
| `solidworks-bridge` | 客户端集成，独立部署 | 30 客户后 |

## 八、一个最小示例：`report-export` skill

最简单、最快能跑通整条 skill 链路的 skill，建议第一个落地。

```
skills/report-export/
├── SKILL.md
├── manifest.json
├── handlers/
│   └── export.py            # 主逻辑：读 L2 BOM → 生成 Excel/PDF
├── prompts/
│   └── format_hint.txt      # 给 LLM：用户描述 → 选择导出格式
└── tests/
    └── test_export.py
```

```python
# handlers/export.py
def run(ctx, args):
    bom = ctx.core.bom.get(args.project_id)
    fmt = args.get("format") or "excel"
    if fmt == "excel":
        return export_excel(bom)
    elif fmt == "pdf":
        return export_pdf(bom)
    elif fmt == "purchase":
        return export_purchase_order(bom)
```

价值：
- 客户立刻能看到"我的 BOM 能一键变成采购单 Excel"
- 验证 Skill Bus 的事件 / 命令 / Agent 三种触发都能跑
- 不依赖任何外部 API、不依赖 L1 库
- 失败成本极低

## 九、约束与红线

1. **skill 不得直连数据库** —— 一律走 Core API。违反者构建期检查报错。
2. **skill 不得读其他 skill 的私有目录** —— 通过 Core API 的事件 / outputs 传递数据。
3. **skill 必须声明所有外部依赖** —— 包括外网域名、本地文件路径。客户在启用时一次性授权。
4. **skill 必须可被禁用** —— 单个 skill 异常不能让整个系统挂掉。Bus 要做超时与熔断。
5. **skill 接口在 v1.0 前不冻结** —— 03 文档说过的话再强调一遍：前 3 个 skill 落地之前，Core API + manifest 必然要改，留一个"experimental"标记。

## 十、下一步动作清单

- [ ] 在仓库新建 `skills/` 目录，加 `.gitkeep`
- [ ] 写 `packages/skill-runtime/` 包：实现最小版 Skill Bus（注册 + 调用 + 事件）
- [ ] 把 `agent_tools.py` 现有能力拆成 `bom-edit` skill，作为第一个迁移样本
- [ ] 落 `report-export` skill 验证全链路
- [ ] 之后随 04 文档的 SKU 映射设计一起，落 `sku-mapping` skill

完成上述四步后，skill 化的骨架就立起来了，后续每个新功能都按 skill 形式增量开发。
