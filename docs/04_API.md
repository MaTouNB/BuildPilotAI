# 04｜REST API 与 SSE 事件契约（M03/M04 实现与后续设计）

> 2026-10-13 修订：本章须与 [实施计划修订与业务契约](11_PLAN_REVISION.md) 一起使用；冲突处以修订条款为准。执行顺序与原阶段编号不同，见修订第 8 节。

> M00 健康检查、M03 公共目录及 M04 手动装机已实现；M05 及后续仍为设计。版本前缀 `/api/v1`，当前响应契约见 backend/app/catalog_schemas.py 和 /docs。

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
| GET | `/api/v1/parts` | `q,category,has_price,quotable,min_price_usd,max_price_usd,sort,page,page_size` |
| GET | `/api/v1/parts/{id}` | 商品、标准规格、价格、溯源和可信度 |
| GET | `/api/v1/parts/{id}/prices` | 若有多个来源候选价格，返回冲突状态和历史快照 |
| GET | `/api/v1/data-sources` | 当前数据集元数据及价格免责声明 |

### M03 当前契约

- 只查询 is_active 的已审核规范目录。staging 源行不能用源 UUID 当作商品 ID 查询。
- 分类：CPU/GPU/MOTHERBOARD/MEMORY/STORAGE/PSU/CASE/COOLER。分类响应为 items（id/name/count）。
- q 最长120字符，大小写不敏感的字面包含查询；SQL 参数绑定，% 和 _ 不作为通配符。
- has_price 表示存在至少一个非空合法历史价；quotable 表示存在显式选定报价。
- min/max_price_usd 为正数、最多两位小数，最多12位数字；只筛选**已选报价**。未选价/缺价商品不进入金额范围结果。
- sort 只接受 name/price_asc/price_desc；价格缺失排最后，商品 UUID 作为稳定排序尾键。
- page 为1–10000，page_size 为1–100（默认20）。列表和价格历史返回 items/total/page/page_size；越过末页返回空 items。
- 商品公共字段：id/category/name/quality_status/has_price/is_quotable/quote_status/selected_price_id/price_usd/price_metadata。
- quote_status 为 SELECTED/MISSING/UNSELECTED/CONFLICT_REVIEW；未选报价的 price_usd、price_metadata、selected_price_id 为 null，不自动取最低或平均价。
- price_metadata 与历史价格记录字段相同：id、price_usd（字符串或 null）、currency、price_kind、dataset_updated_at、observed_at、source_name、quote_status、is_quotable、source_row_id。observed_at 未知为 null。
- `/parts/{id}/prices` 分页提供全部价格候选（含冲突及未选价），当前选价和历史金额不被查询修改。
- 详情增加 specifications、sources、issues、source_count、evidence_limit；specifications 保留 source_fields，未知字段为 null。sources 含原始 raw_data、输入 hash、原行 hash、文件和物理/逻辑行号、来源 URL/数据集日期。
- 详情按稳定 ID 最多展示100条源映射及这批源行的100条未解决质量提示；source_count 是完整映射数，不宣称提示覆盖全部历史。
- data-sources 返回 reviewed_parts、最近最多100个成功批次 items 及 limit。row_count 是批次源记录数；跨 pipeline 不能当作去重 SKU 数。
- 不返回本地审核者、理由或完整审核文件。此阶段是公共只读目录，不是登录后的私人数据接口。
- 无此规范商品/不活跃商品为404；非法 UUID/参数为422；数据库不可用为503。error 对象为 code/message/request_id/details，并返回 X-Request-ID；验证错误不回显原始输入/SQL/凭据。
- `/health`、`/ready` 沿用 M00 返回结构。

当前真实数据：66,778条源记录已入库，规范目录为2；两条 CPU/GPU 样例已获用户明确批准并应用，详见 M03 验收记录。合成测试使用独立 PostgreSQL schema，不混入本地业务目录。

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

## M04 已实现契约（优先于上文方案示例）

- 本地 Demo 开关 `DEMO_ENABLED`，Settings 默认关闭，开发 .env.example 显式开启。仅允许 loopback 请求、受限 Host/Origin，拒绝跨站请求；关闭后所有装机接口返回401。没有实现登录或多用户认证。
- tenant/user_account 专用 Demo 行由迁移创建，服务端选择固定身份；输入额外字段被拒绝，客户端不能声明 owner/tenant 或权威金额。
- GET /api/v1/builds：活动草稿分页 items/total/page/page_size，page=1–10000，page_size=1–100；GET /builds/{id} 返回可见草稿，归档/其他归属返回404。
- POST /builds：name（1–120，去首尾空白）、purpose（最多500，默认空）、budget_amount（正数两位Decimal或null）、budget_currency（仅USD）。返回201，空草稿版本1。
- PATCH /builds/{id}：上述完整元信息 + expected_version（正整数）；修改、移除、归档每次将版本递增1。此 PATCH 是完整元信息替换，省略 purpose/budget 会清空它们，前端发送当前完整值。
- PUT /builds/{id}/items/{slot}：part_id、quantity（严格整数1–99）、expected_price_id（当前所见报价UUID或null，必填）、expected_version。类别必须与八类槽位一致。已停用/不存在商品返回404或422。
- expected_price_id 只用于验证已确认 Diff 中的源价，没有覆盖服务器金额的能力。报价变化返回409；缺价/未选价商品可存未报价条目。客户端 price_snapshot_usd 等额外字段返回422。
- 同槽位同商品仅改数量，保留原报价ID/金额/日期，包括原来缺价的快照；换成不同商品才取新的已选价格。不能通过同一商品数量操作偷偷重新选价。
- DELETE /builds/{id}/items/{slot}：JSON body expected_version；移除条目，空槽位404。DELETE /builds/{id}：同一 body，软归档并保留数据，返回归档后的方案；不再列入活动列表。
- 所有写请求必须携带 UUID 类型 Idempotency-Key。在同事务内锁请求键、检查版本、写快照与持久化响应。相同键同方法/路径/请求体返回原结果，不重复应用；换请求内容返回409。关闭Demo后不得靠旧键读取结果。
- 对业务变更使用行锁及版本校验；选价与ETL共用 transaction advisory lock。写事务锁等待及单语句上限5秒，数据库失败回滚并返回503；前端8秒超时后可用同一键核对/重试。
- 返回 items 中的 slot/part_id/part_name/quantity/part_price_id/price_snapshot_usd/currency/source_name/dataset_updated_at/subtotal_usd；快照来自数据库NUMERIC、历史日期及具体报价FK，不随以后选价变化。
- 返回 total_usd（已报价小计字符串）、price_is_complete、build_is_complete、total_is_complete、over_budget、missing_components、unpriced_slots、pending_requirements、checked_rules_status=UNKNOWN。
- 空方案价格不完整；全部已选条目有价不代表整机完整。必需六类 + 显示与散热要求独立判定。M04 尚无审核规格 override，未选 GPU/COOLER 时保守列为待确认，不从 source_fields.graphics 或包装文字猜测可省略。
- 只有八类齐全且全部已选条目有快照时，当前模板才称“完整整机历史参考总价”。这仍不代表已验证兼容。
- 金额为字符串/null，预算和小计均USD。内存 quantity 是套装数量，不乘套装内 modules 字段。
- 409 的 error.code=VERSION_CONFLICT，401=AUTH_REQUIRED，403=FORBIDDEN，沿用请求ID与脱敏错误。
- 前端在每次写入前显示修改前/后 Diff 和确认按钮。M04 是手动 HTTP 操作；没有 Agent、提案审批库或 M10 权限/审批恢复机制。

M04没有提供上方规划表中的 POST /builds/{id}/check；兼容规则接口在M08实现。
