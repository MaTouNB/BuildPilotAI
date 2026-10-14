# 复制下面这段话作为交给 Codex 的第一条任务

> 实施前必须同时阅读 [docs/11_PLAN_REVISION.md](docs/11_PLAN_REVISION.md)。该文件定义本次修订的业务规则、验收与执行顺序；冲突处以修订条款为准。M00–M02 已实现，验收记录见 docs/verification/。后续阶段仅在用户指定后实施。

你现在要负责开发 BuildPilot AI。请先完整阅读 `README.md`、`AGENTS.md`、`docs/00_PRODUCT.md`、`docs/01_DATA_ETL.md`、`docs/02_ARCHITECTURE.md`、`docs/06_ROADMAP.md`。

**本轮只做 M00（项目环境和质量门禁）**，不要实现 M01 及后续功能；不得删除或修改 `data/raw/csv.zip`。

你必须：
1. 检查当前目录，建立 `frontend/`、`backend/`、`infra/`、`docs/` 的基础工程结构；
2. 在 `backend/` 创建可运行的 FastAPI Hello World、健康检查和 pytest；在 `frontend/` 创建可运行的 React + TypeScript + Vite 页面及基础测试；
3. 添加 Docker Compose（至少 PostgreSQL）、`.env.example`、`.gitignore`、格式检查、测试和运行说明；
4. Pin/Lock 依赖，写明使用的 Python/Node/PostgreSQL 版本；
5. 原始 ZIP 不要写进公开发布的 Git 历史，保留本地原始数据供下阶段使用；
6. 运行已有测试、记录准确结果；
7. 给出本阶段涉及的核心 Python、TypeScript、HTTP、Docker 知识点，用小练习讲解；
8. 最后只交付 M00，并等待我检查验收，再做 M01。

验收：后端 `/health` 返回 200；前端页面能够打开；PostgreSQL 可以启动；一条自动化测试通过；README 写明一键启动和调试命令。不要把尚未跑通的命令说成已跑通。
