# 05｜Agent、RAG、兼容性、记忆与安全的详细设计

> 2026-10-13 修订：本章须与 [实施计划修订与业务契约](11_PLAN_REVISION.md) 一起使用；冲突处以修订条款为准。执行顺序与原阶段编号不同，见修订第 8 节。

## 1. Agent 总原则
Agent 负责解析用户意图、选择工具、按步骤组织任务和向用户解释结果；产品/价格、金额相加、兼容性结论、权限、变更提交由确定性业务服务负责。

初期用 LangChain `create_agent` + 工具，从简单的模型调用与工具循环开始；进阶使用 LangGraph 的 `StateGraph`、Checkpoint 和 `interrupt` 管理复杂生命周期。官方参考：
- https://reference.langchain.com/python/langchain/agents/factory/create_agent
- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.langchain.com/oss/python/langgraph/interrupts

## 2. 分阶段工具能力
| Tool | 入参（概念） | 输出 | 访问控制 |
|---|---|---|---|
| `search_parts` | category, keywords, priced_only, limit | 严格匹配的 SKU 候选、引用 ID | 公共商品目录，只读 |
| `get_part` | part_id | 规格 JSON、source trace | 公共商品目录，只读 |
| `get_reference_price` | part_id | Decimal price + USD + dataset date | 公共商品目录，只读 |
| `estimate_build_total` | part IDs/count | 程序计算的小计、缺价项 | 不允许模型算价格 |
| `get_build` | build_id | 自己的装机方案 | 认证用户+tenant+owner |
| `check_compatibility` | build_id 或结构化 items | PASS/FAIL/UNKNOWN 详情 | 严格规则+授权校验 |
| `propose_build_update` | build_id, patch | 变更草案和 Diff，不保存 | 服务端生成 owner/tenant |
| `apply_approved_change` | proposal_id, approval | 实际提交变更 | 审批+重新授权+版本检查+幂等 |
| `retrieve_docs` | query, permitted scope | 证据 chunk 与引用元数据 | 检索前 ACL 过滤 |

**模型不能创建任意 SQL、读文件系统、调用 shell、拼业务 HTTP 地址；只暴露白名单工具。** 工具需要参数校验、timeout、最大数据量与输出脱敏。

## 3. 基础 Agent 状态
```python
# 概念性结构，具体可由 LangGraph 的 state schema 表达
{
    "messages": [],
    "current_build_id": None,
    "candidate_parts": [],
    "compatibility_report": None,
    "pending_proposal_id": None,
    "tool_errors": []
}
```
**不要把 tenant_id/user_id 当成模型可修改 state 来决定资源权限。** 服务端在每次工具调用时注入认证后的安全执行上下文。`thread_id` 是内部 UUID，不代表授权；读取它前必须先验证 chat_session 属于当前身份。

## 4. 简单 Agent 到 LangGraph 的演进
**M06**：一个 Agent + 三个只读 Tool，测试“检索商品 -> 查询价格 -> 回复”，使用 FakeModel 测试意图与工具参数。

**M07**：增加数据库 Checkpointer；`ChatSession(id) -> thread_id` 映射；实现多轮对话，新建 session 严格隔离，重启服务之后恢复。长会话控制 context window，摘要必须遵守数据不泄露边界。

**M10**：真正的多步图：
```
User -> intent_router -> query_catalog -> draft_plan
                            |                 |
                            v                 v
                   compatibility_check -> propose_change
                                                  |
                                              interrupt
                                                  |
                                          user approval/reject
                                                  |
                                         re-auth + version check
                                                  |
                                           idempotent commit
                                                  |
                                               respond
```
LangGraph `interrupt` 在恢复时会**从被中断节点开头重新执行**；所以 interrupt 前不要执行不可重试的副作用，commit 需要事务和幂等键。未收到批准绝不能写方案。

## 5. 兼容性规则引擎（不交给 LLM 决定）
每条规则输出：`rule_id`, `status: PASS | FAIL | UNKNOWN`, `evidence`, `reason`, `affected_part_ids`, `required_missing_fields`, `confidence_note`。

第一批规则顺序：
1. CPU socket vs 主板 socket：**CPU 原始 CSV 无 socket**，只有先从可信补充表获取 socket，才能判断；缺失为 UNKNOWN。
2. 内存 DDR 代际 vs 主板内存代际：源主板 CSV 没有明确 DDR 类型；必须做可信主板规格补充，否则 UNKNOWN。
3. GPU 实际长度 vs 机箱显卡最大长度：源机箱 CSV 不给最大限长时 UNKNOWN。
4. 散热器高度 vs 机箱空间：同理，信息不足 UNKNOWN。
5. PSU 功率需求：根据保守可解释计算并标注是假设/估算，不能直接等价于整机实际峰值功耗；线缆接口、ATX/12VHPWR 等缺失时 UNKNOWN。

不要因为同厂商/同年份就猜兼容。支持通过经过审核的 `part_spec_override` 小表逐步补齐关键型号（记录来源 URL/采集日期/审核者）。规则不可用时仍允许保存装机方案，但展示不确定事项。

## 6. 预算配单与“幻觉隔离”
预算优化并非大模型记住各型号价格后生成清单，而是：
- 从 SQL 筛选**有明确历史价**的候选、锁定来源和快照；
- Agent 根据用途生成组件权重/偏好，不编造性能分数；
- 使用确定性约束程序选择候选并验证完整度（初版可以启发式，不要求最优数学规划）；
- 汇总 `Decimal` 总价，并返回 `total_is_complete`；
- 若币种为 CNY，明确拒绝直接根据 USD 旧价配 RMB 总价，或单独让用户设置手动“假设汇率”，且结果明示是假设预算，不是国内行情。

## 7. RAG（从零实现，M09 才引入）
**检索对象是文档，不是硬件报价数据库。**

### 文档来源
- 自己编写的 FAQ/装机基础知识、合法可使用的公共许可文档、厂商允许使用的说明书。
- 数据导入时记录 URL、标题、版权/许可、版本、文件 hash。未经核实的受版权保护内容不要批量抓取并公开再分发。

### Pipeline
1. `ingest`：校验文件类型、大小、ACL/来源/授权/哈希（白名单格式，禁止危险内容）。
2. `parse`：提取正文与页码/章节，保留来源定位；扫描文本注入提示只当内容。
3. `chunk`：按标题与长度切分，设置 overlap；保存 chunk_index 和 metadata。
4. `embed`：选定 Embedding 模型及维度，批量生成向量；不同模型不可混用同一向量列。
5. `retrieve`：从当前用户允许访问的文档集检索，先过滤权限（public/tenant/private），再进行精确或近似相似度排序；可以加入关键字检索融合。
6. `generate`：把证据片段和引用锚点交给 LLM；要求没有证据明确声明未知。
7. `evaluate`：建立带预期来源、答案事实和拒答例子的测试集。

### 安全与质量
- 公共文档与私有文档必须在 DB 查询层限制 scope，不能先取出所有敏感文本再让模型“保密”。
- 防 prompt injection：文档内“忽略指令”只是一段被检索的内容，不可提升为系统命令。
- 引用必须能回溯 `document_id + chunk_id + page_ref + source_url`，不存在的引用绝不生成。
- 向量检索在小规模时使用 pgvector 精确查询即可，性能需要时再加 HNSW；关注 ACL 过滤与召回问题。

## 8. 会话和记忆的区别
- 会话历史：业务表 `chat_session/chat_message`，遵守 owner/tenant。
- Agent state：LangGraph PostgresSaver Checkpoint，按 `thread_id` 保存执行状态。
- 长期记忆：非必需，后期只对白名单偏好（如喜欢 ITX/偏好静音）进行明确授权保存；带来源和删除能力。绝不把 AI 猜测的硬件事实当长期记忆。
- 商品价和方案属于业务数据库，不是 LLM Memory。

## 9. 安全威胁模型与防护
- **IDOR 越权**：攻击者猜测 build_id/session_id；所有资源读取/修改后端双维鉴权；错误码不泄露他人资源。
- **租户污染**：RAG 数据 ACL 查询前过滤；shared hardware 是公共目录，私有文档/聊天独立。
- **Prompt Injection**：把用户上传的 CSV、网页、说明书视为不可信文本；模型永远无权提升工具权限。
- **工具参数污染**：服务端工具 schema + 白名单校验；限制 limit、禁止 SQL/Shell、设置 timeout。
- **重复提交**：审批恢复重复、浏览器重试；idempotency key + 事务 + version。
- **密钥/隐私**：LLM Key 只在后端；日志脱敏、审计事件细粒度，外部模型调用前避免发送不需要的个人数据。
- **费用耗尽**：用户/租户请求频率、token、并发、工具调用次数和上下文长度限制。

## 10. 运行时观测（M11）
记录 request_id、trace_id、session_id、model、工具名、耗时、token/cost（可用时）、结果状态、失败原因。Langfuse 可作选配；不得把真实密钥或全部用户私聊无条件发送到第三方观测服务。


## M06 当前实现与部署边界

2026-10-15 用户明确选择 Mock-only。实现使用 LangChain Core StructuredTool、AIMessage/ToolMessage 和显式受限循环；没有实际 LLM、create_agent 或 LangGraph，没有外部 endpoint 适配器。只有三个公共目录只读工具，私有方案/兼容/RAG/变更工具仍待后续授权阶段。

鉴权/CSRF 在模型执行前完成。模型参数不包含身份。工具各开独立只读事务，statement_timeout 1s/lock_timeout 0.5s；异步运行模型 1s/工具 1.5s/整体 6s，最多 4 次工具调用，4 个未结束工具工作线程槽。被取消的 HTTP/超时不能杀死 Python 线程，线程完成前不释放槽。源数据库连接池等待仍由底层连接池约束，不宣称被 SQL timeout 覆盖。

请求 body 8KB、问题1000字符；消息内容上下文32KB、模型内容2KB、单工具结果16KB、最终结果64KB。异常只返回稳定错误，不记录问题、完整模型消息或凭据。LangSmith tracing 在运行作用域强制关闭，包括主机已有 tracing 环境变量的情况。

每用户10次/分钟且并发1、每租户30次/分钟且并发2、全局60次/分钟且并发4，窗口键容量2048。限流器仅进程内，当前必须单进程使用；多 worker/实例前需要共享配额。重启清空窗口，不宣称生产抗滥用能力。聊天不持久化；切换身份清空客户端结果；报价展示由真实工具输出生成，不采信模型的任意价格文字。实际模型能力/费用/token 限制需要以后接入真实 Provider 时另验。

## M07 已落实边界（Mock）

所有会话/运行/事件/历史路由以当前认证用户和租户过滤；流式轮询、工具事件写入与完成提交重新校验身份。撤销成员资格后关闭流且拒绝后续访问。内部线程不返回前端；反序列化关闭 pickle fallback，限制允许模块，外部 tracing 关闭。

上下文引用还须匹配当前 session 的已完成运行及其报价商品；污染 checkpoint 中的跨会话运行引用不能借用私人状态。上下文最多最近六条问题与上一有效商品，历史分页不等于无限模型记忆。

取消/删除通过数据库状态及同一会话锁隔离迟到提交；删除内容失败可重试且不阻塞其他删除项。数据库并发控制跨进程，频率限制仍沿用 M06 进程内实现，不能称作生产分布式配额。日志仅固定错误类别，不记录私有问句/异常原文。完整测试与限制见 [M07](verification/M07.md)。
