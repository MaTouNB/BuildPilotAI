# BuildPilot AI — 电脑 DIY 智能装机 Agent（M00 工程基座）

> 实施前必须同时阅读 [docs/11_PLAN_REVISION.md](docs/11_PLAN_REVISION.md)。该文件定义本次修订的业务规则、验收与执行顺序；冲突处以修订条款为准。M00 工程基座已实现；验收结果见 docs/verification/M00.md。

> **当前已实现 M00：前后端环境、健康检查、PostgreSQL、自动测试与基础 CI 配置。** M01 及后续业务尚未实现；按修订后的阶段顺序逐关验收。

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

GitHub Actions 配置已添加，覆盖同样的检查和真实数据库 smoke；尚未在远端执行。CI 不依赖原始第三方 ZIP。

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
