"""Self-contained, human-readable HTML report for a workflow node."""

from __future__ import annotations

from html import escape
from typing import Any

from .domain import ProjectAnalysis
from .workflow import CURRENT_STAGE_ID, STAGE_BY_ID


CHECK_LABELS = {
    "design-round-contract-valid": ("设计轮次合同结构有效", "Design-round contract is valid"),
    "design-round-contract-approved": ("设计轮次合同已获执行批准", "Design-round contract is approved for execution"),
    "source-files-hashed": ("输入文件已建立哈希身份", "Source files have cryptographic identities"),
    "expected-record-count": ("记录数量符合预期", "Record count matches expectation"),
    "one-to-one-id-pairing": ("蛋白与 CDS 一一配对", "Protein and CDS IDs pair one-to-one"),
    "sequence-alphabet-frame-stop": ("字符、阅读框与终止密码子检查", "Alphabet, frame, and stop checks"),
    "translation-equivalence": ("CDS 翻译与氨基酸序列一致", "CDS translation matches the protein"),
    "candidate-identities-present": ("候选序列具有稳定 ID", "Candidates have stable identities"),
    "accepted-candidates-have-exact-translation": ("通过候选保持精确翻译一致", "Accepted candidates translate exactly"),
    "findings-exported": ("审计发现已完整导出", "Audit findings are exported"),
}

OPERATION_LABELS = {
    "freeze_design_round": ("冻结设计轮次", "Freeze design round", "在生成候选前冻结目标、变量、反馈和缺失证据规则。"),
    "parse_fasta": ("读取 FASTA", "Parse FASTA", "识别多行记录、ID、空记录和重复记录。"),
    "normalize_sequences": ("规范化序列", "Normalize sequences", "统一大小写；RNA 的 U 转换为 T 时保留明确警告。"),
    "translate_cds": ("翻译 CDS", "Translate CDS", "使用标准遗传密码表检查阅读框、起始和终止。"),
    "compare_translation": ("逐残基比对", "Compare translation", "将 CDS 翻译结果与提供的蛋白序列逐位比较。"),
    "calculate_descriptors": ("计算基础指标", "Calculate descriptors", "计算长度、分子量估计、组成、GC、熵和连续重复。"),
    "assign_candidate_identity": ("冻结候选身份", "Freeze candidate identity", "根据标准化 ID、蛋白序列和 CDS 生成稳定候选 ID。"),
}

STATUS_ZH = {
    "pass": "通过",
    "complete": "完成",
    "ready": "可交接",
    "resolved": "已确认",
    "waived": "已豁免",
    "needs_human_input": "需要人工补充",
    "open": "待处理",
    "warning": "警告",
    "fail": "失败",
    "blocked": "阻断",
    "error": "错误",
}

INPUT_LABELS = {
    "project_config": ("项目配置", "Project configuration"),
    "amino_acid_fasta": ("氨基酸 FASTA", "Amino-acid FASTA"),
    "nucleotide_fasta": ("核苷酸 FASTA", "Nucleotide FASTA"),
}

STAGE_NAMES_ZH = {
    "program_and_source_intake": "项目定义与源序列接入",
    "candidate_specification": "候选规格与生成",
}

MODALITY_LABELS_ZH = {
    "recombinant_protein": "重组蛋白疫苗",
    "mrna": "mRNA 疫苗",
}


def _e(value: Any) -> str:
    return escape(str(value), quote=True)


def _status_class(status: str) -> str:
    normalized = status.lower()
    if normalized in {"pass", "complete", "ready", "resolved"}:
        return "status-good"
    if normalized in {"needs_human_input", "open", "warning", "ready_with_open_actions"}:
        return "status-warn"
    if normalized in {"fail", "blocked", "error"}:
        return "status-bad"
    return "status-muted"


def _badge(status: str) -> str:
    label = status.replace("_", " ").upper()
    label_zh = STATUS_ZH.get(status.lower(), status)
    return (
        f'<span class="status {_status_class(status)}">'
        f'<span>{_e(label_zh)}</span><span class="status-en">{_e(label)}</span></span>'
    )


def _check_list(checks: list[dict[str, Any]]) -> str:
    items = []
    for check in checks:
        check_id = str(check["check_id"])
        label_zh, label_en = CHECK_LABELS.get(check_id, (check_id, check_id))
        evidence = check.get("evidence", "")
        evidence_html = f'<p class="evidence">{_e(evidence)}</p>' if evidence else ""
        items.append(
            '<li class="audit-item">'
            f'<div><strong>{_e(label_zh)}</strong><span class="english">{_e(label_en)}</span>'
            f'<code>{_e(check_id)}</code>{evidence_html}</div>'
            f'{_badge(str(check["status"]))}'
            "</li>"
        )
    return "".join(items)


def _input_cards(inputs: dict[str, dict[str, str]]) -> str:
    cards = []
    for name, record in inputs.items():
        label_zh, label_en = INPUT_LABELS.get(name, (name, name))
        cards.append(
            '<article class="io-item">'
            f'<span class="io-kicker">{_e(label_zh)}</span>'
            f'<span class="english">{_e(label_en)}</span>'
            f'<strong>{_e(record["path"].split("/")[-1])}</strong>'
            f'<code class="path">{_e(record["path"])}</code>'
            f'<span class="hash">SHA-256 {_e(record["sha256"][:16])}&hellip;</span>'
            "</article>"
        )
    return "".join(cards)


def _action_cards(
    actions: list[dict[str, Any]],
    empty_message: str,
    empty_message_en: str,
) -> str:
    if not actions:
        return (
            f'<p class="empty-state">{_e(empty_message)}'
            f'<span class="english">{_e(empty_message_en)}</span></p>'
        )
    cards = []
    for action in actions:
        resolution = ""
        if action.get("resolution"):
            resolution_zh = action.get("resolution_zh") or action["resolution"]
            resolution = (
                '<div class="resolution"><strong>已记录结论 / Recorded decision</strong>'
                f'<p>{_e(resolution_zh)}</p>'
                f'<span class="english">{_e(action["resolution"])}</span></div>'
            )
        question_zh = action.get("question_zh") or action["question"]
        cards.append(
            '<article class="action-card">'
            '<div class="action-top">'
            f'<code>{_e(action["action_id"])}</code>'
            f'{_badge(str(action["status"]))}'
            "</div>"
            f'<h4>{_e(question_zh)}<span class="english">{_e(action["question"])}</span></h4>'
            '<dl class="action-meta">'
            f'<div><dt>负责人 / Owner</dt><dd>{_e(action["owner"])}</dd></div>'
            f'<div><dt>最迟完成节点 / Required before</dt><dd>{_e(action["required_before_stage"])}</dd></div>'
            "</dl>"
            f"{resolution}"
            "</article>"
        )
    return "".join(cards)


def _conclusions(analysis: ProjectAnalysis, bundle: dict[str, Any]) -> str:
    accepted = bundle["summary"]["accepted_candidates"]
    total = len(analysis.proteins)
    warnings = bundle["summary"]["warnings"]
    due = bundle["summary"]["due_human_actions"]
    conclusions = [
        (
            "positive",
            "源序列一致性通过",
            "Source sequence consistency passed",
            f"{accepted}/{total} 组蛋白与 CDS 通过精确翻译一致性检查。",
            f"{accepted}/{total} protein/CDS pairs passed exact translation equivalence.",
        )
    ]
    if warnings:
        conclusions.append(("caution", "存在警告", "Warnings recorded", f"本节点记录了 {warnings} 个非阻断警告。", f"This node recorded {warnings} non-blocking warnings."))
    else:
        conclusions.append(("positive", "没有序列警告", "No sequence warnings", "本次输入没有产生序列级错误或警告。", "The current inputs produced no sequence-level errors or warnings."))
    if due:
        conclusions.append(
            (
                "caution",
                "尚未释放到下一节点",
                "Not released to the next node",
                f"进入候选设计前仍有 {due} 个人工决策需要完成。",
                f"{due} human decisions remain before candidate specification can begin.",
            )
        )
    else:
        conclusions.append(("positive", "可以交接", "Ready for handoff", "当前节点已满足进入候选设计的条件。", "The current node meets the conditions for candidate specification."))
    conclusions.append(
        (
            "scope",
            "结论边界",
            "Scope of conclusion",
            "这些结果只证明输入身份和 AA/CDS 一致性，不代表免疫原性、安全性、表达、折叠或保护效果。",
            "These results establish input identity and AA/CDS consistency only; they do not establish immunogenicity, safety, expression, folding, or protection.",
        )
    )
    return "".join(
        '<article class="conclusion {kind}"><h3>{title}<span class="english">{title_en}</span></h3>'
        '<p>{body}</p><span class="english">{body_en}</span></article>'.format(
            kind=_e(kind),
            title=_e(title),
            title_en=_e(title_en),
            body=_e(body),
            body_en=_e(body_en),
        )
        for kind, title, title_en, body, body_en in conclusions
    )


def render_node_report(
    analysis: ProjectAnalysis,
    bundle: dict[str, Any],
    run_id: str,
    created_at: str,
) -> str:
    stage = STAGE_BY_ID[CURRENT_STAGE_ID]
    summary = bundle["summary"]
    input_audit = bundle["input_audit"]
    process_record = bundle["process_record"]
    output_audit = bundle["output_audit"]
    actions = bundle["human_actions"]["actions"]
    handoff = bundle["handoff"]
    blocking_ids = set(handoff["blocking_action_ids"])
    blocking_actions = [action for action in actions if action["action_id"] in blocking_ids]
    future_actions = [
        action
        for action in actions
        if action["status"] == "open" and action["action_id"] not in blocking_ids
    ]
    resolved_actions = [action for action in actions if action["status"] in {"resolved", "waived"}]

    input_items = _input_cards(input_audit["inputs"])
    process_parts = []
    for index, operation in enumerate(process_record["operations"], start=1):
        label_zh, label_en, summary_zh = OPERATION_LABELS.get(
            operation["operation"],
            (operation["operation"], operation["operation"], operation["behavior"]),
        )
        process_parts.append(
            '<li><span class="step-index">{index}</span><div>'
            '<strong>{label_zh}<span class="english">{label_en}</span></strong>'
            '<p>{summary_zh}</p><span class="english">{summary_en}</span></div></li>'.format(
                index=index,
                label_zh=_e(label_zh),
                label_en=_e(label_en),
                summary_zh=_e(summary_zh),
                summary_en=_e(operation["behavior"]),
            )
        )
    process_items = "".join(process_parts)
    candidate_rows = "".join(
        "<tr>"
        f'<td><strong>{_e(protein.protein_id)}</strong></td>'
        f'<td><code>{_e(protein.candidate_id)}</code></td>'
        f'<td>{_badge(protein.status)}</td>'
        f'<td>{_e(protein.metrics["aa_length"])}</td>'
        f'<td>{_e(protein.metrics["cds_length_nt"])}</td>'
        f'<td>{"一致" if protein.metrics["translation_matches"] is True else "不一致"}'
        f'<span class="english">{"Match" if protein.metrics["translation_matches"] is True else "Mismatch"}</span></td>'
        f'<td>{float(protein.metrics["gc_fraction"]):.1%}</td>'
        f'<td>{float(protein.metrics["estimated_molecular_weight_da"]):,.1f}</td>'
        "</tr>"
        for protein in analysis.proteins
    )
    next_stage_name = STAGE_BY_ID[handoff["to_stage"]].name
    next_stage_name_zh = STAGE_NAMES_ZH.get(handoff["to_stage"], next_stage_name)
    target_zh = (
        "预防 LSDV 引起的牛结节性皮肤病"
        if "LSDV" in analysis.config.target_indication
        else analysis.config.target_indication
    )
    host_zh = (
        "牛（Bos taurus）"
        if "cattle" in analysis.config.intended_host_species.lower()
        else analysis.config.intended_host_species
    )
    modalities_en = " + ".join(analysis.config.product_modalities)
    modalities_zh = " + ".join(
        MODALITY_LABELS_ZH.get(modality, modality)
        for modality in analysis.config.product_modalities
    )
    design_summary = analysis.design_dossier.summary()
    objective_rows = "".join(
        "<tr>"
        f"<td><code>{_e(item['objective_id'])}</code></td>"
        f"<td>{_e(item['decision_role'])}</td>"
        f"<td>{_e(item['direction'])}</td>"
        f"<td><code>{_e(item['metric'])}</code></td>"
        f"<td>{_e(item['evidence_stage'])}</td>"
        "</tr>"
        for item in analysis.design_dossier.objective_policy["objectives"]
    )
    variable_rows = "".join(
        "<tr>"
        f"<td><code>{_e(item['variable_id'])}</code></td>"
        f"<td>{_e(item['scope'])}</td>"
        f"<td>{_badge(item['status'])}</td>"
        f"<td>{_e(item['introduced_at_stage'])}</td>"
        f"<td>{_e(item['description'])}</td>"
        "</tr>"
        for item in analysis.design_dossier.variable_registry["variables"]
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_e(stage.name)} | {_e(analysis.config.project_id)}</title>
  <style>
    :root {{
      --page: #f5f7f6;
      --surface: #ffffff;
      --ink: #17201d;
      --muted: #60706a;
      --line: #d8dfdc;
      --green: #12634f;
      --green-soft: #e4f4ee;
      --amber: #9a5b00;
      --amber-soft: #fff1d6;
      --red: #a13030;
      --red-soft: #fde8e7;
      --blue: #285b91;
      --blue-soft: #e8f1fa;
      --charcoal: #21302b;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--page);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 15px;
      line-height: 1.6;
      letter-spacing: 0;
    }}
    a {{ color: var(--green); }}
    code {{
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 0.88em;
      overflow-wrap: anywhere;
    }}
    .topbar {{ background: var(--charcoal); color: #fff; }}
    .topbar-inner {{
      width: min(1180px, calc(100% - 40px));
      margin: 0 auto;
      min-height: 52px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
    }}
    .brand {{ font-weight: 700; }}
    .run-id {{ color: #c9d5d0; font-size: 13px; overflow-wrap: anywhere; }}
    main {{ width: min(1180px, calc(100% - 40px)); margin: 0 auto; padding: 34px 0 72px; }}
    .node-header {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 28px;
      align-items: end;
      padding-bottom: 28px;
      border-bottom: 1px solid var(--line);
    }}
    .eyebrow {{ color: var(--green); font-size: 12px; font-weight: 800; text-transform: uppercase; }}
    h1 {{ margin: 7px 0 8px; font-size: 32px; line-height: 1.2; letter-spacing: 0; }}
    .title-en {{ margin: -2px 0 10px; color: var(--muted); font-size: 17px; font-weight: 600; }}
    .subtitle {{ margin: 0; color: var(--muted); max-width: 780px; }}
    .english {{ display: block; margin-top: 3px; color: var(--muted); font-size: 0.84em; font-weight: 400; line-height: 1.45; }}
    .header-status {{ text-align: right; }}
    .header-status p {{ margin: 8px 0 0; color: var(--muted); font-size: 13px; }}
    .status {{
      display: inline-flex;
      flex-direction: column;
      align-items: flex-start;
      min-height: 25px;
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 800;
      line-height: 1.2;
      white-space: nowrap;
    }}
    .status-en {{ margin-top: 1px; font-size: 9px; font-weight: 700; opacity: 0.8; }}
    .status-good {{ color: var(--green); background: var(--green-soft); }}
    .status-warn {{ color: var(--amber); background: var(--amber-soft); }}
    .status-bad {{ color: var(--red); background: var(--red-soft); }}
    .status-muted {{ color: var(--muted); background: #edf0ef; }}
    .context-strip {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      border-bottom: 1px solid var(--line);
      background: var(--surface);
    }}
    .context-item {{ padding: 18px; border-right: 1px solid var(--line); min-width: 0; }}
    .context-item:last-child {{ border-right: 0; }}
    .context-item span {{ display: block; color: var(--muted); font-size: 12px; }}
    .context-item strong {{ display: block; margin-top: 3px; overflow-wrap: anywhere; }}
    .context-item .english {{ margin-top: 4px; }}
    section {{ padding: 34px 0; border-bottom: 1px solid var(--line); }}
    .section-heading {{ display: flex; align-items: end; justify-content: space-between; gap: 24px; margin-bottom: 20px; }}
    .section-heading h2 {{ margin: 0; font-size: 21px; letter-spacing: 0; }}
    .section-heading p {{ margin: 0; color: var(--muted); max-width: 690px; }}
    .conclusion-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .conclusion {{ min-height: 145px; padding: 18px; border: 1px solid var(--line); border-top-width: 4px; border-radius: 6px; background: var(--surface); }}
    .conclusion h3 {{ margin: 0 0 8px; font-size: 15px; }}
    .conclusion p {{ margin: 0; color: var(--muted); }}
    .conclusion > .english {{ margin-top: 8px; }}
    .conclusion.positive {{ border-top-color: var(--green); }}
    .conclusion.caution {{ border-top-color: var(--amber); }}
    .conclusion.scope {{ border-top-color: var(--blue); }}
    .flow {{ display: grid; grid-template-columns: minmax(0, 1fr) 42px minmax(0, 1fr) 42px minmax(0, 1fr); align-items: stretch; }}
    .flow-column {{ min-width: 0; background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 18px; }}
    .flow-column h3 {{ margin: 0 0 14px; font-size: 16px; }}
    .flow-arrow {{ display: grid; place-items: center; color: var(--green); font-size: 24px; font-weight: 700; }}
    .io-stack {{ display: grid; gap: 10px; }}
    .io-item {{ padding: 12px; border-left: 3px solid var(--blue); background: var(--blue-soft); min-width: 0; }}
    .io-kicker {{ display: block; color: var(--blue); font-size: 10px; font-weight: 800; }}
    .io-item strong, .io-item code, .io-item span {{ overflow-wrap: anywhere; }}
    .io-item .path {{ display: block; margin-top: 5px; color: var(--muted); }}
    .io-item .hash {{ display: block; margin-top: 4px; color: var(--muted); font-size: 11px; }}
    .process-list {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }}
    .process-list li {{ display: grid; grid-template-columns: 27px minmax(0, 1fr); gap: 10px; }}
    .step-index {{ display: grid; place-items: center; width: 27px; height: 27px; color: #fff; background: var(--green); border-radius: 50%; font-size: 11px; font-weight: 800; }}
    .process-list strong {{ display: block; }}
    .process-list p {{ margin: 2px 0 0; color: var(--muted); font-size: 13px; }}
    .process-list .english {{ margin-top: 2px; }}
    .output-list {{ margin: 0; padding-left: 19px; }}
    .output-list li {{ margin: 7px 0; }}
    .audit-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }}
    .audit-panel {{ min-width: 0; }}
    .audit-panel h3 {{ margin: 0 0 10px; font-size: 15px; }}
    .audit-list {{ list-style: none; padding: 0; margin: 0; border-top: 1px solid var(--line); }}
    .audit-item {{ display: flex; align-items: start; justify-content: space-between; gap: 15px; padding: 13px 0; border-bottom: 1px solid var(--line); }}
    .audit-item strong {{ display: block; }}
    .audit-item code {{ display: block; margin-top: 2px; color: var(--muted); }}
    .evidence {{ margin: 5px 0 0; color: var(--muted); font-size: 13px; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); }}
    table {{ width: 100%; border-collapse: collapse; min-width: 790px; }}
    th, td {{ padding: 12px 14px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: middle; }}
    th {{ color: var(--muted); background: #f0f3f2; font-size: 12px; font-weight: 800; }}
    tbody tr:last-child td {{ border-bottom: 0; }}
    .action-group + .action-group {{ margin-top: 30px; }}
    .action-group h3 {{ margin: 0 0 5px; font-size: 16px; }}
    .action-group > p {{ margin: 0 0 13px; color: var(--muted); }}
    .action-card {{ padding: 16px 0; border-top: 1px solid var(--line); }}
    .action-card:last-child {{ border-bottom: 1px solid var(--line); }}
    .action-top {{ display: flex; justify-content: space-between; align-items: center; gap: 16px; }}
    .action-card h4 {{ margin: 8px 0 10px; font-size: 15px; }}
    .action-meta {{ display: flex; flex-wrap: wrap; gap: 20px; margin: 0; }}
    .action-meta div {{ display: flex; gap: 7px; }}
    .action-meta dt {{ color: var(--muted); }}
    .action-meta dd {{ margin: 0; font-weight: 600; }}
    .resolution {{ margin-top: 11px; padding: 12px; border-left: 3px solid var(--green); background: var(--green-soft); }}
    .resolution p {{ margin: 3px 0 0; }}
    .empty-state {{ margin: 0; padding: 14px; color: var(--muted); border: 1px dashed var(--line); }}
    .handoff {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 30px; align-items: center; padding: 22px; border: 1px solid var(--line); border-left: 5px solid var(--amber); background: var(--surface); }}
    .handoff h3 {{ margin: 0 0 6px; }}
    .handoff p {{ margin: 0; color: var(--muted); }}
    .footer {{ padding-top: 28px; color: var(--muted); font-size: 12px; }}
    @media (max-width: 900px) {{
      .context-strip, .conclusion-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .context-item:nth-child(2) {{ border-right: 0; }}
      .context-item:nth-child(-n+2) {{ border-bottom: 1px solid var(--line); }}
      .flow {{ grid-template-columns: 1fr; gap: 10px; }}
      .flow-arrow {{ transform: rotate(90deg); min-height: 28px; }}
      .audit-grid {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 620px) {{
      .topbar-inner, main {{ width: min(100% - 24px, 1180px); }}
      .topbar-inner {{ align-items: start; flex-direction: column; justify-content: center; gap: 0; padding: 8px 0; }}
      .node-header {{ grid-template-columns: 1fr; align-items: start; }}
      .header-status {{ text-align: left; }}
      h1 {{ font-size: 25px; }}
      .context-strip, .conclusion-grid {{ grid-template-columns: 1fr; }}
      .context-item {{ border-right: 0; border-bottom: 1px solid var(--line); }}
      .section-heading {{ display: block; }}
      .section-heading p {{ margin-top: 7px; }}
      .handoff {{ grid-template-columns: 1fr; }}
      .action-top {{ align-items: start; flex-direction: column; gap: 7px; }}
    }}
    @media print {{
      body {{ background: #fff; }}
      .topbar {{ background: #fff; color: #000; border-bottom: 1px solid #aaa; }}
      .run-id {{ color: #333; }}
      main {{ width: 100%; padding: 15px; }}
      section {{ break-inside: avoid; }}
    }}
  </style>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand">疫苗设计流程 / Vaccine Design Flow</div>
      <div class="run-id">Run {_e(run_id)}</div>
    </div>
  </header>
  <main>
    <header class="node-header">
      <div>
        <div class="eyebrow">节点 1 / NODE 1 &middot; 项目定义与源序列接入 / PROGRAM AND SOURCE INTAKE</div>
        <h1>LSDV 牛疫苗设计：源序列审计</h1>
        <p class="title-en">LSDV cattle vaccine design: source sequence audit</p>
        <p class="subtitle">本节点确认项目上下文、原始蛋白与 CDS 的身份和一致性，并把需要人工确认的问题交给下一节点。</p>
        <span class="english">This node verifies program context and the identity and consistency of source proteins and CDS records, then carries unresolved human decisions into the next node.</span>
      </div>
      <div class="header-status">
        {_badge(summary["status"])}
        <p>生成于 / Generated {_e(created_at)}</p>
      </div>
    </header>

    <div class="context-strip">
      <div class="context-item"><span>目标 / Target</span><strong>{_e(target_zh)}</strong><span class="english">{_e(analysis.config.target_indication)}</span></div>
      <div class="context-item"><span>目标宿主 / Intended host</span><strong>{_e(host_zh)}</strong><span class="english">{_e(analysis.config.intended_host_species)}</span></div>
      <div class="context-item"><span>产品路线 / Modalities</span><strong>{_e(modalities_zh)}</strong><span class="english">{_e(modalities_en)}</span></div>
      <div class="context-item"><span>下一节点 / Next node</span><strong>{_e(next_stage_name_zh)}</strong><span class="english">{_e(next_stage_name)}</span></div>
    </div>

    <section>
      <div class="section-heading">
        <h2>当前结论 / Conclusions</h2>
        <p>先看结论，再决定是否需要展开技术细节。<span class="english">Read the conclusions first, then expand the technical evidence as needed.</span></p>
      </div>
      <div class="conclusion-grid">{_conclusions(analysis, bundle)}</div>
    </section>

    <section>
      <div class="section-heading">
        <h2>输入如何变成输出 / Input to output</h2>
        <p>原始文件经过可追踪处理，生成冻结候选和下一节点交接包。<span class="english">Traceable processing converts source files into frozen candidates and a next-node handoff package.</span></p>
      </div>
      <div class="flow">
        <div class="flow-column">
          <h3>输入 / Inputs</h3>
          <div class="io-stack">{input_items}</div>
        </div>
        <div class="flow-arrow" aria-hidden="true">&rarr;</div>
        <div class="flow-column">
          <h3>处理 / Processing</h3>
          <ol class="process-list">{process_items}</ol>
        </div>
        <div class="flow-arrow" aria-hidden="true">&rarr;</div>
        <div class="flow-column">
          <h3>输出 / Outputs</h3>
          <ul class="output-list">
            <li>{_e(output_audit["summary"]["accepted_candidates"])} 个通过审计的原始候选<span class="english">{_e(output_audit["summary"]["accepted_candidates"])} audited source candidates</span></li>
            <li>稳定候选 ID 与完整输入哈希<span class="english">Stable candidate IDs and complete input hashes</span></li>
            <li>输入、过程和输出审计记录<span class="english">Input, process, and output audit records</span></li>
            <li>人工事项与下一节点 handoff<span class="english">Human actions and next-node handoff</span></li>
          </ul>
        </div>
      </div>
    </section>

    <section>
      <div class="section-heading">
        <h2>本轮设计合同 / Design-round contract</h2>
        <p>候选生成之前先冻结目标与可搜索空间；后续模型只能提供证据或下一轮建议。<span class="english">Objectives and the searchable space are frozen before proposal generation; downstream models may only add evidence or next-round requests.</span></p>
      </div>
      <div class="context-strip">
        <div class="context-item"><span>设计轮次 / Round</span><strong>{_e(design_summary['round_id'])}</strong><span class="english">index {_e(design_summary['round_index'])}</span></div>
        <div class="context-item"><span>目标 / Objectives</span><strong>{_e(design_summary['objective_count'])}</strong><span class="english">versioned objective records</span></div>
        <div class="context-item"><span>可搜索变量 / Searchable</span><strong>{_e(design_summary['searchable_variable_count'])}</strong><span class="english">of {_e(design_summary['variable_count'])} variables</span></div>
        <div class="context-item"><span>历史反馈 / Prior feedback</span><strong>{_e(design_summary['prior_feedback_request_count'])}</strong><span class="english">accepted request IDs</span></div>
      </div>
      <div class="table-wrap"><table>
        <thead><tr><th>目标 ID / Objective</th><th>决策角色 / Role</th><th>方向 / Direction</th><th>指标 / Metric</th><th>证据节点 / Stage</th></tr></thead>
        <tbody>{objective_rows}</tbody>
      </table></div>
      <div class="table-wrap"><table>
        <thead><tr><th>变量 ID / Variable</th><th>范围 / Scope</th><th>状态 / Status</th><th>引入节点 / Stage</th><th>说明 / Description</th></tr></thead>
        <tbody>{variable_rows}</tbody>
      </table></div>
    </section>

    <section>
      <div class="section-heading">
        <h2>审计详情 / Audit details</h2>
        <p>每一项检查都保留状态与证据，后续节点不能只继承最终分数。<span class="english">Every check retains status and evidence; downstream nodes cannot inherit only a final score.</span></p>
      </div>
      <div class="audit-grid">
        <div class="audit-panel">
          <h3>输入审计 / Input audit</h3>
          <ul class="audit-list">{_check_list(input_audit["checks"])}</ul>
        </div>
        <div class="audit-panel">
          <h3>输出审计 / Output audit</h3>
          <ul class="audit-list">{_check_list(output_audit["checks"])}</ul>
        </div>
      </div>
    </section>

    <section>
      <div class="section-heading">
        <h2>候选结果 / Candidate results</h2>
        <p>这些是冻结的原始参照，不是已经证明有效的疫苗候选。<span class="english">These are frozen source references, not vaccine candidates with demonstrated efficacy.</span></p>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>蛋白<br>Protein</th><th>候选 ID<br>Candidate ID</th><th>状态<br>Status</th><th>氨基酸<br>AA</th><th>核苷酸<br>CDS nt</th><th>翻译<br>Translation</th><th>GC</th><th>估算分子量<br>Estimated mass Da</th></tr></thead>
          <tbody>{candidate_rows}</tbody>
        </table>
      </div>
    </section>

    <section>
      <div class="section-heading">
        <h2>人工介入 / Human intervention</h2>
        <p>问题按照阻断优先级分组；已确认的回答作为正式决策保留。<span class="english">Questions are grouped by blocking priority; confirmed answers remain as formal decisions.</span></p>
      </div>
      <div class="action-group">
        <h3>进入候选设计前必须回答 / Required before candidate design</h3>
        <p>这些事项会阻断下一节点。<span class="english">These actions block the next node.</span></p>
        {_action_cards(blocking_actions, "当前没有阻断下一节点的人工事项。", "No human action currently blocks the next node.")}
      </div>
      <div class="action-group">
        <h3>后续阶段需要补充 / Required later</h3>
        <p>现在不阻断，但会随 handoff 持续传递。<span class="english">These do not block now, but remain in every handoff until resolved.</span></p>
        {_action_cards(future_actions, "当前没有延后处理的人工事项。", "No deferred human actions are currently open.")}
      </div>
      <div class="action-group">
        <h3>已经确认 / Confirmed decisions</h3>
        <p>这些回答已经进入项目审计历史。<span class="english">These answers are preserved in the project audit history.</span></p>
        {_action_cards(resolved_actions, "尚无已确认事项。", "No decisions have been confirmed yet.")}
      </div>
    </section>

    <section>
      <div class="section-heading">
        <h2>下一节点交接 / Next-node handoff</h2>
        <p>handoff 会携带候选 ID、输入哈希、三类审计以及全部未解决人工事项。<span class="english">The handoff carries candidate IDs, input hashes, all three audits, and every unresolved human action.</span></p>
      </div>
      <div class="handoff">
        <div>
          <h3>{_e(next_stage_name_zh)}<span class="english">{_e(next_stage_name)}</span></h3>
          <p>阻断事项：{_e(len(blocking_actions))}；持续携带的开放事项：{_e(len(handoff["carried_human_actions"]))}。<span class="english">Blocking actions: {_e(len(blocking_actions))}; open actions carried forward: {_e(len(handoff["carried_human_actions"]))}.</span></p>
        </div>
        {_badge(handoff["readiness"])}
      </div>
    </section>

    <footer class="footer">
      <div>Project {_e(analysis.config.project_id)} &middot; Node {_e(CURRENT_STAGE_ID)} &middot; Run {_e(run_id)}</div>
      <div>结构化记录 / Structured records: summary.json / input_audit.json / process_record.json / output_audit.json / human_actions.json / handoff.json</div>
    </footer>
  </main>
</body>
</html>
"""
