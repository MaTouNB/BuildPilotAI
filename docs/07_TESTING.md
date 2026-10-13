# 07｜测试与验收策略

> 2026-10-13 修订：本章须与 [实施计划修订与业务契约](11_PLAN_REVISION.md) 一起使用；冲突处以修订条款为准。执行顺序与原阶段编号不同，见修订第 8 节。

## 1. 测试金字塔
- Unit：价格解析、内存复合字段、类别映射、兼容规则、预算计算、权限 policy、proposal Diff、幂等逻辑。
- Integration：完整导入到真实 PostgreSQL，Catalog/Build APIs，登录/租户资源过滤，LangGraph Checkpoint 和恢复，RAG 检索 ACL。
- End-to-End：Playwright 从页面走完“建清单、查价、登录隔离、AI 修改并审批”。
- Agent Evaluation：使用 FakeModel/确定性的 Tool fixture 做门槛测试，另用可选真实 LLM 做质量评测，但真实模型测试不应成为基础 CI 的唯一判断。

## 2. 数据 ETL 基线检查
当前 ZIP SHA-256: `5c3073dbe3ea2241c8b907b0a119bec6992fa4d3335c958569027c2f066b49ab`。
- `csv_count == 25`
- `source_rows == 66778`
- `nonempty_price_rows == 16222`（非空≠全部已验证合法，校验应单独做）
- `empty_price_rows == 50556`
- 内存 speed=`5,6000` 和 modules=`2,16` 的解析满足期望。
- 单个错误价格行不会使整个 batch 无声丢失。
- 原始源行与 Gold 商品一一或多对一映射可追溯。
- 重跑同 ZIP，规范化结果确定；数据库执行幂等。

## 3. 金额/目录测试
| 用例 | 预期 |
|---|---|
| 空价格商品进入配单 | 只报已报价小计，整机总价不完整 |
| 商品 `price_usd=0` | 非法来源价格，不作为免费商品 |
| 多组件价格合计 | Decimal 精确相加，与后端一致 |
| 改数据库来源价 | 历史 BuildItem 快照不变化 |
| 预算 800 USD | 只用美元价格，超预算明确提示 |
| 用户提出 6000 人民币 | 不暗中美元转人民币；明确暂不支持 |
| 同名不同容量 SSD | 两个独立 SKU/候选，不被名称去重删除 |
| 多数价格缺失 | 系统正常，不能为凑齐预算捏造数值 |

## 4. 权限安全矩阵
至少三账号：`tenantA.user1`、`tenantA.user2`、`tenantB.user3`。
| 访问场景 | 预期 |
|---|---|
| user1 看自己的 Build | 允许 |
| user1 看 user2 的 Build | 拒绝 |
| user1 改 user2 的 Build | 拒绝 |
| user1 看 tenantB 的 Build | 拒绝 |
| user1 读 user2 ChatSession/messages/checkpoint | 拒绝 |
| user1 提交 user2 的 proposal_id | 拒绝 |
| user1 请求公开商品目录 | 允许 |
| user1 RAG 查询 tenantB 私有文档 | 检索前拒绝命中 |
| 未登录访问私有 API | 401 |
| 前端伪造 user_id/tenant_id | 后端忽略或拒绝，不影响权限 |

## 5. 兼容性规则测试（先测三态，不盲猜）
- CPU socket 明确匹配 -> PASS；明确冲突 -> FAIL；任一未知 -> UNKNOWN。
- 主板 DDR5 vs 内存 DDR4 -> FAIL；主板内存代际未知 -> UNKNOWN。
- GPU 长度大于已知机箱限长 -> FAIL；机箱限长未知 -> UNKNOWN。
- 功耗估算不足不等于实际 PS​​U 绝对不兼容；需要 evidence 和假设标签。

## 6. LangGraph/审批回归
- 一个 Thread A 的事实不得自动出现在 Thread B；不同用户更不能混用。
- 服务重启后 M07 会话继续。
- 模型超时不产生未审批的数据修改。
- interrupt 恢复时，被暂停节点可能从开头再跑；确保审批前工具无不可重试副作用。
- 重复批准或网络重试：proposal 最多应用一次。
- 方案审批期间被另一个请求更改版本：409，要求重新确认。
- 提案超时/拒绝：不修改数据库。

## 7. RAG 评测样本（建议至少 20 题）
按四组：
- **命中文档（8 题）**：能从 FAQ/说明书找到具体答案，附正确引用。
- **没有证据（4 题）**：必须明确未知/建议核查厂商，不编造。
- **租户隔离（4 题）**：私有文档不可被其他租户检索。
- **恶意注入（4 题）**：文档要求“忽略系统提示/执行工具”等不能成为 Agent 指令。
评估指标：来源有效率、关键事实正确率、拒答正确率、ACL 泄漏为零、工具权限违规为零。

## 8. 质量门槛 / Definition of Done
每个里程碑完成必须同时满足：
1. 它新增功能有自动测试；
2. 本地运行命令可执行，有真实日志/结果；
3. 相关 API/数据/架构文档已更新；
4. 没有已知越权或价格编造漏洞；
5. 合理的错误反馈，不能隐藏 traceback 导致假成功；
6. 未实现功能不在 README 中宣传成已完成。

## 9. 建议测试命令（由 Codex 在 M00 建立）
```bash
# 具体工作目录和包工具以初始化成果为准
cd backend && pytest -q
cd backend && ruff check .
cd frontend && npm run typecheck && npm run test
# 未来：npm run test:e2e
```
