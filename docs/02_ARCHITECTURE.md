# 02｜技术架构与目录结构

> 2026-10-13 修订：本章须与 [实施计划修订与业务契约](11_PLAN_REVISION.md) 一起使用；冲突处以修订条款为准。执行顺序与原阶段编号不同，见修订第 8 节。

## 1. 架构选择
**模块化单体，前后端分离**。业务 API、ETL、Agent 在一个 FastAPI 应用仓库内按模块隔离；前端 React+TS 单独目录；数据库 PostgreSQL 一个实例，必要时逻辑 schema 隔离 checkpoints/RAG。开发阶段不搭微服务、服务网格、Kafka。

```text
Browser (React/Vite/Ant Design)
  | REST + SSE, cookie/JWT credential
  v
FastAPI /api/v1
  ├── auth: 用户、租户、资源 ACL
  ├── catalog: 商品搜索、规格、历史报价
  ├── builds: 装机配置、版本、价格快照
  ├── compatibility: 三态确定性规则
  ├── chat: 会话归属、流式事件、会话消息索引
  ├── agent: LangChain Tools / LangGraph 编排 / 审批
  ├── rag: 文档摄取、Chunk、Embedding、ACL过滤
  ├── etl: ZIP -> profile/clean/import
  └── audit: 工具/资源变更审计
  |
  ├── PostgreSQL (catalog, quote, build, auth, chat, audit)
  ├── pgvector (RAG 阶段加入)
  ├── LangGraph PostgresSaver (Agent checkpoint，阶段加入)
  └── LLM provider (OpenAI-compatible; no-key mock)
```

## 2. 候选技术
- Web：React、TypeScript、Vite、Ant Design、TanStack Query、React Router；状态只在需要时加 Zustand。
- API：Python、FastAPI、Pydantic v2；SQLAlchemy 2 async or sync 由项目统一选择，避免混用；Alembic；psycopg。
- ETL：标准库 CSV、ZipFile、Decimal、Pydantic、Pytest；Pandas 仅用于探索可选。
- Agent：LangChain `create_agent` 起步，复杂流程用 LangGraph 的 state/checkpointer/interrupt。
- 数据库：PostgreSQL；RAG 时 pgvector；局部关键规格用普通结构化索引，`JSONB` 放品类差异。
- 认证：安全会话/JWT + Argon2 密码哈希；不要自己实现加密算法。开发可用 HttpOnly cookie；如 JWT，则后端验证签名/时效且防 XSS/CSRF，根据认证传输方式配置。
- 质量：Ruff、Pytest、TS typecheck、Vitest、Playwright、GitHub Actions（CI 环节加入）。
- 运行：Docker Compose；可观测性、队列、对象存储视需求引入，不强依赖。

## 3. 单仓目录（未来目标，由 Codex 按阶段逐步创建）
```text
buildpilot-ai/
├── AGENTS.md
├── README.md
├── CODEX_START_HERE.md
├── frontend/
│   ├── src/app/
│   ├── src/pages/{Login,Catalog,Builds,BuildEditor,Chat,Admin}/
│   ├── src/components/{PartCard,PriceTag,AgentPanel}/
│   ├── src/services/
│   └── src/tests/
├── backend/
│   ├── pyproject.toml
│   ├── alembic/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/{config,security,db}/
│   │   ├── api/v1/
│   │   ├── modules/{auth,catalog,builds,compatibility,chat,audit}/
│   │   ├── ai/{agent,tools,graphs,memory,rag,schemas}/
│   │   └── etl/{profile,clean,import_db,normalizers}/
│   └── tests/{unit,integration,security,evaluation}/
├── data/
│   ├── raw/csv.zip       # 本地只读，默认 Git ignore
│   ├── reports/
│   ├── processed/
│   └── fixtures/
├── infra/{docker-compose.yml,docker/}
├── docs/
└── .github/workflows/
```

## 4. 业务与 Agent 的边界
- 搜索/筛选/计算/保存是**确定性业务能力**；Agent 只能通过经过许可的 Tool Gateway 调用。
- 第一次 Agent 阶段无需 RAG：`search_parts`、`get_reference_price` 足以学习工具循环。
- 后续 RAG 用于厂商说明书、知识库 FAQ、装机故障流程；商品价格和规格不经向量搜索作为真实值。
- LangGraph Thread/Checkpoint 保存**运行状态**；`chat_session` 表保存**业务归属**，两者不能互相取代。
- `tenant_id/user_id` 只从认证上下文获得，前端展示字段仅作为 UI 状态。

## 5. 前后端协议原则
- HTTP REST JSON；可取消的 SSE 流式事件；分页、筛选、排序、显式 `request_id`。
- API 提供清晰的 Pydantic 响应模型；OpenAPI 自动生成，前端可使用类型生成器。
- 每个写入要考虑乐观锁（build.version）和幂等键（approval/工具执行）。
- 不能把 LLM 密钥放在浏览器；SSE 同样做权限鉴定和错误降敏。

## 6. 逐步可运行原则
- M00–M04 不需要模型 API，不依赖外网；产品搜索、手工装机必须可用。
- M05–M07 可以 FakeModel 运行固定测试，外部模型通过 `.env` 可选启用。
- M08–M12 逐步加入规则、RAG、审批、安全与观测，不影响基本手工装机功能。

## M00 实际架构决策

已实现 React/Vite/Ant Design 状态页、FastAPI 存活/就绪接口、同步 psycopg 数据库探测、PostgreSQL Compose，以及基础 CI。M02 ORM 默认沿用同步 SQLAlchemy 2，除非后续阶段有明确理由修改。Vite 同源代理替代开发环境开放 CORS。Python 使用 uv，前端使用 npm，两侧锁文件已生成。
