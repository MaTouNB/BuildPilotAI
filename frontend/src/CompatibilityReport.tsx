import { useState } from 'react'
import Alert from 'antd/es/alert'
import Button from 'antd/es/button'
import Tag from 'antd/es/tag'
import { useResource } from './useResource'
type Status = 'PASS' | 'FAIL' | 'UNKNOWN'
type Evidence = { id: string; part_id: string; field_name: string; value: string; unit: string | null; source_url: string; source_locator: string; retrieved_at: string; version: number }
export type Compatibility = { build_id: string; build_version: number; checked_at: string; ruleset_version: string; checked_rules_status: Status; rules: { rule_id: string; title: string; status: Status; evidence: Evidence[]; missing_fields: string[]; issues: string[]; explanation: string; compared_values: (string | null)[] }[]; uncovered_checks: string[] }
const fieldNames: Record<string, string> = { 'CPU.part_id': '处理器', 'MOTHERBOARD.part_id': '主板', 'MEMORY.part_id': '内存', 'GPU.part_id': '显卡', 'CASE.part_id': '机箱', 'CPU.socket': '处理器插槽', 'MOTHERBOARD.socket': '主板插槽', 'MEMORY.memory_type': '内存 DDR 代际', 'MOTHERBOARD.memory_type': '主板支持的 DDR 代际', 'GPU.length_mm': '显卡长度（mm）', 'CASE.max_gpu_length_mm': '机箱显卡空间（mm）' }
const colors = { PASS: 'success', FAIL: 'error', UNKNOWN: 'warning' }
export default function CompatibilityReport({ id, version }: { id: string; version: number }) {
  const [attempt, setAttempt] = useState(0)
  const resource = useResource<Compatibility>(`/builds/${id}/compatibility?expected_version=${version}`, attempt)
  return <section className="compatibility-report" aria-label="兼容规则报告"><div className="panel-heading"><h3>兼容规则报告</h3><Button onClick={() => setAttempt(x => x + 1)}>重新检查</Button></div>
    <details><summary>在会话中检查此版本</summary><p>将以下问题发送给私人会话助手：</p><code>检查方案 {id} 版本 {version} 的兼容性</code></details>
    <p>仅检查下列三项条件；PASS 不代表整机兼容。未审核的原始规格不参与判定。</p>
    {resource.status === 'loading' ? <p role="status">正在检查兼容规则…</p> : resource.status === 'error' ? <Alert type="error" title="兼容报告不可用" description="方案可能有新版本或服务暂不可用。请刷新方案后重试。" /> : resource.data.build_id !== id || resource.data.build_version !== version ? <Alert type="warning" title="报告版本已过期，请刷新方案" /> : <>
      <CompatibilityEvidence report={resource.data} />
    </>}
  </section>
}

export function CompatibilityEvidence({ report }: { report: Compatibility }) { return <section aria-label="兼容检查证据">
      <p><Tag color={colors[report.checked_rules_status]}>已检查规则：{report.checked_rules_status}</Tag>方案版本 {report.build_version} · 规则 {report.ruleset_version}</p>
      <p className="result-meta">检查时间：{new Date(report.checked_at).toLocaleString()}</p>
      <ul className="compatibility-rules">{report.rules.map(rule => <li key={rule.rule_id}><h4><Tag color={colors[rule.status]}>{rule.status}</Tag>{rule.title}</h4><small>{rule.rule_id}</small><p>{rule.explanation}</p>
        {rule.compared_values.every(value => value !== null) && <p>对比值：{rule.compared_values.join(' / ')}</p>}
        {rule.missing_fields.length > 0 && <p>缺少可用证据：{rule.missing_fields.map(field => fieldNames[field] ?? field).join('、')}</p>}
        {rule.issues.some(issue => issue.includes('CONFLICT_REVIEW')) && <Alert type="warning" title="规格证据冲突，需人工审核" />}
        {rule.evidence.map(proof => <details key={proof.id}><summary>{proof.field_name} = {proof.value}{proof.unit ? ` ${proof.unit}` : ''}（审核版本 {proof.version}）</summary><p>商品 ID：{proof.part_id}</p><a href={proof.source_url} target="_blank" rel="noreferrer">查看规格来源</a><p>{proof.source_locator}</p><p>查阅时间：{new Date(proof.retrieved_at).toLocaleString()}</p></details>)}
      </li>)}</ul>
      <h4>尚未检查</h4><ul>{report.uncovered_checks.map(text => <li key={text}>{text}</li>)}</ul>
</section> }
