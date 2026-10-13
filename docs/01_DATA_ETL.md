# 01｜数据审计、清洗与 ETL 技术设计

> 2026-10-13 修订：本章须与 [实施计划修订与业务契约](11_PLAN_REVISION.md) 一起使用；冲突处以修订条款为准。执行顺序与原阶段编号不同，见修订第 8 节。

## 0. 输入事实与基线
- 输入：`data/raw/csv.zip`，其 SHA256 为 `5c3073dbe3ea2241c8b907b0a119bec6992fa4d3335c958569027c2f066b49ab`。
- 内含 25 个 CSV、66,778 条记录；非空 `price` 16,222 条，其余 50,556 条缺价。
- 各类字段见 `data/reports/source_profile.json`（基于原始 ZIP 自动生成）。
- 数据集最后更新：**2025-07-23**；是数据集级元数据，绝不是每条商品价格的采集时间。
- 始终保留原始 ZIP；导入程序不得覆盖原始数据。

## 1. 推荐技术栈
Python 3.12+（实际实现固定环境）、标准库 `zipfile/csv/io/hashlib/decimal/json/re`、Pydantic v2、Pytest、SQLAlchemy 2、Alembic、PostgreSQL。66k 行标准库足够，Pandas 可在探索统计时额外加入，避免把 ETL 逻辑绑定到 DataFrame 的隐式转换。

## 2. 分层
1. `raw`：保留 ZIP、文件哈希、类别路径、源头行位置与原始字典；不可变。
2. `staging`：统一字段命名，解析金额和规格，生成 `issues`、`warnings`、`provenance`，但不丢掉空价格数据。
3. `catalog`：可供 API 查询的统一 HardwarePart、PartPrice 和基础筛选字段。
4. `quotable_view`：价格为合法 Decimal 且必要信息满足要求的报价候选子集。缺价配件只排除报价，不排除商品目录。

## 3. 通用字段与规则
统一输出：
```
source_dataset, dataset_updated_at, source_file, source_line_number,
source_record_hash, category, name_raw, name_normalized,
specifications_json, price_usd, currency, quote_status,
quality_status, import_batch_id
```
- `source_record_hash`: 原始 CSV 标准化序列化内容的 SHA256（不含源行号）；单独保留 `source_file + source_line_number` 以支持追踪。
- `source_record_key`: `dataset_id + source_file + source_line_number` 的确定性键；用于重跑定位，不作为跨不同数据版本稳定 SKU。
- `price`: `"" -> NULL`；非空转 Decimal，必须 finite 且 >0；非法价格进入问题清单。用 NUMERIC(12,2) 时对小数精度执行明确四舍五入政策，不静默截断。
- `name`: trim + 统一 Unicode 和多余空白；保留原始名称；空名称为 `ERROR`。
- 数据单位：频率标注 MHz/GHz 或 MT/s，显存/存储 GB，长度 mm，功率 W；**没有证据不能自动猜单位**。
- 未知规格保留 NULL 和 `UNKNOWN`；不推断主板支持某代 CPU 或 GPU 一定装得下机箱。
- 对异常值优先标记 `REVIEW`，而不是删除/填平均值。

## 4. 分类与字段清洗
首批只实现八类主机硬件，映射：
```
cpu -> CPU
motherboard -> MOTHERBOARD
video-card -> GPU
memory -> MEMORY
internal-hard-drive -> STORAGE
power-supply -> PSU
case -> CASE
cpu-cooler -> COOLER
```
随后保留 25 类全部商品的原始记录，未知类型进入扩展类别或待处理，不丢数据。

| 品类 | 源字段 | 转换结果/注意事项 |
|---|---|---|
| CPU | core_count, core_clock, boost_clock, tdp | 解析 int/Decimal；**源中无 socket** |
| 主板 | socket, form_factor, max_memory, memory_slots | socket 保留标准字符串；**源中无明确内存代际** |
| GPU | chipset, memory, length, boost_clock | 显存 GB、长度 mm；部分缺省 |
| 内存 | speed=`5,6000` | DDR5、6000 MT/s，必须验证 split 数量/数值 |
| 内存 | modules=`2,16` | 2 条、每条 16 GB，总 32 GB |
| 硬盘 | capacity, type, interface, form_factor | 不要直接凭名字推导主板插槽兼容 |
| 电源 | wattage, efficiency, modular, type | PSU 功率与标准化效率、模组化类型 |
| 机箱 | type, external_volume, internal_35_bays | **源中通常没有显卡最大限长** |
| 散热器 | rpm, noise_level, size | 复杂字段谨慎拆解，先保留原值 |

## 5. 去重策略（不可按 name 直接去重）
- **源重复**：完全相同的原始字段行，原始记录仍全部保留。规范化目录可以归并，但必须在映射表保留全部源行。
- **自然键候选**：`category + 规范名称 + 关键规格`，只作为候选去重，不是绝对 SKU。特别注意不同容量 SSD、不同容量/条数的同系列内存、不同芯片组显卡。
- **价格冲突**：完全相同规格却存在不同价，禁止盲目取均价或最低价；形成多个 `source_price_entry`，标记冲突并使用明确选价政策（初版优先保留源顺序、给出 REVIEW；可配置人工选定）。
- **人工治理**：为高频候选型号提供 alias/merge 记录，原始记录不丢。
- 源实读显示很多同名/完全重复行；具体数量以清洗程序产生的可重现报告为准。

## 6. 异常、质量与失败策略
- `ERROR`：非法必填字段、价格格式无法解析、列结构破坏；行进入 quarantine，不写入规范化目录。
- `WARN`：缺价、型号重复嫌疑、规格缺失/超出合理范围；可保留到目录但部分功能不可用。
- `INFO`：规范化成功。
- 不能 `except Exception: continue` 静默吞错；记录原因、源路径、行号、源行摘要。
- 批次总结要包含：总数、成功数、ERROR/WARN、各品类价格覆盖率、重复分布、未知字段、耗时、输入 SHA。
- 首版单次整批 ETL 运行可以在本地完成，不需要异步分布式系统。

## 7. 幂等与复现
- 首先校验 ZIP 哈希；对每个 `import_batch` 记录 source/hash/数据集日期/程序版本/状态。
- 规范化后的价格与商品使用源映射或者唯一键 + UPSERT；同一 ZIP 重跑结果一致，行数不增长。
- 支持 `--dry-run` 只出质量报告，`--apply` 执行数据库事务入库；异常时回滚本批次。
- 报告导出：`profile.json`、`quality_report.json`、`quarantine.jsonl`、`cleaned_<category>.jsonl`；日志不泄露用户隐私。

## 8. 计划 CLI（M01/M02 实现，不是当前已存在命令）
```bash
python -m app.etl.profile --input data/raw/csv.zip --output data/reports/profile.json
python -m app.etl.clean --input data/raw/csv.zip --output data/processed/ --dry-run
python -m app.etl.import_db --input data/processed/ --apply
```

## 9. 测试样例（M01 必做）
- `parse_price(' 129.99 ') == Decimal('129.99')`; `'' -> None`; `abc`, `-10`, `NaN`, `Infinity` -> 拒绝。
- `speed='5,6000' -> DDR5/6000`; `modules='2,16' -> 2 × 16`; 拆分不足、零值、非数字时进入 quarantine。
- 同名不同规格不能被合并；完全重复行的 provenance 不丢。
- 数据质量测试：读取 25 个 CSV，共 66,778 条源行；16,222 个非空价格；源数据 SHA 必须匹配（此版本数据）。
- 重复导入后规范表和报价表不增长；任一清洗错误不允许制造假价格。

## 10. 严格报价约束
所有返回报价的数据结构必须包含：`value`, `currency='USD'`, `price_kind='historical_reference'`, `dataset_updated_at='2025-07-23'`, `source_dataset`, `is_quotable`。UI 与 Agent 解释中始终显示同等信息。预算功能第一版只接受 USD，人民币预算提示暂不支持或需要用户明确输入人工设定的换算假设，绝不静默转换。
