# BuildPilot AI — 电脑 DIY 智能装机 Agent（M02 数据库入库）

> 实施前必须同时阅读 [docs/11_PLAN_REVISION.md](docs/11_PLAN_REVISION.md)。该文件定义本次修订的业务规则、验收与执行顺序；冲突处以修订条款为准。M00–M02 已实现；验收结果见 docs/verification/。

> **当前已实现 M00 工程基座、M01 文件清洗和 M02 数据库迁移/入库。** 配件 API 和装机功能尚未实现；按修订后的阶段顺序逐关验收。

## 本地运行（M00）

需要 Node 22（本机验收 22.23.1 / npm 10.9.8）、uv 0.12.5，以及运行中的 Docker Compose v2。Python 固定为 3.12.12，由 uv 选择/安装；PostgreSQL 17.6-alpine 已固定镜像摘要。React 19.3.0、Vite 8.3.4、Ant Design 6.6.6、FastAPI 0.143.0；完整依赖见 backend/uv.lock 和 frontend/package-lock.json。

在项目根目录执行：

```bash
make setup   # 创建私有 .env；保留已有配置；安装锁定依赖
make db-up   # 启动 PostgreSQL，等待健康状态
make dev     # 同时启动前后端；Ctrl+C 停止两者
```

已有依赖与配置时，一次启动可用 `make db-up && make dev`。首次安装需要访问包源；M00 运行无需 LLM Key。

- 页面：http://127.0.0.1:5173/
- 存活：http://127.0.0.1:8000/health（不依赖数据库）
- 就绪：http://127.0.0.1:8000/ready（真实 SELECT 1；失败返回 503）
- OpenAPI：http://127.0.0.1:8000/docs
- 数据库：127.0.0.1:5433；密码只在本地 .env，脚本随机生成且不显示。

前端通过 Vite 同源代理访问 /health、/ready、/api，不需要开放跨域。只绑定 loopback，当前是单用户 Demo，尚未启用认证或业务 CRUD。

### 测试与检查

```bash
make check                   # Ruff、pytest、Oxlint、TypeScript、Vitest、前端 build
python3 scripts/smoke.py      # 真实 HTTP + 数据库验收；先运行 make db-up
```

单独调试：

```bash
cd backend
UV_CACHE_DIR=/tmp/buildpilot-uv-cache uv run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
# 另一个终端
cd frontend
npm run dev
```

GitHub Actions 已执行并通过前后端质量检查和真实数据库 smoke：[M00 基线 CI](https://github.com/MaTouNB/BuildPilotAI/actions/runs/38364422647)（提交 32d98f8）。CI 不依赖原始第三方 ZIP。

### 停止、清理与故障定位

- `make dev` 的终端按 Ctrl+C 停止前后端；`make db-down` 停止容器，保留数据库卷。
- 重建本地依赖可以移除 backend/.venv、frontend/node_modules 和 frontend/dist，然后重跑 make setup；不需要删除原始数据。
- 数据库卷 buildpilot_postgres_data 是持久数据。普通停止命令不会删除它；不要把删除卷当作日常清理步骤。
- API 未连接：确认 8000 端口服务已启动。数据库未就绪：确认 Docker 在运行、容器 healthy、.env 已配置，且 POSTGRES_PORT 与映射一致。
- 已有数据库卷时修改 .env 密码不会自动修改数据库账户密码，应恢复原凭据或进行明确的数据库凭据变更。
- 5173/8000 被占用时先停止冲突进程；开发端口固定，不静默切换。
- `npm run preview` 仅预览静态构建，不包含开发代理；完整容器部署在 M12 实施。

实现说明及真实结果：[M00 验收记录](docs/verification/M00.md)。学习讲解及三个练习：[M00 学习笔记](docs/learning-notes/M00.md)。

实现时参考：[Vite 入门](https://vite.dev/guide/)、[FastAPI 最小应用](https://fastapi.tiangolo.com/tutorial/first-steps/)、[uv 项目管理](https://docs.astral.sh/uv/guides/projects/)。

## M01 数据审计与文件清洗

M01 不导入数据库，不调用模型，新增代码仅使用 Python 标准库。前端仍是 M00 状态页。

在项目根目录运行全量验收：

```bash
python3 scripts/check_m01.py
```

该脚本校验输入哈希、核对全部 25 类基线、检查每条源行都有去向，并验证重复导出的文件逐字节一致。原始 ZIP 必须在本地；公开仓库和 CI 不包含该文件。

也可在 backend 目录分步执行：

```bash
UV_CACHE_DIR=/tmp/buildpilot-uv-cache uv run --locked python -m app.etl.profile --input ../data/raw/csv.zip --output ../data/processed/m01-profile.json
UV_CACHE_DIR=/tmp/buildpilot-uv-cache uv run --locked python -m app.etl.clean --input ../data/raw/csv.zip --output ../data/processed/m01-dry-run --dry-run
UV_CACHE_DIR=/tmp/buildpilot-uv-cache uv run --locked python -m app.etl.clean --input ../data/raw/csv.zip --output ../data/processed/m01
```

- dry-run 只输出 profile、quality_report 和归属标记；不写 JSONL、不连接数据库。必须使用独立报告目录，不能覆盖已有完整清洗输出。
- 完整清洗输出 raw.jsonl、八类 cleaned 文件、extensions.jsonl、quarantine.jsonl 和报告；全部保留在被 Git 忽略的 data/processed/。
- 源行不合并，candidate_key 只表示疑似同规格候选，不是稳定 SKU。is_quotable 一律 false，待 M02 建立目录身份和选价规则后才能报价。
- ROUND_HALF_UP 将价格明确舍入到两位小数，原价保留在 raw_data，并记录舍入警告。价格缺失保持 null；冲突不选最低价或均价。
- 所有现有源字段都保留；不擅自推断 CPU socket、主板 DDR 类型、机箱限长或不确定的频率单位。
- 重跑只替换本工具拥有且无外来文件的输出目录。不要同时向同一目录运行多个清洗进程。

实际结果与限制：[M01 验收记录](docs/verification/M01.md)。讲解与练习：[M01 学习笔记](docs/learning-notes/M01.md)。

## M02 PostgreSQL 入库

SQLAlchemy 2.1.4 / Alembic 1.20.0 已锁定。迁移先建六张业务表及 import_attempt 审计表；未审核记录保留在 raw_import_row.normalized_data 和 part_price staging，part_id 为 null，不自动创建规范 SKU。

```bash
make setup
make db-up
make db-migrate
make check        # 非数据库测试、静态检查、前端测试及构建
make check-db     # 真正 PostgreSQL 测试；随机隔离 schema，不清空业务表
make check-m02    # 本地全量入库、重跑和溯源验收；需已有 M01 输出及原始 ZIP
```

在 backend 目录分步验证/应用：

```bash
UV_CACHE_DIR=/tmp/buildpilot-uv-cache uv run --locked python -m app.etl.import_db --input ../data/processed/m01 --dry-run
UV_CACHE_DIR=/tmp/buildpilot-uv-cache uv run --locked python -m app.etl.import_db --input ../data/processed/m01 --apply
```

`--dry-run` 不连接数据库，验证分区、原行哈希、金额及审核文件。`--apply` 必须在迁移后运行；相同输入/pipeline/审核文件重跑直接返回 already_imported。导出文件内容改变但 pipeline 未升级时拒绝静默覆盖。

### 显式审核身份与选价

审核文件须由本地操作者准备，存放在被忽略的 data/reviews/。结构如下；占位符须替换为实际值：

```json
{
  "input_sha256": "<原始 ZIP 的 SHA-256>",
  "entries": [{
    "source_record_key": "<M01 导出中的源记录键>",
    "part_id": "<自行确定并长期保留的 UUID>",
    "reviewed_by": "<本地审核者>",
    "reason": "<身份核对和选价依据>",
    "select_price": false
  }]
}
```

用同一份 M01 输出配合 `--review-map ../data/reviews/approved.json --apply` 可补充审核，无需重新生成源数据。select_price=false 只确认身份；true 明确选定该行合法非空历史价。同一商品只能选一个当前价；旧价金额不改，替换过程在 import_attempt 留下原/新报价 ID、审核文件与时间。

同名不同规格不能合到同一个 UUID；新数据版本不按名字自动延续身份。只对同一输入中的相同源记录键复用此前的审核映射；跨输入版本需新的显式映射。当前仅本地 CLI 操作，不宣称已实现登录管理员权限或签名审核。

实际数据目前 **0 个已审核规范商品**，全部先入 staging；这不是导入失败。M03 展示规范目录前需要审核少量可靠样例。合成 fixtures 已验证缺价商品存在但不可报价、冲突价经显式选择后可溯源，以及旧报价金额不被后续选价修改。

[数据库 ER 与实现说明](docs/03_DATABASE.md) · [M02 验收记录](docs/verification/M02.md) · [M02 学习笔记](docs/learning-notes/M02.md)

## 一句话目标

构建一套 **React + FastAPI + PostgreSQL + LangChain/LangGraph + pgvector** 的电脑 DIY Web 应用，支持配件数据库、历史美元价格、手工装机、确定性兼容检测、RAG 问答、多用户隔离与可审批的 Agent 操作。

## 重要数据声明（所有终端/页面/API/Agent 必须一致）

- 数据源：`docyx/pc-part-dataset`：https://github.com/docyx/pc-part-dataset
- 原始文件：`data/raw/csv.zip`（当前交接包内已包含上传的原始压缩包）
- 数据集**最后更新日期**：**2025-07-23**；**不能表述成所有商品价格在当天采集**。
- 币种：USD；价格是**历史参考价，不是实时价、成交价或中国市场人民币价**。
- 不允许 Agent 编造、推测或静默补全缺失价格。
- 数据源为收集自 PCPartPicker 的第三方数据，商用/重新分发前单独核查授权。公开 GitHub 仓库建议默认不提交原始压缩包。

## 基线数据（通过压缩包实读）

- ZIP：718,533 B；SHA-256: `5c3073dbe3ea2241c8b907b0a119bec6992fa4d3335c958569027c2f066b49ab`
- 25 个 CSV，66,778 条记录；16,222 条价格字段非空，50,556 条价格缺失。
- 各品类字段、数量、价格覆盖率：`data/reports/source_profile.json`。

## 先看哪几个文件

1. `CODEX_START_HERE.md` — 直接复制给 Codex 的首轮指令。
2. `AGENTS.md` — Codex 在这个项目里必须遵守的开发约束。
3. `docs/00_PRODUCT.md` — 产品需求和范围边界。
4. `docs/06_ROADMAP.md` — 13 个依赖明确的阶段、验收门槛。
5. `docs/09_CODEX_PROMPTS.md` — 每个阶段的可复制任务指令。

## 资料目录

| 文件 | 内容 |
|---|---|
| `docs/00_PRODUCT.md` | 用户故事、边界、MVP、验收 |
| `docs/01_DATA_ETL.md` | 数据清洗方案、口径、字段转换、可复现 ETL |
| `docs/02_ARCHITECTURE.md` | 组件划分、依赖、部署结构、目录树 |
| `docs/03_DATABASE.md` | 核心实体/字段/约束与迁移计划 |
| `docs/04_API.md` | REST/SSE 设计与 JSON 示例 |
| `docs/05_AGENT_RAG_SECURITY.md` | Tools、状态、记忆、RAG、权限、审批与安全 |
| `docs/06_ROADMAP.md` | 逐阶段开发路线、每阶段学习任务及 Done 定义 |
| `docs/07_TESTING.md` | 质量、权限、数据、评估和回归测试 |
| `docs/08_LEARNING.md` | 边开发边学习的知识点与练习题 |
| `docs/09_CODEX_PROMPTS.md` | 面向 Codex 的逐关卡提示词 |
| `docs/10_DECISIONS_RISKS.md` | 架构决策、约束、风险清单 |

## 建议实施方式

- 一次只向 Codex 下发**一个阶段的任务**，完成后审阅 diff、运行测试、实际操作页面，再进入下一阶段。
- 不要把清洗数据当作 RAG 文档。**商品/价格 SQL 查询**；**说明书/FAQ 通过 RAG 检索**。
- 初期部署采用模块化单体，后续需要时再拆异步任务和 Agent Runtime。
- 开始时不需要任何付费 LLM：先使用可测试的 FakeModel/Mock 工具，直到 Agent 阶段再配置 LLM Key。
