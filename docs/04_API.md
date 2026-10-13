# 04｜REST API 与 SSE 事件契约（设计稿）

> 2026-10-13 修订：本章须与 [实施计划修订与业务契约](11_PLAN_REVISION.md) 一起使用；冲突处以修订条款为准。执行顺序与原阶段编号不同，见修订第 8 节。

> 此文档是**拟实现的接口契约**，不是已经运行的服务。版本前缀 `/api/v1`，请求响应字段以 Pydantic 模型/OpenAPI 为准，具体实现时同步更新本文件。

## 1. 通用规范
- API：`/api/v1`；JSON 使用 snake_case（前端按类型生成）；分页 `page/page_size`，总数 `total`；错误均返回明确 `code/message/request_id/details`（用户不应看到内部 traceback）。
- 私有资源的 `tenant_id`、`owner_user_id` 由认证上下文确定，绝不以用户上传的字段决定权限。
- 对写操作支持 `Idempotency-Key`（尤其 Agent 审批应用）；方案更新需传 `If-Match` 或 `expected_version`。
- 价格统一返回 `price_usd` + `price_metadata`，不要只返回裸数字；`price_usd = null` 表示无报价。
- 页面均显示来源和数据集最后更新时间；搜索接口的报价字段与详情一致。
- 使用 UTC 数据库存储时区时间，前端按用户区域格式化；任何业务日期需与 UTC 区别处理。

## 2. 系统和公共配件库（M00–M03）
| 方法 | 路径 | 描述 |
|---|---|---|
| GET | `/health` | 存活健康检查；不暴露凭证或数据库敏感信息 |
| GET | `/api/v1/catalog/categories` | 可选硬件类别 |
| GET | `/api/v1/parts` | `q,category,has_price,min_price_usd,max_price_usd,sort,page,page_size` |
| GET | `/api/v1/parts/{id}` | 商品、标准规格、价格、溯源和可信度 |
| GET | `/api/v1/parts/{id}/prices` | 若有多个来源候选价格，返回冲突状态和历史快照 |
| GET | `/api/v1/data-sources` | 当前数据集元数据及价格免责声明 |

### 商品详情示例
```json
{
  "id": "example-part-uuid",
  "category": "CPU",
  "name": "Example CPU Model",
  "specifications": {"core_count": 8, "tdp_w": 120},
  "price_usd": "451.50",
  "price_metadata": {
    "currency": "USD",
    "price_kind": "historical_reference",
    "source": "docyx/pc-part-dataset",
    "dataset_updated_at": "2025-07-23",
    "source_observed_at": null
  },
  "quality_status": "PARTIAL",
  "source_traceable": true
}
```
上面 UUID/配件数据为**响应结构示例**；不是对某条原始 SKU 的报价承诺。

## 3. 认证与会话（M05）
| 方法 | 路径 | 描述 |
|---|---|---|
| POST | `/api/v1/auth/login` | 验证账号密码，设置安全 Cookie 或返回短期 token |
| POST | `/api/v1/auth/logout` | 注销，令牌/会话失效 |
| GET | `/api/v1/auth/me` | 获取已登录账户和其租户成员身份 |
| POST | `/api/v1/auth/select-tenant` | 切换当前获授权的租户（可选） |

如果采用跨站 cookie，合理配置 SameSite/Secure、CSRF 防护、CORS 白名单；不要为了方便在浏览器 localStorage 明文保存长期密钥。密码只存安全哈希。

## 4. 装机方案（M04）
| 方法 | 路径 | 描述 |
|---|---|---|
| GET | `/api/v1/builds` | 只返回当前用户在当前租户内的方案 |
| POST | `/api/v1/builds` | 创建方案，预算按 USD 指定 |
| GET | `/api/v1/builds/{id}` | 获取本人方案，返回条目+价格快照+版本 |
| PATCH | `/api/v1/builds/{id}` | 修改名称、预算等，含 expected_version |
| PUT | `/api/v1/builds/{id}/items/{slot}` | 添加/替换方案配件，重算快照与总价 |
| DELETE | `/api/v1/builds/{id}/items/{slot}` | 删除条目，需要版本检查 |
| POST | `/api/v1/builds/{id}/check` | 执行确定性兼容性校验 |
| DELETE | `/api/v1/builds/{id}` | 软删除/归档（更安全） |

### 更新条目请求示例
```json
{
  "part_id": "example-part-uuid",
  "quantity": 1,
  "expected_version": 3
}
```
### 方案总价响应示例
```json
{
  "build_id": "example-build-uuid",
  "version": 4,
  "total_usd": "749.50",
  "quoted_item_count": 6,
  "unquoted_item_count": 1,
  "total_is_complete": false,
  "price_note": "历史美元参考价；数据集最后更新于 2025-07-23"
}
```
`total_usd` 在 `total_is_complete=false` 时仅代表**已报价项小计**，不能宣传为整机完整总价。

## 5. Agent 会话（M06–M07）
| 方法 | 路径 | 描述 |
|---|---|---|
| POST | `/api/v1/ai/sessions` | 新建用户独占会话、内部绑定安全 thread_id |
| GET | `/api/v1/ai/sessions` | 当前用户会话列表 |
| GET | `/api/v1/ai/sessions/{id}` | 当前用户会话基本信息 |
| GET | `/api/v1/ai/sessions/{id}/messages` | 已持久化消息列表 |
| POST | `/api/v1/ai/sessions/{id}/messages` | 发送消息（可返回 SSE） |
| DELETE | `/api/v1/ai/sessions/{id}` | 删除/归档会话，后续清理 checkpoint |
| POST | `/api/v1/ai/sessions/{id}/cancel` | 取消尚未完成的执行（可选） |

### SSE（事件名定稿后统一 types）
```text
event: run.started
data: {"request_id":"abc","session_id":"..."}

event: message.delta
data: {"text":"我先查看硬件库..."}

event: tool.started
data: {"tool_name":"search_parts","tool_call_id":"t1"}

event: tool.completed
data: {"tool_call_id":"t1","summary":"找到 12 条候选配件"}

event: action.approval_required
data: {"proposal_id":"...","diff":{}}

event: run.completed
data: {"request_id":"abc"}
```
- 建议事件集合：`run.started / message.delta / tool.started / tool.completed / action.approval_required / run.completed / run.failed`。
- 流式生成发生超时/断网时，后端通过 `request_id` 关联最终执行状态；不得在中断连接后擅自重复写入。
- 如果认证使用 HttpOnly cookie，客户端可以考虑 EventSource 仅 GET 或 fetch 流式消费 POST；采用何者需与实际认证机制一致。

## 6. 知识库与审批（M09–M10）
| 方法 | 路径 | 描述 |
|---|---|---|
| POST | `/api/v1/knowledge/documents` | 管理员/授权用户上传支持格式并记录许可 |
| GET | `/api/v1/knowledge/documents` | 按用户权限展示可检索的文档 |
| GET | `/api/v1/knowledge/documents/{id}` | 文档元数据、解析状态、权限 |
| DELETE | `/api/v1/knowledge/documents/{id}` | 删除授权文档并清理索引 |
| GET | `/api/v1/ai/proposals/{id}` | 读取本人待审批工具变更方案 |
| POST | `/api/v1/ai/proposals/{id}/approve` | 显式批准，需重新鉴权与版本检查 |
| POST | `/api/v1/ai/proposals/{id}/reject` | 拒绝提案，审计记录 |

### 审批冲突
- 404/403：提案不存在或无权访问；可用统一 404 隐藏他人资源存在性。
- 409：目标 build 的 `version` 与提案 `expected_build_version` 不匹配。
- 410：审批已过期；409：已审批/已应用，按幂等键返回确定性结果。

## 7. 典型错误响应
```json
{
  "code": "BUILD_VERSION_CONFLICT",
  "message": "装机方案已经发生变化，请重新获取并确认",
  "request_id": "req-example",
  "details": {"expected_version": 3, "current_version": 4}
}
```

## 8. 非功能性约束
- API 列表默认分页、服务端上限，避免返回全部 66k 件数据。
- 记录请求 ID、用户 ID（安全脱敏）、耗时、错误类型；不默认把原始 Prompt/私有文档写入公开日志。
- 健康检查、就绪检查分开；LLM 不可用不应使手工装机业务整体不可用。
- Swagger/OpenAPI 可本地查看；开发中同步维护实际协议与文档。

## M00 已实现接口

| 方法 | 路径 | 响应 |
|---|---|---|
| GET | / 与 /health | 200，`{"status":"ok","service":"buildpilot-api","milestone":"M00"}` |
| GET | /ready | 数据库 SELECT 1 成功为 200，`{"status":"ready","database":"connected"}`；未配置或数据库错误为 503，`{"status":"unavailable"}` |

以上为工程探测接口，其余业务接口仍是设计稿。探测失败不向客户端返回连接参数或异常细节。
