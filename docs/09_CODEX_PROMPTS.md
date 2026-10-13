# 09｜分关卡 Codex 执行指令模板

> 2026-10-13 修订：本章须与 [实施计划修订与业务契约](11_PLAN_REVISION.md) 一起使用；冲突处以修订条款为准。执行顺序与原阶段编号不同，见修订第 8 节。

> **使用方法：** 把本交接包解压到一个 Git 仓库。首次向 Codex 发送 `CODEX_START_HERE.md` 中的指令。以后每次只复制下面一关的内容。每关末尾加：“本轮仅实施此阶段，不要自动推进下一阶段；输出完成内容、真实测试结果、学习讲解与验收步骤”。

## 通用任务前缀（每关先贴）
```text
你是 BuildPilot AI 的全栈开发者与教学伙伴。先阅读 AGENTS.md、README.md、docs/06_ROADMAP.md、对应技术文档。只实施我指定的里程碑，不要越界。
任务完成后必须：(1) 运行真正的测试/静态检查，报告命令与结果；(2) 更新文档；(3) 解释核心调用链和重要语法；(4) 给我 3 个能手动操作的小练习；(5) 说明局限与下一步建议，但不能擅自实现后续里程碑。
如要引入新的库，先解释原因，并使用受支持的版本及锁文件。不要写死 API Key。不可伪称运行/下载/测试成功。原始数据源：data/raw/csv.zip，价格为历史 USD，数据集最后更新 2025-07-23。
```

## M00｜搭好前后端、数据库与质量门禁
```text
只做 M00。初始化 backend(FastAPI+Pydantic+pytest+ruff) 与 frontend(React+TS+Vite+Ant Design+Vitest)，Docker Compose 提供 PostgreSQL。实现健康检查、首页 API 连通状态、配置样例文件、开发/测试/清理说明。数据 ZIP 不解压覆盖，不上 Git 公共仓库。先不写业务 CRUD，也不调用 LLM。
验收：前后端页面可运行，数据库可连接，至少各一条测试通过，版本和依赖锁定。请逐行解释 FastAPI 最小入口、React 最小页面与 Docker Compose 之间的关系。
```

## M01｜数据审计 + 清洗 ETL（文件阶段）
```text
只做 M01。读取 data/raw/csv.zip；校验 SHA256=5c3073dbe3ea2241c8b907b0a119bec6992fa4d3335c958569027c2f066b49ab，25 个 CSV、66778 源行、16222 条非空价。按 docs/01_DATA_ETL.md 实现 profile、清洗、quarantine、normalizer 和对应 pytest。最先实现 CPU、motherboard、video-card、memory，再扩展八类主机配件。必须保留原始行追踪；price Decimal 空值 NULL；同名不可直接合并；speed='5,6000' 和 modules='2,16' 正确解析。输出 JSON/JSONL 质量报告，不接数据库。提供 dry-run CLI。最后给出 Python 语法和清洗规则学习笔记。
```

## M02｜PostgreSQL 表与幂等 ETL 入库
```text
只做 M02。阅读 docs/03_DATABASE.md，创建 Alembic 迁移、SQLAlchemy Models、ETL import CLI，将 M01 规范化结果导入 PostgreSQL。raw_import_row、import_batch、hardware_part、part_source_map、part_price、data_quality_issue 能溯源。价格 NUMERIC、NULL 不变零；实现来源冲突 REVIEW 和可重跑幂等。写真实 PostgreSQL 集成测试、导入统计和数据库 ER 图。不得删除原始 ZIP；不得按 name 去重。
```

## M03｜商品目录 REST 和前端搜索
```text
只做 M03。依据 docs/04_API.md 提供 /api/v1/parts、/parts/{id}、/catalog/categories、/data-sources；分页和按品类、名称、是否有价、价格范围筛选。React 展示商品卡片、详情、搜索与空/错误状态。任何价格页面必须显示“USD 历史参考价 / 数据集最后更新于 2025-07-23”。编写真实数据库 API 测试与前端测试，不接 AI。
```

## M04｜手动装机工作台
```text
只做 M04。创建 Build 和 BuildItem 模型、迁移和 CRUD；从真实 Part ID 添加、替换、移除配件。选择时持久化历史价格快照、来源与数据集日期；总价由 Decimal 计算；缺价项提示“合计不完整”，不能当 0。只支持 USD 预算，版本乐观锁冲突返回 409。开发装机工作台：按 CPU/主板/GPU/内存/SSD/电源/机箱/散热搜索选配，显示方案明细、保存、加载、错误。M05 之前必须明显标记单用户 Demo，不伪装成已隔离多用户。
```

## M05｜真正实现用户登录和租户隔离
```text
只做 M05。创建账户、租户、成员关系和安全登录/登出；密码哈希使用成熟库。把 Build API 改为严格按已认证 tenant_id+owner_user_id 鉴权，前端支持登录与当前身份。准备 A 租户两用户、B 租户一用户的 demo fixtures；写跨用户、跨租户、篡改 URL/Body 用户ID、未登录访问、登出失效的安全集成测试。权限永远不来自用户可控 Prompt、URL 或前端字段。
```

## M06｜最小 LangChain Agent Tool Calling
```text
只做 M06。添加可配置 OpenAI-compatible LLM（无 Key 使用 mock 模式），用当前官方 LangChain create_agent 与工具机制实现搜索配件、读取商品详情、读取历史报价三个只读工具，放在后端 Tool Gateway 统一鉴权/校验、限制输出量。前端右侧增加 AI 助手窗口。演示用户“查询 Ryzen 7 9800X3D 的历史美元价格”，Agent 必须基于商品数据库回答并注明日期，不允许编造价。为工具调用加结构化日志和 Mock LLM 测试。不要先上 RAG 或长期记忆。
```

## M07｜LangGraph 持久化 Thread、多会话、SSE
```text
只做 M07。依照 docs/05_AGENT_RAG_SECURITY.md 完成业务 chat_session/chat_message 与 LangGraph PostgreSQL checkpointer 映射；服务器生成 thread_id、会话 ID，前端可以创建/列出/继续/删除会话，使用 SSE 逐步输出 message.delta 与 tool 事件。重新启动后还能继续；不同 session/不同用户/不同租户都不能串数据。Mock 测试持久化、断线、会话权限；请解释 checkpointer 与业务会话归属为何是两个问题。
```

## M08｜兼容性三态规则引擎
```text
只做 M08。设计 Python CompatibilityRule 协议，规则输出 PASS/FAIL/UNKNOWN+证据+缺失字段。根据可信且可审计的补充规格表做 CPU socket 对主板、内存代际对主板、GPU 长度对机箱可容纳长度三条规则。原始 CSV 缺 CPU socket、主板内存代际和机箱限长时必须 UNKNOWN，严禁按型号名称幻觉补全。Agent 只能解释工具给出的规则结果。参数化测试匹配、冲突、数据未知三个路径。
```

## M09｜从零做 RAG
```text
只做 M09。不要给硬件目录做语义向量当价格来源。创建合法文档上传/自写 FAQ 的 ingestion、内容解析、chunking、embedding、pgvector 检索与引用。固定 embedding 模型和维度，公开/租户/个人文档在 DB 查询前进行 ACL 限制。工具 retrieve_docs 返回真实 document_id/chunk_id/page/source。编写至少20条确定性评测题（命中、无证据、租户隔离、注入）。提供 no-key mock embedding 供基本测试；真实 embedding 作为可选配置。输出 RAG 数据流图和学习笔记。
```

## M10｜自然语言组装与审批修改
```text
只做 M10。实现自然语言选配 Agent：按用途生成候选，工具搜索可报价配件，确定性程序算美元预算与兼容报告；返回结构化草案。用户说“修改我已保存的清单，把显卡更换”时，先调用只读工具并生成 action_proposal（Diff、target_build_id、expected_version、失效时间），LangGraph interrupt 暂停；React 展示批准/拒绝。批准 API 必须重新鉴权、检查版本、事务性幂等提交；未经批准不写数据；模拟重复点击/中途并发编辑/审批过期/断线恢复。
```

## M11｜可观测、评测与安全补齐
```text
只做 M11。加入 request_id、trace、模型/工具耗时、审计日志、敏感信息脱敏；按需加自部署 Langfuse。对跨租户 IDOR、RAG ACL、文档 prompt injection、工具滥用、模型错误/超时、重复写入、限流开展系统性测试。建立可回归的评测脚本，输出 price_hallucination、tool_success、source_validity、permission_violations 等指标，报告真实结果和风险，不写虚构准确率。
```

## M12｜CI、Docker 化、完整演示交付
```text
只做 M12。创建 frontend/backend/PostgreSQL Docker 服务、迁移/seed 执行、CI 测试/静态检查、环境配置、生产部署注意事项、备份/恢复和运行手册。提供完整演示路径：登录、搜索硬件、创建方案、历史价标记、AI 查价、兼容 UNKNOWN、RAG 真实引用、审批改配置、跨用户资源访问失败。请在干净环境启动，真实执行测试并保留结果。不得夸大为已经过生产并发认证的企业级系统。
```

## Codex 卡住时的修复指令（不要重新生成整个项目）
```text
请仅修复以下测试/错误，不重构无关模块。先复现，判断是环境/数据/业务逻辑/版本兼容哪类问题；给出最小修改，并为本次错误增加回归测试。说明为什么原实现失败、验证命令是什么，以及修复后真实结果。不要悄悄修改价格口径、数据文件或权限策略。

【把错误堆栈和复现步骤粘贴在这里】
```

## 每次完成后的学习问答模板
```text
本阶段代码已通过测试。请暂时不要写代码，像技术导师一样把本次提交里最重要的三个设计决策讲清楚：输入是什么，输出是什么，失败路径是什么。对涉及的 Python/TypeScript/LangGraph 关键语法逐行讲解；最后出 5 道小题测试我是否理解，等我回答后再评分。
```
