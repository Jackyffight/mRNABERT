"""Bilingual deterministic reports for Stage 4 and Stage 5."""

from __future__ import annotations

from html import escape
from typing import Any

from .requirement_gates import REQUIREMENT_CLASS_LABELS


def _number(value: Any, digits: int = 3) -> str:
    return f"{float(value):.{digits}f}" if isinstance(value, (int, float)) else "-"


def _requirement_class_label(requirement_class: str) -> str:
    labels = REQUIREMENT_CLASS_LABELS.get(
        requirement_class, (requirement_class, "未分类")
    )
    return f"{labels[0]} / {labels[1]}"


def _requirement_cards(requirements: list[dict[str, Any]]) -> str:
    if not requirements:
        return "<div class='req'>No missing requirement / 无缺失要求</div>"
    return "".join(
        "<div class='req'>"
        f"<div><code>{escape(item['requirement_id'])}</code> "
        f"<span class='gate gate-{escape(item['requirement_class'])}'>"
        f"{escape(_requirement_class_label(item['requirement_class']))}</span></div>"
        f"<p>{escape(item['description'])}<span>{escape(item['description_zh'])}</span></p>"
        "<div class='req-meta'>"
        f"Due / 到期: <code>{escape(item['required_before_stage'])}</code> · "
        f"Resolution / 解决路径: <code>{escape(item['resolution_strategy'])}</code> · "
        f"Exploration / 探索: {'allowed / 可继续' if item['exploratory_progress_allowed'] else 'blocked / 阻塞'}"
        "</div></div>"
        for item in requirements
    )


def _page(
    *,
    title: str,
    subtitle: str,
    run_id: str,
    created_at: str,
    status: str,
    ruleset_id: str,
    metrics: list[tuple[str, str]],
    notice: str,
    body: str,
    actions: list[dict[str, Any]],
) -> str:
    metric_html = "".join(
        f"<div class='metric'><b>{escape(value)}</b><span>{escape(label)}</span></div>"
        for value, label in metrics
    )
    action_rows = "".join(
        "<tr>"
        f"<td><code>{escape(action['action_id'])}</code></td>"
        f"<td>{escape(action['question'])}<span>{escape(action.get('question_zh', ''))}</span></td>"
        f"<td>{escape(_requirement_class_label(action.get('requirement_class', 'project_action')))}</td>"
        f"<td><code>{escape(action['required_before_stage'])}</code></td>"
        f"<td>{escape(action['status'])}</td>"
        "</tr>"
        for action in actions
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>
:root {{ --ink:#17211d; --muted:#5d6963; --line:#d8dedb; --paper:#fff; --wash:#f4f7f5; --green:#16633d; --amber:#9a5b00; }}
* {{ box-sizing:border-box; }} body {{ margin:0; color:var(--ink); background:var(--wash); font-family:Arial,"Noto Sans SC","Microsoft YaHei",sans-serif; line-height:1.55; }}
header {{ background:#17352a; color:#fff; padding:32px 24px; }} header div,main {{ max-width:1180px; margin:auto; }}
h1 {{ margin:0 0 7px; font-size:29px; letter-spacing:0; }} h2 {{ margin:30px 0 11px; font-size:20px; }}
main {{ padding:22px 22px 50px; }} .meta {{ color:#ccdad3; font-size:12px; }}
.summary {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; }}
.metric {{ background:#fff; border:1px solid var(--line); border-radius:6px; padding:14px; }} .metric b {{ display:block; color:var(--green); font-size:24px; }}
.metric span,td span {{ display:block; color:var(--muted); font-size:12px; }}
.notice {{ margin:14px 0; padding:12px 14px; border-left:4px solid var(--amber); background:#fff8e8; }}
.table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:6px; background:#fff; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }} th,td {{ padding:9px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }} th {{ background:#edf2ef; white-space:nowrap; }}
.req {{ background:#fff; border:1px solid var(--line); border-radius:6px; padding:12px 14px; margin:8px 0; }} .req p {{ margin:8px 0; }} .req p span {{ display:block; color:var(--muted); }} .req-meta {{ color:var(--muted); font-size:12px; }}
.gate {{ display:inline-block; padding:2px 7px; border:1px solid var(--line); border-radius:4px; font-size:11px; font-weight:bold; }} .gate-blocking_now {{ color:#8b1e1e; background:#fff0f0; border-color:#e0b1b1; }} .gate-design_variable {{ color:#155a77; background:#eef8fc; border-color:#b5d7e4; }} .gate-required_before_ranking {{ color:#7b4c00; background:#fff8e8; border-color:#e4ca8c; }} .gate-required_before_release {{ color:#54426f; background:#f6f1fb; border-color:#cabbe0; }} code {{ font-size:12px; }}
footer {{ margin-top:30px; color:var(--muted); font-size:12px; }}
@media(max-width:760px) {{ .summary {{ grid-template-columns:1fr 1fr; }} main {{ padding:16px 11px 38px; }} header {{ padding:25px 15px; }} }}
</style></head><body><header><div><h1>{escape(title)}</h1><p>{escape(subtitle)}</p>
<p class="meta">Run {escape(run_id)} · {escape(created_at)} · status={escape(status)} · {escape(ruleset_id)}</p></div></header>
<main><section class="summary">{metric_html}</section><div class="notice">{escape(notice)}</div>{body}
<h2>Human actions / 人工事项</h2><div class="table-wrap"><table><thead><tr><th>Action</th><th>Question / 问题</th><th>Gate / 门禁</th><th>Due / 到期</th><th>Status</th></tr></thead><tbody>{action_rows}</tbody></table></div>
<footer>Generated by versioned deterministic code. Missing evidence remains not_evaluated; no LLM-authored scientific conclusion is embedded. / 由版本化确定性代码生成；缺失证据保持未评估，不嵌入 LLM 编写的科学结论。</footer>
</main></body></html>"""


def render_immune_report(
    result: dict[str, Any],
    actions: list[dict[str, Any]],
    run_id: str,
    created_at: str,
) -> str:
    rows = []
    for candidate in result["candidates"]:
        categories = candidate["categories"]
        conservation = categories["pathogen_conservation"]
        rows.append(
            "<tr>"
            f"<td><b>{escape(candidate['candidate_key'])}</b><span>{escape(candidate['candidate_id'])}</span></td>"
            f"<td>{escape(candidate['status'])}</td>"
            f"<td>{_number(categories['surface_accessibility_proxy']['exposed_fraction'])}</td>"
            f"<td>{_number(conservation['evaluated_residue_fraction'])}</td>"
            f"<td>{_number(conservation['mean_conservation_fraction'])}</td>"
            f"<td>{sum(category['status'] == 'evaluated' for category in categories.values())}/{len(categories)}</td>"
            "</tr>"
        )
    requirements = _requirement_cards(result["requirements"])
    body = (
        "<h2>Candidate evidence / 候选证据</h2><div class='table-wrap'><table><thead><tr>"
        "<th>Candidate / 候选</th><th>Status</th><th>Surface proxy exposed fraction / 表面代理</th>"
        "<th>Conservation coverage / 保守性覆盖</th><th>Mean conservation / 平均保守性</th><th>Categories / 类别</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        f"<h2>Missing requirements / 缺失要求</h2>{requirements}"
    )
    return _page(
        title="Stage 4 · Immune evidence assessment",
        subtitle="免疫证据评估 · partial evidence remains explicit",
        run_id=run_id,
        created_at=created_at,
        status=result["status"],
        ruleset_id=result["ruleset_id"],
        metrics=[
            (str(len(result["candidates"])), "Candidates / 候选"),
            (str(len(result["alignment_profiles"])), "Alignments / 对齐"),
            (
                str(sum(state["status"] == "evaluated" for state in result["adapter_states"].values())),
                "External adapters / 外部适配器",
            ),
            (str(len(result["requirements"])), "Missing requirements / 缺失要求"),
        ],
        notice="Computational evidence only. needs_data records incomplete evidence; it does not by itself block exploratory continuation. Each requirement below declares its own deadline. / 仅为计算证据。needs_data 表示证据不完整，并不自动阻塞探索性推进；每条要求在下方声明自己的到期节点。",
        body=body,
        actions=actions,
    )


def render_developability_report(
    result: dict[str, Any],
    actions: list[dict[str, Any]],
    run_id: str,
    created_at: str,
) -> str:
    rows = []
    for candidate in result["candidates"]:
        descriptors = candidate["descriptors"]
        rows.append(
            "<tr>"
            f"<td><b>{escape(candidate['candidate_key'])}</b><span>{escape(candidate['candidate_id'])}</span></td>"
            f"<td>{_number(descriptors['gravy'])}</td>"
            f"<td>{_number(descriptors['charge_proxy'], 2)}</td>"
            f"<td>{descriptors['hydrophobic_region_count']}</td>"
            f"<td>{descriptors['low_complexity_region_count']}</td>"
            f"<td>{descriptors['n_linked_glycosylation_sequon_count']}</td>"
            f"<td>{candidate['review_liability_count']}</td>"
            "</tr>"
        )
    requirements = _requirement_cards(result["requirements"])
    body = (
        "<h2>Intrinsic descriptors / 内在描述符</h2><div class='table-wrap'><table><thead><tr>"
        "<th>Candidate / 候选</th><th>GRAVY</th><th>Charge proxy / 电荷代理</th>"
        "<th>Hydrophobic / 疏水</th><th>Low complexity / 低复杂度</th><th>NXS/T</th><th>Review / 复核</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        f"<h2>Missing requirements / 缺失要求</h2>{requirements}"
    )
    return _page(
        title="Stage 5 · Developability assessment",
        subtitle="可开发性评估 · intrinsic descriptors and explicit gaps",
        run_id=run_id,
        created_at=created_at,
        status=result["status"],
        ruleset_id=result["ruleset_id"],
        metrics=[
            (str(len(result["candidates"])), "Candidates / 候选"),
            (
                str(sum(candidate["review_liability_count"] for candidate in result["candidates"])),
                "Review liabilities / 复核项",
            ),
            (
                str(sum(state["status"] == "evaluated" for state in result["adapter_states"].values())),
                "External adapters / 外部适配器",
            ),
            (str(len(result["requirements"])), "Missing requirements / 缺失要求"),
        ],
        notice="Intrinsic rules are descriptors, not calibrated manufacturing predictions. needs_data does not equal blocked: design variables and later gates may proceed as explicit assumptions. / 内在规则只是描述符，不是校准后的制造预测。needs_data 不等于流程阻塞：设计变量和后续门禁可以作为显式假设继续推进。",
        body=body,
        actions=actions,
    )
