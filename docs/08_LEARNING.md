# 08｜边干边学的配套学习地图

> 2026-10-13 修订：本章须与 [实施计划修订与业务契约](11_PLAN_REVISION.md) 一起使用；冲突处以修订条款为准。执行顺序与原阶段编号不同，见修订第 8 节。

## 学习协议
每个阶段按以下循环：**先看概念（控制在当阶段必须懂的范围）→ 小练习 → Codex 实现最小功能 → 自己讲解调用链 → 运行测试 → 故障注入 → 小结**。不要只看 Codex 返回“Done”。

每阶段保留 `docs/learning-notes/Mxx.md`，模板：
```markdown
# Mxx 学习笔记
## 本阶段我能解释的 5 个概念
## 画出的调用链 / 数据流
## 亲自改过的 2 处代码
## 运行过的命令与结果
## 制造过的 1 个 Bug、如何定位
## 我还不懂的 3 个问题
```

## 重点知识矩阵
| 阶段 | 应理解的知识 | 验证自己真的懂了的动作 |
|---|---|---|
| M00 | Python 模块、虚拟环境、JS/TS、组件、HTTP、容器 | 不靠 Codex 自己加一个 GET API 和对应前端展示 |
| M01 | csv/zipfile、DictReader、with、Decimal、异常、类型验证 | 独立编写内存 speed/modules 的解析器与测试 |
| M02 | SQL DDL、主外键、事务、SQLAlchemy、Alembic、JSONB | 写 SQL 查指定类别价格，并故意制造冲突回滚 |
| M03 | REST 查询、HTTP 状态、查询参数、序列化、分页 | curl 实际查 GPU，并解释 400/422/404 差别 |
| M04 | CRUD、关系约束、快照、Decimal、乐观锁 | 在两个浏览器标签触发版本冲突并修复 |
| M05 | Authentication vs Authorization、Cookie/JWT、RBAC/ABAC、IDOR | 人工篡改 URL UUID 验证 403/404 |
| M06 | Prompt、Function/Tool Calling、工具返回、模型幻觉 | 画出一轮 LLM->Tool->LLM 流程，不靠模型计算价格 |
| M07 | LangGraph thread/checkpointer/state、SSE、上下文窗口 | 重启容器再继续会话；查看 checkpoint 数据 |
| M08 | 确定性规则与三态逻辑、规格证据与质量 | 让缺少 socket 的 CPU 返回 UNKNOWN 而非 PASS |
| M09 | 文档 ingest、切块、Embedding、pgvector、召回、引用 | 修改 chunk size 对比检索结果；手工检查引用 |
| M10 | StateGraph、interrupt/Command、审批、幂等 | 重复点击“批准”十次，证实只修改一次 |
| M11 | 审计、分布式追踪、Prompt Injection、评估 | 根据 trace 和失败样本找出一个具体 Bug 并加回归测试 |
| M12 | CI、Docker、环境隔离、备份恢复、运行手册 | 全新机器或干净容器从 README 部署并演示 |

## 重要概念区分
1. **LLM vs Agent**：一个生成文字，另一个围绕目标使用工具/状态/流程组织执行；但 Agent 不是自动拥有权限。
2. **RAG vs 数据库查询**：RAG 从文档查证据，不保证数据一致性；数据库是产品与价格事实源。
3. **Memory vs 业务数据**：Memory 是交互上下文/偏好，Build 是数据库里权威用户资源。
4. **Checkpoint vs 会话归属**：Checkpoint 记录模型流程状态，会话表记录谁有权读取它。
5. **Prompt 规则 vs 服务端权限**：Prompt 不是安全边界，服务端 ACL 才是。
6. **兼容性判断 vs 兼容性猜测**：缺证据必须 UNKNOWN。

## 不应该一开始学习的东西
微服务治理、Kubernetes、超大型向量搜索、Multi-Agent 辩论、复杂流批一体、实时价格采集、动态网页代码生成。这些都可能有用，但不是本项目当前学习路径的主线。

## 代码审阅清单（每次 Codex 输出后用）
- 能否解释函数输入输出类型？
- 发生异常时，错误去哪了，是否能定位源数据行号？
- 会不会把 NULL 价格误当作 0？
- 会不会按名字错误去重？
- 用户身份从哪里来，是否由后端确认？
- 有没有外部 API/LLM 依赖，能否 Mock？
- 修改操作有没有事务、版本、审批、幂等？
- 这个模型回答是否能追踪到某条 SKU 或某个文档 chunk？
- 你能运行/解释相应测试吗？
