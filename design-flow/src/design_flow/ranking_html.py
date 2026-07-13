"""Bilingual deterministic Stage 7 ranking report."""

from __future__ import annotations

from html import escape
from typing import Any


def _number(value: Any) -> str:
    return f"{float(value):.4f}" if isinstance(value, (int, float)) else "-"


def render_ranking_report(
    result: dict[str, Any],
    actions: list[dict[str, Any]],
    run_id: str,
    created_at: str,
) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(row['modality'])}</td>"
        f"<td>{row['rank'] if row['rank'] is not None else '-'}</td>"
        f"<td><b>{escape(row['candidate_key'])}</b><span>{escape(row['candidate_id'])}</span></td>"
        f"<td>{_number(row['score'])}</td><td>{_number(row['evidence_coverage'])}</td>"
        f"<td>{'eligible / 可排序' if row['eligible'] else 'excluded / 排除'}</td>"
        f"<td>{escape('; '.join(row['exclusion_reasons']) or '-')}</td>"
        "</tr>"
        for row in result["rankings"]
    )
    portfolio_rows = "".join(
        "<tr>"
        f"<td>{escape(modality)}</td><td>{item['rank']}</td>"
        f"<td><b>{escape(item['candidate_key'])}</b><span>{escape(item['candidate_id'])}</span></td>"
        f"<td>{_number(item['score'])}</td><td>{escape(item['selection_reason'])}</td>"
        "</tr>"
        for modality, items in result["provisional_portfolios"].items()
        for item in items
    )
    feature_rows = "".join(
        "<tr>"
        f"<td><code>{escape(feature['feature_id'])}</code></td><td>{escape(feature['direction'])}</td>"
        f"<td>{_number(feature['weight'])}</td><td>{'yes / 是' if feature['required'] else 'no / 否'}</td>"
        f"<td>{escape(', '.join(feature['modalities']))}</td><td>{escape(feature['source'])}</td>"
        "</tr>"
        for feature in result["feature_policy"]
    )
    requirements = "".join(
        f"<div class='req'><code>{escape(item['requirement_id'])}</code><br>{escape(item['description'])}</div>"
        for item in result["requirements"]
    ) or "<div class='req'>No missing requirement / 无缺失要求</div>"
    action_rows = "".join(
        "<tr>"
        f"<td><code>{escape(action['action_id'])}</code></td>"
        f"<td>{escape(action['question'])}<span>{escape(action.get('question_zh', ''))}</span></td>"
        f"<td>{escape(action['status'])}</td>"
        "</tr>"
        for action in actions
    )
    eligible = sum(row["eligible"] for row in result["rankings"])
    selected = sum(len(items) for items in result["provisional_portfolios"].values())
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stage 7 · Integrated ranking</title><style>
:root{{--ink:#17211d;--muted:#5d6963;--line:#d8dedb;--wash:#f4f7f5;--green:#16633d;--amber:#9a5b00}}*{{box-sizing:border-box}}body{{margin:0;color:var(--ink);background:var(--wash);font-family:Arial,"Noto Sans SC","Microsoft YaHei",sans-serif;line-height:1.55}}
header{{background:#17352a;color:#fff;padding:32px 24px}}header div,main{{max-width:1220px;margin:auto}}h1{{margin:0 0 7px;font-size:29px;letter-spacing:0}}h2{{margin:30px 0 11px;font-size:20px}}main{{padding:22px 22px 50px}}.meta{{color:#ccdad3;font-size:12px}}
.summary{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}.metric{{background:#fff;border:1px solid var(--line);border-radius:6px;padding:14px}}.metric b{{display:block;color:var(--green);font-size:24px}}.metric span,td span{{display:block;color:var(--muted);font-size:12px}}
.notice{{margin:14px 0;padding:12px 14px;border-left:4px solid var(--amber);background:#fff8e8}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:6px;background:#fff}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#edf2ef;white-space:nowrap}}.req{{background:#fff;border:1px solid var(--line);border-radius:6px;padding:12px 14px;margin:8px 0}}code{{font-size:12px}}footer{{margin-top:30px;color:var(--muted);font-size:12px}}@media(max-width:760px){{.summary{{grid-template-columns:1fr 1fr}}main{{padding:16px 11px 38px}}header{{padding:25px 15px}}}}
</style></head><body><header><div><h1>Stage 7 · Integrated ranking</h1><p>综合排序与组合选择 · transparent technical prioritization with explicit missing evidence</p><p class="meta">Run {escape(run_id)} · {escape(created_at)} · status={escape(result['status'])} · {escape(result['ruleset_id'])}</p></div></header><main>
<section class="summary"><div class="metric"><b>{len(result['rankings'])}</b><span>Modality rows / 模态记录</span></div><div class="metric"><b>{eligible}</b><span>Eligible / 可排序</span></div><div class="metric"><b>{selected}</b><span>Provisional portfolio / 临时组合</span></div><div class="metric"><b>{len(result['requirements'])}</b><span>Missing requirements / 缺失要求</span></div></section>
<div class="notice">This is not an efficacy rank or experimental release. Zero-weight evidence is displayed but cannot change the score. Missing positive-weight evidence is penalized. / 这不是有效性排名或实验放行；零权重证据仅展示，缺失的正权重证据会受到惩罚。</div>
<h2>Ranking / 排序</h2><div class="table-wrap"><table><thead><tr><th>Modality / 模态</th><th>Rank</th><th>Candidate / 候选</th><th>Score</th><th>Coverage / 覆盖</th><th>Status</th><th>Reasons / 原因</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2>Provisional portfolio / 临时候选组合</h2><div class="table-wrap"><table><thead><tr><th>Modality</th><th>Rank</th><th>Candidate</th><th>Score</th><th>Selection reason / 选择原因</th></tr></thead><tbody>{portfolio_rows}</tbody></table></div>
<h2>Frozen feature policy / 固化特征策略</h2><div class="table-wrap"><table><thead><tr><th>Feature</th><th>Direction</th><th>Weight</th><th>Required</th><th>Modalities</th><th>Source</th></tr></thead><tbody>{feature_rows}</tbody></table></div>
<h2>Missing requirements / 缺失要求</h2>{requirements}
<h2>Human actions / 人工事项</h2><div class="table-wrap"><table><thead><tr><th>Action</th><th>Question / 问题</th><th>Status</th></tr></thead><tbody>{action_rows}</tbody></table></div>
<footer>Generated by versioned deterministic code. Component scores, exclusions, and sensitivity are machine-reproducible; no LLM-authored ranking conclusion is embedded. / 由版本化确定性代码生成；分项得分、排除原因和敏感性可由机器重算，不嵌入 LLM 排名结论。</footer></main></body></html>"""
