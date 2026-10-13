# 10｜架构决策记录（ADR）与风险清单

> 2026-10-13 修订：本章须与 [实施计划修订与业务契约](11_PLAN_REVISION.md) 一起使用；冲突处以修订条款为准。执行顺序与原阶段编号不同，见修订第 8 节。

## ADR-001｜为什么第一版不选择 Dify/FastGPT
目标是深入学习模型工具调用、LangGraph 状态持久化、RAG、安全边界和审批，不优先追求低代码上线速度。因此自己开发 Tool Gateway/应用服务，使用 LangChain/LangGraph 的运行时能力，不重造通用 Agent 框架。Dify/FastGPT 后续可作为对比学习，而非第一版关键路径。

## ADR-002｜为何模块化单体而非微服务
个人练手，业务/模型/数据同时从零开始；拆微服务会引入服务发现、网络故障、部署复杂度，抵消学习收益。明确模块边界，后续再按瓶颈拆服务。

## ADR-003｜为什么先 SQL 后 RAG
价格和硬件规格有明确列、主键、约束，可用 SQL 确切检索和计算；RAG 有概率性召回，适用于 FAQ 和文档。混用会提高报价幻觉与解释成本。

## ADR-004｜USD 旧数据不可按人民币实时行情使用
所有报价统一 USD；数据集最后更新时间 2025-07-23；不宣称采价日或实时报价。人民币预算需独立的假设汇率功能并标明假设（一期不做）。缺价不算 0。金额用 Decimal 和 NUMERIC，快照可追溯。

## ADR-005｜三态兼容性
没有可信规格则 UNKNOWN，不做积极猜测；所有可确定的规则用 Python；LLM 只解释已计算结果。逐步人工补充可信规格字段与证据 URL。

## ADR-006｜企业级隔离在后端而不是 Prompt
用户私有资源靠 tenant+user ACL；Thread ID 只是定位 state，绝不是凭证；RAG 预检索 ACL；工具使用后端注入安全上下文。

## ADR-007｜修改需要明确审批
自然语言提案与实际落库分离；使用 proposal、预期版本、审批状态和幂等键；LangGraph interrupt 恢复时可能重新执行节点，副作用必须在审批和幂等保护之后。

## 主要风险和处置
| 风险 | 影响 | 处置 |
|---|---|---|
| 历史数据过期 | 不适合指导真实采购 | UI/API/Agent 强制免责声明与日期；不称现价 |
| 原始数据版权/许可 | 数据再分发或商用风险 | 个人学习；保留来源，公开仓库不含原始数据；商用前核查 |
| 多数价格缺失 | 严格预算配单候选不足 | 缺价保留，但不可报价；系统清晰解释缺价 |
| 同名不同规格/重复 | SKU 对错、价格偏差 | 综合规格候选去重+原始行映射+人工 REVIEW |
| 关键兼容字段缺失 | 错误兼容承诺 | 三态规则/证据补全/UNKNOWN |
| Agent 幻觉 | 假价格、假 SKU、假文档引用 | 工具输出为权威事实，程序计算并约束引用 |
| Token/模型成本 | 练手成本不可控 | 可 Mock，无 Key 默认离线，预算与频率限制 |
| RAG Prompt 注入 | 越权或不当工具操作 | 文档不可信，权限仅在后端，工具白名单 |
| 审批重复恢复 | 多次修改、状态破坏 | 事务 + 版本检查 + 幂等 + 审计 |
| 框架 API 变更 | 示例失效 | 以实现时官方文档为准，锁定版本与测试 |
| 个人时间有限 | 项目半途而废 | 分 M00-M12，每关可运行/可验收，不跨关乱写 |

## 发布边界
- **个人练习项目 / 演示级应用**：可以公开源代码与自行编写的知识文档（各自许可核对）。
- **企业级设计原则**：权限、可审计、错误处理、CI、可恢复、数据治理都有清晰实现和测试。
- **生产级系统**：需要真实安全审核、性能压测、运维演练、许可确认以及事故响应。未完成前不得自称已具备生产保障。

## 参考资料（在线资料可能更新）
- 源数据：https://github.com/docyx/pc-part-dataset
- LangChain create_agent：https://reference.langchain.com/python/langchain/agents/factory/create_agent
- LangGraph 状态持久化：https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph interrupts：https://docs.langchain.com/oss/python/langgraph/interrupts
- pgvector：https://github.com/pgvector/pgvector
