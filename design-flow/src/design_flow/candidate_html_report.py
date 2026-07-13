"""Bilingual HTML report for candidate specification."""

from __future__ import annotations

from html import escape
from typing import Any

from .candidate_specification import CandidateBatchAnalysis


def _e(value: Any) -> str:
    return escape(str(value), quote=True)


def _badge(status: str) -> str:
    normalized = status.lower()
    css_class = (
        "good"
        if normalized in {"pass", "ready", "released", "complete"}
        else "bad"
        if normalized in {"fail", "blocked", "rejected", "error"}
        else "warn"
    )
    return f'<span class="badge {css_class}">{_e(status.replace("_", " "))}</span>'


def _component_text(candidate: dict[str, Any]) -> str:
    parts: list[str] = []
    for component in candidate["inferred_components"]:
        if component["component_type"] == "source_segment":
            parts.append(
                f"{component['source_protein_id']}:{component['source_start']}-{component['source_end']}"
            )
        else:
            parts.append(f"addition:{component['sequence']}")
    return " -> ".join(parts)


def render_candidate_report(
    analysis: CandidateBatchAnalysis,
    bundle: dict[str, Any],
    run_id: str,
    created_at: str,
) -> str:
    candidates = bundle["candidate_batch"]["candidates"]
    candidate_rows = "".join(
        "<tr>"
        f"<td><strong>{_e(candidate['display_name'])}</strong><code>{_e(candidate['candidate_key'])}</code></td>"
        f"<td>{_e(candidate['candidate_type'])}</td>"
        f"<td>{len(candidate['amino_acid_sequence'])}</td>"
        f"<td>{_e(_component_text(candidate))}</td>"
        f"<td>{_e(candidate['translation_relation']['relation'])}</td>"
        f"<td>{_badge(candidate['release_status'])}</td>"
        f"<td>{_badge('ready' if candidate['exploratory_structure_ready'] else 'blocked')}</td>"
        "</tr>"
        for candidate in candidates
    )
    findings = [
        issue
        for candidate in candidates
        for issue in candidate.get("issues", [])
    ]
    finding_rows = "".join(
        "<tr>"
        f"<td>{_badge(str(finding['severity']))}</td>"
        f"<td><code>{_e(finding['protein_id'] or 'project')}</code></td>"
        f"<td><code>{_e(finding['code'])}</code></td>"
        f"<td>{_e(finding['message'])}</td>"
        "</tr>"
        for finding in findings
    ) or '<tr><td colspan="4">没有候选发现 / No candidate findings</td></tr>'
    action_rows = "".join(
        '<article class="action">'
        f"<div><code>{_e(action['action_id'])}</code>{_badge(action['status'])}</div>"
        f"<h3>{_e(action.get('question_zh') or action['question'])}</h3>"
        f"<p>{_e(action['question'])}</p>"
        f"<small>Required before: {_e(action['required_before_stage'])} | Owner: {_e(action['owner'])}</small>"
        "</article>"
        for action in bundle["human_actions"]["actions"]
        if action["status"] == "open"
    )
    model_cards = "".join(
        '<article class="model-card">'
        f"<div><strong>{_e(model_name)}</strong>{_badge(record['status'])}</div>"
        f'<p>{_e(record["summary_zh"])}</p><p class="en">{_e(record["summary"])}</p>'
        "</article>"
        for model_name, record in bundle["model_inputs"]["models"].items()
    )
    summary = bundle["summary"]
    output_summary = bundle["output_audit"]["summary"]
    source_ready = analysis.source_handoff.get("readiness") == "ready"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>候选规格与生成 / Candidate specification</title>
  <style>
    :root {{ --ink:#17211b; --muted:#667169; --line:#d8ded9; --green:#176b45;
      --green-bg:#e8f5ed; --amber:#8a5a00; --amber-bg:#fff4d8; --red:#a52b2b;
      --red-bg:#fdeaea; --paper:#fff; --wash:#f5f7f5; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:var(--wash); font-family:Arial,"Noto Sans CJK SC",sans-serif; line-height:1.55; }}
    main {{ max-width:1240px; margin:0 auto; background:var(--paper); min-height:100vh; box-shadow:0 0 30px rgba(20,40,28,.08); }}
    header {{ padding:42px 52px 34px; border-bottom:4px solid var(--green); }}
    h1 {{ margin:0; font-size:32px; letter-spacing:0; }}
    header p {{ color:var(--muted); margin:8px 0 0; }}
    section {{ padding:30px 52px; border-bottom:1px solid var(--line); }}
    h2 {{ margin:0 0 14px; font-size:22px; }}
    h3 {{ font-size:16px; margin:8px 0; }}
    code {{ display:block; color:#405047; overflow-wrap:anywhere; margin-top:4px; }}
    .en {{ color:var(--muted); }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    .metric, .model-card, .action {{ border:1px solid var(--line); padding:15px; border-radius:6px; background:#fff; }}
    .metric strong {{ display:block; font-size:25px; }}
    .badge {{ display:inline-block; padding:3px 7px; border-radius:4px; font-size:11px; font-weight:700; text-transform:uppercase; margin-left:6px; }}
    .badge.good {{ color:var(--green); background:var(--green-bg); }}
    .badge.warn {{ color:var(--amber); background:var(--amber-bg); }}
    .badge.bad {{ color:var(--red); background:var(--red-bg); }}
    .notice {{ padding:14px 16px; background:var(--amber-bg); border-left:4px solid var(--amber); margin-top:18px; }}
    .table {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ padding:10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
    th {{ background:#eef2ef; white-space:nowrap; }}
    .models {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }}
    .actions {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }}
    footer {{ padding:22px 52px; color:var(--muted); font-size:12px; }}
    @media (max-width:800px) {{ header,section,footer {{ padding-left:20px; padding-right:20px; }} .grid,.models,.actions {{ grid-template-columns:1fr; }} h1 {{ font-size:25px; }} }}
  </style>
</head>
<body><main>
  <header>
    <h1>候选规格与生成</h1>
    <p>Candidate specification and generation</p>
    <p>本节点把源对照、截短体与手工融合体转换为可审计候选清单，并生成下一模型节点的精确输入。</p>
    <p class="en">This node converts source controls, truncations, and manual fusions into an audited candidate batch and exact downstream model inputs.</p>
  </header>
  <section>
    <h2>当前结论 / Conclusions</h2>
    <div class="grid">
      <div class="metric"><strong>{output_summary['candidate_count']}</strong>候选记录<br><span class="en">candidate records</span></div>
      <div class="metric"><strong>{output_summary['source_control_count']}</strong>源对照<br><span class="en">source controls</span></div>
      <div class="metric"><strong>{output_summary['manual_candidate_count']}</strong>手工候选<br><span class="en">manual candidates</span></div>
      <div class="metric"><strong>{output_summary['exploratory_structure_ready_count']}</strong>可探索折叠<br><span class="en">exploratory structure inputs</span></div>
    </div>
    <div class="notice"><strong>当前批次是 provisional，不是实验放行。</strong>
      第一节点 handoff={_e(analysis.source_handoff.get('readiness'))}；候选节点状态={_e(summary['status'])}。
      <span class="en">This batch is provisional, not released for experiment. Source handoff ready: {_e(source_ready)}.</span>
    </div>
  </section>
  <section>
    <h2>模型启动顺序 / Model launch order</h2>
    <div class="models">{model_cards}</div>
  </section>
  <section>
    <h2>候选批次 / Candidate batch</h2>
    <div class="table"><table>
      <thead><tr><th>候选 / Candidate</th><th>类型 / Type</th><th>AA</th><th>序列推断组件 / Sequence-derived components</th><th>CDS 关系 / Translation</th><th>放行 / Release</th><th>ESMFold2</th></tr></thead>
      <tbody>{candidate_rows}</tbody>
    </table></div>
  </section>
  <section>
    <h2>审计发现 / Audit findings</h2>
    <div class="table"><table><thead><tr><th>级别</th><th>候选</th><th>规则</th><th>证据 / Evidence</th></tr></thead><tbody>{finding_rows}</tbody></table></div>
  </section>
  <section>
    <h2>人工确认 / Human decisions</h2>
    <div class="actions">{action_rows}</div>
  </section>
  <footer>Project {_e(analysis.config.project_id)} | Run {_e(run_id)} | Created {_e(created_at)} | Source run {_e(analysis.source_run_id)}</footer>
</main></body></html>
"""
