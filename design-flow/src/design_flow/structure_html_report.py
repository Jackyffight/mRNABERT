"""Self-contained bilingual HTML for deterministic Stage 3 assessments."""

from __future__ import annotations

from html import escape
from typing import Any

from .structure_assessment import StructureAssessmentAnalysis


def _number(value: Any, digits: int = 2) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.{digits}f}"
    return "-"


def _band_label(value: str) -> str:
    return {
        "higher_confidence": "Higher combined confidence / 较高综合置信",
        "mixed_confidence": "Mixed combined confidence / 混合综合置信",
        "low_confidence": "Lower combined confidence / 较低综合置信",
    }.get(value, value)


def render_structure_report(
    analysis: StructureAssessmentAnalysis,
    bundle: dict[str, Any],
    run_id: str,
    created_at: str,
) -> str:
    summary = bundle["summary"]
    succeeded_count = analysis.result_summary["records"]["succeeded"]
    rows = []
    details = []
    for assessment in analysis.assessments:
        flags = assessment["review_flags"]
        rows.append(
            "<tr>"
            f"<td><strong>{escape(assessment['candidate_key'])}</strong>"
            f"<span>{escape(assessment['candidate_id'])}</span></td>"
            f"<td>{assessment['length']}</td>"
            f"<td>{_number(assessment['mean_plddt'])}</td>"
            f"<td>{_number(assessment['ptm'], 4)}</td>"
            f"<td>{escape(_band_label(assessment['confidence_band']))}</td>"
            f"<td>{escape(assessment['release_status'])}</td>"
            f"<td>{len(flags)}</td>"
            "</tr>"
        )
        component_rows = "".join(
            "<tr>"
            f"<td>{component['component_index']}</td>"
            f"<td>{escape(component['component_type'])}</td>"
            f"<td>{component['candidate_start']}-{component['candidate_end']}</td>"
            f"<td>{escape(str(component.get('source_protein_id') or '-'))}</td>"
            f"<td>{_number(component['mean_plddt'])}</td>"
            f"<td>{_number(component['geometry']['radius_of_gyration_angstrom'])}</td>"
            "</tr>"
            for component in assessment["components"]
        )
        comparison_rows = "".join(
            "<li>"
            f"{escape(comparison['source_protein_id'])}: "
            f"dRMSD {_number(comparison['distance_matrix_rmsd_angstrom'])} A; "
            f"pLDDT delta {_number(comparison['mean_plddt_delta'])}"
            "</li>"
            for comparison in assessment["source_geometry_comparisons"]
        ) or "<li>Not applicable / 不适用</li>"
        flag_rows = "".join(
            f"<li><code>{escape(flag['code'])}</code></li>"
            for flag in flags
        ) or "<li>None under ruleset / 当前规则下无</li>"
        geometry = assessment["geometry"]
        details.append(
            "<details>"
            f"<summary>{escape(assessment['candidate_key'])} · "
            f"pLDDT {_number(assessment['mean_plddt'])} · "
            f"pTM {_number(assessment['ptm'], 4)}</summary>"
            "<div class='detail-grid'>"
            "<section><h4>Geometry / 几何</h4>"
            f"<p>Radius of gyration / 回转半径: <b>{_number(geometry['radius_of_gyration_angstrom'])} A</b></p>"
            f"<p>End-to-end / 端到端: <b>{_number(geometry['end_to_end_distance_angstrom'])} A</b></p>"
            f"<p>Principal extents / 主轴跨度: <b>{', '.join(_number(value) for value in geometry['principal_axis_extents_angstrom'])} A</b></p>"
            f"<p>Shape anisotropy / 形状各向异性: <b>{_number(geometry['shape_anisotropy'], 3)}</b></p>"
            "</section>"
            "<section><h4>Review flags / 复核标记</h4><ul>"
            f"{flag_rows}</ul></section>"
            "</div>"
            "<h4>Components / 组件</h4>"
            "<div class='table-wrap'><table><thead><tr>"
            "<th>#</th><th>Type / 类型</th><th>Range / 范围</th>"
            "<th>Source / 来源</th><th>pLDDT</th><th>Rg (A)</th>"
            f"</tr></thead><tbody>{component_rows}</tbody></table></div>"
            "<h4>Source geometry comparisons / 来源结构几何对照</h4>"
            f"<ul>{comparison_rows}</ul>"
            "</details>"
        )
    blocking_action_ids = set(bundle["handoff"]["blocking_action_ids"])
    actions = "".join(
        "<tr>"
        f"<td><code>{escape(action['action_id'])}</code></td>"
        f"<td>{escape(action['question'])}<span>{escape(action['question_zh'])}</span></td>"
        f"<td>{escape(action['status'])}</td>"
        f"<td>{'yes / 是' if action['action_id'] in blocking_action_ids else 'no / 否'}</td>"
        "</tr>"
        for action in bundle["human_actions"]["actions"]
    )
    lower_confidence = [
        assessment["candidate_key"]
        for assessment in analysis.assessments
        if assessment["confidence_band"] == "low_confidence"
    ]
    quarantined = [
        assessment["candidate_key"]
        for assessment in analysis.assessments
        if assessment["release_status"] == "quarantined"
    ]
    limitations = "".join(
        f"<li>{escape(str(item))}</li>"
        for item in analysis.result_run_manifest.get("limitations", [])
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stage 3 · Protein structure assessment</title>
<style>
:root {{ color-scheme: light; --ink:#17211d; --muted:#5d6963; --line:#d8dedb; --paper:#fff; --wash:#f4f7f5; --green:#16633d; --amber:#9a5b00; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; color:var(--ink); background:var(--wash); font-family:Arial,"Noto Sans SC","Microsoft YaHei",sans-serif; line-height:1.55; }}
header {{ background:#17352a; color:#fff; padding:34px 28px 30px; }}
header div, main {{ max-width:1180px; margin:auto; }}
h1 {{ margin:0 0 8px; font-size:30px; letter-spacing:0; }}
h2 {{ margin:34px 0 12px; font-size:21px; }}
h3 {{ margin:0; font-size:16px; }}
h4 {{ margin:16px 0 7px; font-size:14px; }}
p {{ margin:6px 0; }}
.meta {{ color:#cbd9d2; font-size:13px; }}
main {{ padding:22px 24px 54px; }}
.summary {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; }}
.metric {{ background:var(--paper); border:1px solid var(--line); border-radius:6px; padding:14px; }}
.metric b {{ display:block; font-size:25px; color:var(--green); }}
.metric span, td span {{ display:block; color:var(--muted); font-size:12px; }}
.notice {{ border-left:4px solid var(--amber); background:#fff8e8; padding:12px 14px; margin:14px 0; }}
.table-wrap {{ overflow:auto; background:var(--paper); border:1px solid var(--line); border-radius:6px; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th,td {{ text-align:left; padding:9px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ background:#edf2ef; white-space:nowrap; }}
details {{ background:var(--paper); border:1px solid var(--line); border-radius:6px; margin:9px 0; padding:0 14px 14px; }}
summary {{ cursor:pointer; font-weight:700; padding:12px 0; }}
.detail-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
code {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }}
footer {{ color:var(--muted); font-size:12px; margin-top:32px; }}
@media (max-width:760px) {{ .summary {{ grid-template-columns:1fr 1fr; }} .detail-grid {{ grid-template-columns:1fr; }} main {{ padding:18px 12px 40px; }} header {{ padding:26px 16px; }} }}
@media print {{ body {{ background:#fff; }} details {{ break-inside:avoid; }} details[open] summary {{ display:none; }} }}
</style>
</head>
<body>
<header><div>
<h1>Stage 3 · Protein structure assessment</h1>
<p>探索性蛋白结构评估 · Deterministic exploratory report</p>
<p class="meta">Run {escape(run_id)} · {escape(created_at)} · Ruleset structure-exploratory-rules-v1</p>
</div></header>
<main>
<section class="summary">
<div class="metric"><b>{summary['candidate_count']}</b><span>Assessed / 已评估</span></div>
<div class="metric"><b>{summary['higher_confidence_count']}</b><span>Higher combined / 较高综合置信</span></div>
<div class="metric"><b>{summary['mixed_confidence_count']}</b><span>Mixed combined / 混合综合置信</span></div>
<div class="metric"><b>{summary['low_confidence_count']}</b><span>Lower combined / 较低综合置信</span></div>
</section>
<div class="notice"><strong>Scope / 边界：</strong> These are single-sequence computational hypotheses. The combined band is a local review heuristic: higher requires mean pLDDT &gt;= 80 and pTM &gt;= 0.70; mixed requires mean pLDDT &gt;= 70 and pTM &gt;= 0.50; all others enter the lower review band. It is not a calibrated probability or release gate. / 这些是单序列计算假设。综合分层只是本地复核规则：较高层要求平均 pLDDT &gt;= 80 且 pTM &gt;= 0.70，混合层要求平均 pLDDT &gt;= 70 且 pTM &gt;= 0.50，其余进入较低复核层；它不是校准概率或放行门槛。</div>
<h2>Current conclusions / 当前结论</h2>
<div class="notice"><strong>Technical result / 技术结果：</strong> {succeeded_count}/{summary['candidate_count']} checksum-bound candidates were assessed and the computational audit passed. Lower combined review band: {escape(', '.join(lower_confidence) or 'none')}. Quarantined by upstream candidate status: {escape(', '.join(quarantined) or 'none')}. No candidate is approved, rejected, or experimentally released by Stage 3. / 本节点完成全部候选的校验绑定结构评估；较低综合复核层和上游隔离状态只决定后续复核优先级，不代表实验成败或放行。</div>
<h3>Model limitations / 模型限制</h3><ul>{limitations}</ul>
<h2>Batch result / 批次结果</h2>
<div class="table-wrap"><table><thead><tr><th>Candidate / 候选</th><th>AA</th><th>pLDDT</th><th>pTM</th><th>Combined band / 综合分层</th><th>Release state / 放行状态</th><th>Flags / 标记</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2>Candidate details / 候选详情</h2>
{''.join(details)}
<h2>Human review / 人工复核</h2>
<div class="table-wrap"><table><thead><tr><th>Action</th><th>Question / 问题</th><th>Status</th><th>Due now / 当前到期</th></tr></thead><tbody>{actions}</tbody></table></div>
<h2>Process provenance / 过程溯源</h2>
<p>Model: ESMFold2-Fast <code>{escape(analysis.job_manifest['model']['structure_revision'])}</code></p>
<p>ESMC-6B: <code>{escape(analysis.job_manifest['model']['language_model_revision'])}</code></p>
<p>Job identity: <code>{escape(analysis.job_manifest['job_identity'])}</code></p>
<p>GPU run identity: <code>{escape(analysis.result_run_manifest['run_identity'])}</code></p>
<footer>Generated entirely from versioned rules and checksum-bound artifacts. Any later LLM review must be stored separately as reviewer evidence. / 本报告完全由版本化规则和校验绑定产物生成；后续 LLM 审核必须作为独立审核证据保存。</footer>
</main>
</body>
</html>
"""
