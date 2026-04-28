# PEBS BOM — 对话式 BOM 生成系统

通过 3D 文件或表格自动生成产品 BOM，前端用 G6 可视化，智能体（MiniMax M2.7）可实时操控 BOM 节点。

## 架构

- **前端**：Next.js 14 + TS + AG Grid + @antv/g6 v5
- **后端**：FastAPI + SQLAlchemy
- **LLM**：MiniMax Token Plan (M2.7)，Anthropic 兼容端点
- **存储**：本地 SQLite + 文件系统（本机开发）; Postgres + MinIO（生产）

---

## 本地原生启动（推荐，不需要 Docker）

### 1. 装依赖（一次性）

```bash
brew install python@3.11 pnpm
```

### 2. 一键 setup

```bash
cd /Users/mingyue/PEBS_BOM
./scripts/setup.sh
```
脚本会：
- 创建 Python venv + 装依赖
- 装前端依赖
- 复制 `.env.example → .env`
- 建好 `apps/api/data/` 数据目录

### 3. 填 MiniMax Key

打开 `.env`，填入：
```
MINIMAX_PLAN_API_KEY=<你的 Token Plan Key>
```
Key 从 https://platform.minimaxi.com/user-center/basic-information/interface-key 获取（**Token Plan 专区**，不是按量付费）。

### 4. 启动（两个终端）

**终端 A：后端**
```bash
./scripts/dev-api.sh
# 看到 "Uvicorn running on http://127.0.0.1:8000" 即成功
```

**终端 B：前端**
```bash
./scripts/dev-web.sh
# 看到 "Ready in xxx ms" 即成功
```

打开 http://localhost:3000 。

### 5. 测试样例

```bash
python3 apps/api/.venv/bin/python scripts/make_sample_xlsx.py > /tmp/sample.xlsx
```
或用你自己的 xlsx。在首页点 "选择文件" 上传，即会跳转到 BOM 编辑页。

---

## Docker 启动（生产/完整服务）

```bash
cp .env.example .env
# 编辑 .env：
#   MINIMAX_PLAN_API_KEY=xxx
#   DATABASE_URL=postgresql+asyncpg://pebs:pebs_dev_pw@postgres:5432/pebs_bom
#   STORAGE_BACKEND=minio
docker compose up -d --build
```

---

## 能听懂的 Agent 指令

- "列出所有零件"
- "在电机组件下加一个编码器子节点，数量 1"
- "所有外购件的节点改成红色描边"
- "把螺钉 M4×10 的数量改成 30"
- "删除刹车总成及其子节点"

---

## 项目结构

```
apps/
  web/              Next.js 用户端
  api/              FastAPI 后端
    app/
      llm/          LLM provider 抽象（MiniMax M2.7）
      routes/       upload / bom / agent(SSE) / export
      services/     excel_parser / bom_normalizer / storage / exporter
      agent_tools.py  G6 工具链（add/del/update/restyle/move）
      models/       SQLAlchemy
packages/
  bom-schema/       共享 TS 类型
scripts/            setup / dev-api / dev-web / make_sample_xlsx
deploy/             init.sql (Postgres)
```

## 路线图

- [x] P0 Excel → LLM 字段映射 → BOM 表格 + G6 图 → 导出
- [x] P2 Agent 操控 G6（add/del/style/move）
- [x] P1 STEP / IGES 解析（不再用 three.js，零件结构复杂时影响性能）
- [ ] **Layer 1 补完**：SKU 智能映射 / 客户历史沉淀 / 风险预警 / ECO 影响分析
- [ ] **Layer 2 skill**：比价 / PDF 报价单解析 / 采购单生成 / SW 旁路助手
- [ ] P3 多租户 + 订阅计费（验证 PMF 后再做）
- [ ] P4 Admin 管理后台

## 战略与决策文档

项目演进过程中的战略思考与关键决策记录在 `docs/`：

- [00-原始商业规划.md](docs/00-原始商业规划.md) — 最初的商业规划全文
- [01-初步问题分析.md](docs/01-初步问题分析.md) — 反向尽调与 D1-D6 决策项
- [02-数据获取策略.md](docs/02-数据获取策略.md) — 米思米/震坤行 API 拒绝后的备选路径
- [03-产品架构与下一步.md](docs/03-产品架构与下一步.md) — 四层架构、PEBS_BOM 现状定位、立即行动清单
- [04-SKU智能映射设计.md](docs/04-SKU智能映射设计.md) — Layer 1 第一个补齐项的设计说明
- [conversations/](docs/conversations/) — 关键讨论的原始对话记录
