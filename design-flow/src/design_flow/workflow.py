"""Complete system route and audit contract for every design-flow stage."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class StageDefinition:
    order: str
    stage_id: str
    name: str
    purpose: str
    capabilities: tuple[str, ...]
    input_audit: tuple[str, ...]
    process: tuple[str, ...]
    output_audit: tuple[str, ...]
    human_intervention: tuple[str, ...]
    depends_on: tuple[str, ...] = ()


CURRENT_STAGE_ID = "program_and_source_intake"
WORKFLOW_ID = "vaccine-design-build-test-learn"
WORKFLOW_VERSION = 2
SYSTEM_ARCHITECTURE_VERSION = 2
APPROVED_WORKFLOW_HASHES = {
    (1, 1): "0c2f4fff63cddcf3ea2851b0501db7dff61ca8a93eea7bf93b0b09a4dc709763",
    (2, 2): "a5e858dba9ae2c4d480f9c2b1661ed79138211f4e5e99157dce3b1f6aef30b0c",
}


FULL_WORKFLOW = (
    StageDefinition(
        order="1",
        stage_id=CURRENT_STAGE_ID,
        name="Program definition and source intake",
        purpose=(
            "Freeze the design-round question, objectives, searchable variables, source identities, "
            "immutable controls, and evidence provenance before any sequence is proposed."
        ),
        capabilities=(
            "Pair protein and CDS records and verify translation consistency.",
            "Detect malformed, mislabeled, duplicated, or incomplete source records.",
            "Create stable candidate IDs, input hashes, and a source evidence baseline.",
            "Validate a versioned design brief, objective policy, and design-variable registry.",
        ),
        input_audit=(
            "Verify target indication, intended host, product modalities, owners, and success criteria.",
            "Verify round identity, prior feedback, hard gates, optimization objectives, and searchable variables.",
            "Verify source accession/version, organism or isolate, file integrity, and record identity.",
            "Verify AA/CDS record counts, alphabets, reading frames, start/stop behavior, and pairing IDs.",
        ),
        process=(
            "Normalize FASTA formatting without silently changing biological sequence content.",
            "Translate CDS with the declared genetic code and compare it residue by residue with AA input.",
            "Calculate descriptive sequence metrics and quarantine inconsistent records.",
            "Freeze the round contract without allowing an LLM or model to change its authority status.",
        ),
        output_audit=(
            "Confirm every accepted source has an immutable ID, normalized sequences, and SHA-256 provenance.",
            "Confirm errors, warnings, and exclusions are explicit and machine-readable.",
            "Confirm the source set and unresolved decisions are ready for candidate specification.",
            "Confirm every objective and variable is explicit before proposal generation begins.",
        ),
        human_intervention=(
            "Approve the design brief, objective policy, variable registry, and immutable reference controls.",
            "Resolve missing provenance and decide whether disputed or mislabeled files are replaced or rejected.",
            "Assign owners and resolutions to every open action before its blocking stage."
        ),
    ),
    StageDefinition(
        order="2",
        stage_id="candidate_specification",
        name="Candidate specification and generation",
        purpose=(
            "Generate or import a round-specific proposal pool and represent originals, truncations, manual "
            "controls, and model-generated constructs under one explicit lineage and construct grammar."
        ),
        capabilities=(
            "Enumerate single-protein and multi-protein fusion candidates.",
            "Track residue boundaries, domain order, linkers, tags, signal peptides, and cleavage sites.",
            "Use pluggable sequence design models while preserving manually supplied controls.",
            "Consume accepted redesign requests from the prior immutable round.",
        ),
        input_audit=(
            "Accept only source records released by stage 1 and verify their hashes.",
            "Audit allowed boundaries, required domains, forbidden edits, length limits, and modality constraints.",
            "Audit every manual construct against its claimed components and annotations.",
            "Audit generator identity, parameters, parents, rationale, and consumed feedback for every proposal.",
        ),
        process=(
            "Generate candidates from a versioned grammar and record every parent-to-child transformation.",
            "Deduplicate exact sequences and separate generation from scoring.",
            "Retain original and manual constructs as named controls in every candidate batch.",
            "Keep biological candidate identity separate from proposal provenance.",
        ),
        output_audit=(
            "Verify exact AA sequence, component map, lineage, generator revision, and parameter set per candidate.",
            "Verify no tag, linker, residue deletion, or insertion is implicit.",
            "Verify candidate coverage and diversity against the approved design space.",
            "Verify proposal lineage and feedback consumption are complete and machine-readable.",
        ),
        human_intervention=(
            "Approve construct grammar, boundaries, domain order, linker families, tags, and required controls.",
            "Review candidates violating biological assumptions even when computationally valid.",
            "Freeze the candidate batch before expensive model evaluation."
        ),
        depends_on=(CURRENT_STAGE_ID,),
    ),
    StageDefinition(
        order="3",
        stage_id="protein_structure_assessment",
        name="Protein structure assessment",
        purpose=(
            "Test whether each construct is structurally plausible and preserves intended domains, surfaces, "
            "and interfaces before downstream ranking."
        ),
        capabilities=(
            "Run one or more pinned monomer or complex structure predictors.",
            "Extract confidence, disorder, clashes, domain geometry, interfaces, and refolding consistency.",
            "Compare variants with source structures and manual controls."
        ),
        input_audit=(
            "Verify candidate batch hash, chain definitions, oligomer assumptions, templates, and predictor limits.",
            "Verify sequence length and alphabet compatibility for every selected structure backend.",
            "Verify source or reference structures and residue mappings when supplied."
        ),
        process=(
            "Predict structures with pinned models, revisions, seeds, and inference parameters.",
            "Compute confidence and geometry features under a common residue map.",
            "Repeat or cross-check predictions when uncertainty or model disagreement is material."
        ),
        output_audit=(
            "Verify every score maps to the exact candidate and structure artifact checksum.",
            "Verify failures and low-confidence regions are retained rather than filtered silently.",
            "Verify structural gates are calibrated and do not claim experimental folding.",
            "Export deterministic review findings as next-round redesign requests without mutating candidates.",
        ),
        human_intervention=(
            "Review domain preservation, exposed regions, unexpected interfaces, and low-confidence linkers.",
            "Approve exceptions to structural gates with a written rationale.",
            "Select candidates requiring alternative oligomer or construct hypotheses."
        ),
        depends_on=("candidate_specification",),
    ),
    StageDefinition(
        order="4",
        stage_id="immune_evidence_assessment",
        name="Immune evidence assessment",
        purpose=(
            "Assemble computational evidence relevant to immune recognition while keeping predictions "
            "separate from experimental immunogenicity claims."
        ),
        capabilities=(
            "Assess conservation, surface accessibility, epitope evidence, host presentation coverage, and similarity risks.",
            "Map evidence to residues, domains, and candidate structure context.",
            "Quantify disagreement and uncertainty across methods and host assumptions."
        ),
        input_audit=(
            "Verify target population or host genetics, pathogen sequence panel, and evidence database versions.",
            "Verify candidate residue maps and structural confidence before geometry-dependent analysis.",
            "Verify exclusion lists, homology databases, and threshold calibration sets."
        ),
        process=(
            "Run pinned evidence adapters and retain per-method raw scores.",
            "Aggregate only after calibration, leakage checks, and explicit host assumptions.",
            "Flag conflicting evidence instead of hiding it in a single composite score."
        ),
        output_audit=(
            "Verify residue-level evidence is traceable to model/database revision and candidate hash.",
            "Verify coverage, uncertainty, conflicts, and unsupported regions are reported.",
            "Verify outputs are labeled computational evidence, not efficacy or safety conclusions.",
            "Keep missing evidence separate from evidence-backed next-round redesign requests.",
        ),
        human_intervention=(
            "Confirm host population assumptions and acceptable evidence thresholds.",
            "Review conserved versus variable regions and biologically implausible predictions.",
            "Approve which evidence may act as a gate versus a ranking feature."
        ),
        depends_on=("candidate_specification", "protein_structure_assessment"),
    ),
    StageDefinition(
        order="5",
        stage_id="developability_assessment",
        name="Developability and manufacturability assessment",
        purpose=(
            "Estimate whether candidates can be expressed, purified, handled, and formulated without "
            "obvious sequence or structure liabilities."
        ),
        capabilities=(
            "Assess solubility, aggregation, disorder, proteolysis, topology, stability, and modification liabilities.",
            "Compare predicted behavior across expression hosts and product formats.",
            "Expose hard constraints separately from tunable optimization objectives."
        ),
        input_audit=(
            "Verify intended expression host, compartment, purification strategy, formulation assumptions, and construct form.",
            "Verify sequence/structure inputs and model applicability domains.",
            "Verify thresholds against internal or public experimental baselines where available."
        ),
        process=(
            "Run pinned property predictors and rule-based checks.",
            "Normalize outputs with applicability and uncertainty metadata.",
            "Evaluate tradeoffs without allowing one favorable metric to erase a hard liability."
        ),
        output_audit=(
            "Verify every liability has residue-level or construct-level evidence and severity.",
            "Verify pass/fail rules and ranking features are versioned and reproducible.",
            "Verify unsupported predictions are marked not evaluated.",
            "Export rule-backed liabilities as reviewable next-round redesign requests.",
        ),
        human_intervention=(
            "Confirm host, purification, formulation, and acceptable risk thresholds.",
            "Review liabilities that may be mitigated experimentally rather than by redesign.",
            "Approve redesign requests and preserve the rejected candidate lineage."
        ),
        depends_on=("candidate_specification", "protein_structure_assessment"),
    ),
    StageDefinition(
        order="6A",
        stage_id="protein_product_design",
        name="Recombinant protein product design",
        purpose=(
            "Convert an accepted antigen candidate into a traceable recombinant expression and purification construct."
        ),
        capabilities=(
            "Design expression-specific signal peptides, tags, cleavage sites, and host-compatible coding constructs.",
            "Evaluate expression and purification constraints without changing the antigen silently.",
            "Release a protein-product specification for experimental review."
        ),
        input_audit=(
            "Verify selected antigen lineage, expression host, vector constraints, tags, cleavage strategy, and product form.",
            "Verify every expression-only addition is distinguishable from the final antigen sequence.",
            "Verify developability gates and unresolved exceptions."
        ),
        process=(
            "Generate versioned expression constructs and back-translate only under declared host constraints.",
            "Re-run sequence and structural checks affected by expression-specific additions.",
            "Create a bill of materials and exact release sequence."
        ),
        output_audit=(
            "Verify antigen, expression construct, tags, and cleavage products are separately defined.",
            "Verify released DNA and translated protein match the approved design.",
            "Verify all manufacturing assumptions and unresolved risks are included."
        ),
        human_intervention=(
            "Approve expression host, vector, tag, cleavage, purification, and formulation choices.",
            "Confirm whether expression-only residues remain in the tested product.",
            "Sign off the exact construct before synthesis or cloning."
        ),
        depends_on=("developability_assessment",),
    ),
    StageDefinition(
        order="6B",
        stage_id="mrna_product_design",
        name="mRNA product design",
        purpose=(
            "Create host-aware mRNA constructs that preserve the approved antigen protein while optimizing "
            "delivery-specific sequence constraints."
        ),
        capabilities=(
            "Optimize synonymous CDS under codon, GC, motif, repeat, and RNA-structure constraints.",
            "Version UTR, cap, poly(A), and other non-coding design assumptions when available.",
            "Compare multiple Pareto-optimal mRNA designs instead of emitting one opaque sequence."
        ),
        input_audit=(
            "Verify exact antigen AA, target species/cell context, delivery platform constraints, and forbidden motifs.",
            "Verify source/optimized CDS identity and quarantine mislabeled sequences.",
            "Verify non-coding elements and formulation assumptions are licensed and versioned."
        ),
        process=(
            "Generate synonymous candidates while continuously asserting translation identity.",
            "Score codon, motif, GC, repeat, and RNA-structure objectives with pinned tools.",
            "Retain Pareto frontier, parentage, and all rejected hard-constraint violations.",
            "Return hard-constraint failures as mRNA-specific requests for the next immutable round.",
        ),
        output_audit=(
            "Verify every released mRNA translates exactly to the approved antigen.",
            "Verify all coding and non-coding components, scores, constraints, and tool revisions.",
            "Verify no optimized sequence is accepted solely because of a language-model score."
        ),
        human_intervention=(
            "Approve target species/cell context, UTR/poly(A) choices, motif constraints, and synthesis limits.",
            "Review Pareto tradeoffs and select more than one design when uncertainty is material.",
            "Sign off the exact mRNA sequence before synthesis."
        ),
        depends_on=("developability_assessment",),
    ),
    StageDefinition(
        order="7",
        stage_id="integrated_ranking",
        name="Integrated ranking and portfolio selection",
        purpose=(
            "Combine validated evidence into a transparent, uncertainty-aware portfolio without hiding hard gates "
            "or model disagreement."
        ),
        capabilities=(
            "Rank candidates by modality-specific objectives, uncertainty, diversity, and experimental value.",
            "Compare generated candidates directly with originals and manual controls.",
            "Perform sensitivity analysis across weights, thresholds, and missing evidence."
        ),
        input_audit=(
            "Verify candidate identities, stage completeness, score calibration, missing values, and exclusion reasons.",
            "Verify no training/test leakage or duplicated biological entities in task models.",
            "Verify ranking policy, weights, hard gates, and decision budget are frozen before scoring."
        ),
        process=(
            "Apply hard gates first, then transparent multi-objective ranking and uncertainty penalties.",
            "Measure rank stability and retain component-level explanations.",
            "Select a diverse portfolio with explicit positive, negative, original, and manual controls."
        ),
        output_audit=(
            "Verify ranks reproduce from component scores and policy revision.",
            "Verify excluded candidates remain visible with reasons.",
            "Verify selected portfolio covers controls, diversity, and uncertainty rather than only top scores.",
            "Publish accepted, rejected, and deferred next-round requests with the selected portfolio.",
        ),
        human_intervention=(
            "Approve ranking policy, budget, risk tolerance, and control composition.",
            "Review unstable ranks and disagreements between modalities or evidence families.",
            "Sign off the experimental candidate portfolio."
        ),
        depends_on=(
            "protein_structure_assessment",
            "immune_evidence_assessment",
            "developability_assessment",
            "protein_product_design",
            "mrna_product_design",
        ),
    ),
    StageDefinition(
        order="8",
        stage_id="experiment_release",
        name="Experiment design and release",
        purpose=(
            "Convert the selected portfolio into a blinded, controlled, and traceable experimental release package."
        ),
        capabilities=(
            "Define controls, replicates, randomization, batches, acceptance criteria, and sample identities.",
            "Generate release manifests and chain-of-custody records.",
            "Freeze predictions before outcomes are observed."
        ),
        input_audit=(
            "Verify selected candidates, synthesis feasibility, controls, assay objectives, budget, and ethical approvals.",
            "Verify sample identifiers do not leak ranking labels to blinded operators.",
            "Verify protocol versions, units, replicate plans, and acceptance rules."
        ),
        process=(
            "Create blinded sample sheets, randomization, batch layout, and release checksums.",
            "Freeze model predictions and candidate manifests before execution.",
            "Record deviations through controlled amendments."
        ),
        output_audit=(
            "Verify every physical sample maps to one immutable candidate and batch.",
            "Verify controls, replicates, and acceptance criteria are complete.",
            "Verify release package approval and amendment history."
        ),
        human_intervention=(
            "Scientific, laboratory, safety, and quality owners approve protocol and release.",
            "Operators record deviations, failed controls, and sample handling events.",
            "No model is updated until the frozen release is closed."
        ),
        depends_on=("integrated_ranking",),
    ),
    StageDefinition(
        order="9",
        stage_id="assay_ingestion",
        name="Assay ingestion and quality control",
        purpose=(
            "Turn raw laboratory observations into immutable, unit-aware, quality-controlled evidence linked to "
            "the released candidates."
        ),
        capabilities=(
            "Ingest raw files, metadata, controls, replicates, batches, units, and protocol revisions.",
            "Apply predefined assay QC and preserve both raw and processed observations.",
            "Expose batch effects, missingness, and censored measurements."
        ),
        input_audit=(
            "Verify file checksums, instrument exports, sample mapping, protocol, batch, units, and operator metadata.",
            "Verify controls and acceptance criteria before unblinding.",
            "Verify amendments and exclusions have signed reasons."
        ),
        process=(
            "Store immutable raw data, normalize through versioned transforms, and run assay-specific QC.",
            "Aggregate replicates only under declared rules and retain individual measurements.",
            "Unblind only after QC status is frozen."
        ),
        output_audit=(
            "Verify every value traces to raw file, sample, unit, batch, and transform revision.",
            "Verify failed controls and excluded observations remain visible.",
            "Verify labels are suitable for the declared downstream learning task."
        ),
        human_intervention=(
            "Laboratory and data owners adjudicate failed controls, deviations, censoring, and exclusions.",
            "Approve the frozen analysis dataset and permitted endpoint definitions.",
            "Document any exploratory endpoint separately from preregistered endpoints."
        ),
        depends_on=("experiment_release",),
    ),
    StageDefinition(
        order="10",
        stage_id="learning_and_iteration",
        name="Learning, calibration, and next-round design",
        purpose=(
            "Learn task-specific decision models from leakage-safe experimental data, update objective evidence, "
            "and authorize a new immutable design round that restarts from Stage 1."
        ),
        capabilities=(
            "Train and compare task heads, baselines, calibration models, and active-learning policies.",
            "Estimate generalization by biological split, batch, and time.",
            "Propose the next portfolio while preserving all prior predictions and outcomes."
        ),
        input_audit=(
            "Verify frozen assay labels, entity-level split rules, batch metadata, sample size, and endpoint definitions.",
            "Verify baselines, model revisions, hyperparameter budgets, and leakage controls.",
            "Verify unresolved data-quality issues are represented, not discarded silently."
        ),
        process=(
            "Train baselines and task models under fixed splits and repeated seeds.",
            "Calibrate uncertainty, perform ablations, and compare prospective decision utility.",
            "Version model cards, predictions, and active-learning acquisition policy."
        ),
        output_audit=(
            "Verify held-out and prospective metrics, uncertainty calibration, stability, and failure analysis.",
            "Verify claims are limited to evaluated populations and assays.",
            "Verify the next-round proposal preserves controls and targets informative uncertainty."
        ),
        human_intervention=(
            "Approve labels, splits, decision thresholds, and acceptable evidence for model promotion.",
            "Review failure modes and decide whether to redesign candidates, assays, or models.",
            "Approve the next round, then restart at program intake with inherited lineage, feedback, and evidence."
        ),
        depends_on=("assay_ingestion",),
    ),
)


STAGE_BY_ID = {stage.stage_id: stage for stage in FULL_WORKFLOW}
STAGE_POSITION_BY_ID = {
    stage.stage_id: index for index, stage in enumerate(FULL_WORKFLOW)
}


def action_due_for_handoff(
    required_before_stage: str,
    *,
    current_stage: str,
    to_stages: tuple[str, ...],
) -> bool:
    """Return whether an open action must block this handoff.

    Actions due at an earlier stage remain overdue. Future actions are carried
    forward without blocking until their declared stage is an immediate target.
    """
    if (
        required_before_stage not in STAGE_POSITION_BY_ID
        or current_stage not in STAGE_POSITION_BY_ID
        or any(stage_id not in STAGE_POSITION_BY_ID for stage_id in to_stages)
    ):
        return True
    return (
        STAGE_POSITION_BY_ID[required_before_stage]
        <= STAGE_POSITION_BY_ID[current_stage]
        or required_before_stage in to_stages
    )


def stage_contract(stage: StageDefinition) -> dict[str, Any]:
    return {
        "order": stage.order,
        "stage_id": stage.stage_id,
        "name": stage.name,
        "purpose": stage.purpose,
        "capabilities": list(stage.capabilities),
        "input_audit_contract": list(stage.input_audit),
        "process_contract": list(stage.process),
        "output_audit_contract": list(stage.output_audit),
        "human_intervention_contract": list(stage.human_intervention),
        "depends_on": list(stage.depends_on),
    }


def workflow_contract() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "system_architecture_version": SYSTEM_ARCHITECTURE_VERSION,
        "workflow_id": WORKFLOW_ID,
        "workflow_version": WORKFLOW_VERSION,
        "entry_stage": CURRENT_STAGE_ID,
        "stages": [stage_contract(stage) for stage in FULL_WORKFLOW],
    }


def workflow_contract_sha256() -> str:
    canonical = json.dumps(
        workflow_contract(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def approved_workflow_hash(
    architecture_version: int = SYSTEM_ARCHITECTURE_VERSION,
    workflow_version: int = WORKFLOW_VERSION,
) -> str | None:
    return APPROVED_WORKFLOW_HASHES.get((architecture_version, workflow_version))


def validate_workflow(stages: tuple[StageDefinition, ...] = FULL_WORKFLOW) -> None:
    if not stages:
        raise ValueError("Workflow must contain at least one stage")
    stage_ids = [stage.stage_id for stage in stages]
    duplicate_ids = sorted(
        stage_id for stage_id in set(stage_ids) if stage_ids.count(stage_id) > 1
    )
    if duplicate_ids:
        raise ValueError(f"Duplicate workflow stage IDs: {duplicate_ids}")
    if CURRENT_STAGE_ID not in stage_ids:
        raise ValueError(f"Current stage is missing from workflow: {CURRENT_STAGE_ID}")
    orders = [stage.order for stage in stages]
    duplicate_orders = sorted(order for order in set(orders) if orders.count(order) > 1)
    if duplicate_orders:
        raise ValueError(f"Duplicate workflow stage orders: {duplicate_orders}")
    for stage in stages:
        scalar_fields = (stage.order, stage.stage_id, stage.name, stage.purpose)
        contract_fields = (
            stage.capabilities,
            stage.input_audit,
            stage.process,
            stage.output_audit,
            stage.human_intervention,
        )
        if not all(isinstance(value, str) and value.strip() for value in scalar_fields):
            raise ValueError(f"Workflow stage has an empty identity field: {stage.stage_id!r}")
        if not all(
            values and all(isinstance(value, str) and value.strip() for value in values)
            for values in contract_fields
        ):
            raise ValueError(f"Workflow stage has an empty audit contract: {stage.stage_id}")

    known_ids = set(stage_ids)
    unknown_dependencies = sorted(
        (stage.stage_id, dependency)
        for stage in stages
        for dependency in stage.depends_on
        if dependency not in known_ids
    )
    if unknown_dependencies:
        raise ValueError(f"Unknown workflow dependencies: {unknown_dependencies}")

    remaining_dependencies = {
        stage.stage_id: set(stage.depends_on)
        for stage in stages
    }
    resolved: set[str] = set()
    while remaining_dependencies:
        ready = sorted(
            stage_id
            for stage_id, dependencies in remaining_dependencies.items()
            if dependencies <= resolved
        )
        if not ready:
            cycle_nodes = sorted(remaining_dependencies)
            raise ValueError(f"Workflow dependency cycle detected among: {cycle_nodes}")
        resolved.update(ready)
        for stage_id in ready:
            del remaining_dependencies[stage_id]

    roots = sorted(stage.stage_id for stage in stages if not stage.depends_on)
    if roots != [CURRENT_STAGE_ID]:
        raise ValueError(
            f"Workflow must have exactly one entry stage ({CURRENT_STAGE_ID}); found {roots}"
        )

    dependents = {stage_id: set() for stage_id in stage_ids}
    for stage in stages:
        for dependency in stage.depends_on:
            dependents[dependency].add(stage.stage_id)
    reachable = {CURRENT_STAGE_ID}
    frontier = [CURRENT_STAGE_ID]
    while frontier:
        current = frontier.pop()
        for dependent in dependents[current] - reachable:
            reachable.add(dependent)
            frontier.append(dependent)
    if reachable != known_ids:
        raise ValueError(f"Workflow contains unreachable stages: {sorted(known_ids - reachable)}")

    terminals = sorted(stage_id for stage_id, children in dependents.items() if not children)
    if len(terminals) != 1:
        raise ValueError(f"Workflow must have exactly one terminal stage; found {terminals}")


validate_workflow()

if approved_workflow_hash() != workflow_contract_sha256():
    raise RuntimeError(
        "Executable workflow differs from its approved version/hash; "
        "create a new workflow version instead of rewriting the frozen contract"
    )
