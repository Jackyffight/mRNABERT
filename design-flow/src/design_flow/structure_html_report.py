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
        "higher_confidence": "Higher confidence / 较高置信",
        "mixed_confidence": "Mixed confidence / 混合置信",
        "low_confidence": "Low confidence / 低置信",
    }.get(value, value)


def render_structure_report(
    analysis: StructureAssessmentAnalysis,
    bundle: dict[str, Any],
    run_id: str,
    created_at: str,
) -> str:
    summary = bundle["summary"]
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
            f"<td>{_number(assessment['ptm'], 3)}</td>"
            f"<td>{escape(_band_label(assessment['confidence_band']))}</td>"
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
            f"pTM {_number(assessment['ptm'], 3)}</summary>"
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
    actions = "".join(
        "<tr>"
        f"<td><code>{escape(action['action_id'])}</code></td>"
        f"<td>{escape(action['question'])}<span>{escape(action['question_zh'])}</span></td>"
        f"<td>{escape(action['status'])}</td>"
        "</tr>"
        for action in bundle["human_actions"]["actions"]
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
<div class="metric"><b>{summary['higher_confidence_count']}</b><span>Higher confidence / 较高置信</span></div>
<div class="metric"><b>{summary['mixed_confidence_count']}</b><span>Mixed confidence / 混合置信</span></div>
<div class="metric"><b>{summary['low_confidence_count']}</b><span>Low confidence / 低置信</span></div>
</section>
<div class="notice"><strong>Scope / 边界：</strong> These are single-sequence computational hypotheses. The rules create review flags only; they do not establish folding, immunogenicity, safety, or efficacy. / 这些是单序列计算假设；规则只产生复核标记，不证明折叠、免疫原性、安全性或有效性。</div>
<h2>Batch result / 批次结果</h2>
<div class="table-wrap"><table><thead><tr><th>Candidate / 候选</th><th>AA</th><th>pLDDT</th><th>pTM</th><th>Band / 分层</th><th>Flags / 标记</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2>Candidate details / 候选详情</h2>
{''.join(details)}
<h2>Human review / 人工复核</h2>
<div class="table-wrap"><table><thead><tr><th>Action</th><th>Question / 问题</th><th>Status</th></tr></thead><tbody>{actions}</tbody></table></div>
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
