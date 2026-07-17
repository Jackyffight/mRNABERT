# ADR 0005: Make the Python Research Graph the Authoritative Control Plane

Status: accepted target architecture, 2026-07-17

Extends ADR 0001 through ADR 0004. It does not change the frozen workflow-v2
contract or reinterpret historical runs. Implementing this decision requires a
new architecture/workflow version, migration notes, and tests.

## Context

An exploratory central kitchen cannot assume that a project arrives with a
complete design recipe. Direct target evidence, population context, assay context,
and accepted construct boundaries are often absent. Public prior art, analogous
programs, mechanistic knowledge, model predictions, and human discoveries must be
able to introduce new hypotheses while a program is running.

The current immutable round model preserves identity and auditability, but its
canonical stage route remains a single execution DAG. It can carry unresolved
actions and redesign requests, yet it does not represent the complete research
process: hypotheses branch, evidence supports or contradicts them, candidate
families expand, and external knowledge may retire an active branch after several
evaluations have already run.

Four possible orchestration mechanisms were considered:

1. an agent framework such as Pi or a project-specific agent runtime;
2. a smaller pretrained model acting as a scheduler;
3. general LLMs such as Codex, Claude, or an audited open model, controlled by
   versioned Skills;
4. Python scripts and services coordinating the system.

Using any one mechanism as the sole controller creates a poor boundary. A
free-running agent cannot own reproducible state. A scheduler model trained before
high-quality traces exist will encode immature policy. Skills do not provide global
state or planning. Python alone cannot perform open-world literature interpretation,
analogy transfer, or hypothesis generation.

## Decision

Use all four mechanisms as layers with one authority boundary:

> Python owns canonical state and execution. Agents own interaction. General LLMs
> propose research work. Skills expose versioned capabilities. A smaller learned
> policy may later rank actions after sufficient audited traces exist.

The target control path is:

```text
Pi / custom agent / UI
          |
          v
     Research API
          |
          v
Python Research Graph Kernel
  - event store and immutable identities
  - typed node/edge state machine
  - dependency and invalidation propagation
  - budgets, permissions, caching, and task queue
  - eligible action generation
          |
          v
     Model / Tool Router
  - deterministic Python operators
  - general LLM plus versioned Skills
  - audited local/open LLMs
  - learned action-ranking policy
  - pinned scientific models
```

No model or agent writes canonical graph state directly. It returns a typed
proposal such as `proposed_hypothesis`, `proposed_edge`, `evidence_patch`,
`candidate_expansion`, or `proposed_action`. The Python kernel validates identity,
schema, provenance, permission, and dependency requirements before appending an
event.

## Research Graph Model

The product presents one connected research graph but keeps separate linked
layers internally:

- **knowledge graph:** source claims, public evidence, transferred priors,
  mechanistic priors, applicability, and contradictions;
- **hypothesis graph:** questions, assumptions, competing explanations, tests,
  support, contradiction, and refinement;
- **design graph:** candidates, transformations, protected regions, parentage, and
  proposal families;
- **execution graph:** tool calls, models, parameters, artifacts, dependencies,
  retries, and costs;
- **decision graph:** gates, Pareto fronts, ranks, portfolios, uncertainty, and
  release decisions.

The semantic research graph may contain feedback loops. The persisted provenance
graph remains an append-only temporal DAG: a loop creates new versioned nodes and
edges rather than mutating an earlier event. Historical hypotheses and failed
candidates are retired or superseded, never deleted.

Stages become capability operators and report views over a subgraph. They are not
the sole owners of chronological progression. Stage 3 structure assessment, for
example, may be called for an initial candidate, a redesigned child, or a later
counterfactual branch while retaining one stable adapter contract.

## Hypothesis And Evidence States

Research state is not internally binary. Nodes use at least:

```text
proposed -> admitted -> scheduled -> evaluated
                               |-> supported
                               |-> contradicted
                               |-> inconclusive
                               |-> stale
                               |-> retired
                               `-> superseded
```

Execution decisions may be binary, such as whether to allocate the next compute
batch, but uncertainty and scope remain attached to the underlying reasoning.
`not_evaluated`, `inconclusive`, and `contradicted` are distinct states.

If a human discovers during Stage 3 that two of seven hypotheses were invalidated
by external evidence, the system appends the evidence and `contradicts` edges,
retires the two hypotheses, marks affected descendants inactive or stale, cancels
pending jobs, recomputes the dependency closure, and reallocates budget. It does
not erase the hypotheses or restart unrelated branches.

## Planner And Scheduler

The Python kernel first generates the set of actions whose dependencies,
permissions, hard constraints, and budgets permit execution. A planner then ranks
those actions by a versioned acquisition policy approximating:

```text
priority = expected information gain
           * expected decision impact
           * novelty or coverage gain
           / expected compute or experiment cost
```

At first, this policy is deterministic and inspectable, supplemented by attributed
general-LLM proposals and human dispositions. Every decision records action
features, selection reason, cost, evidence produced, graph changes, rank changes,
and human acceptance or rejection.

A smaller model is introduced only after those traces form a credible training and
evaluation set. Its first role is action ranking, relevance classification,
deduplication, or routing, not unrestricted graph mutation. It must support
abstention and deterministic fallback. It may be a learning-to-rank model,
cross-encoder, graph model, or small language model; a generative scheduler is not
assumed to be necessary.

## Agent And Skill Boundaries

An agent framework is a replaceable interaction runtime. It may manage sessions,
tool loops, human approvals, and model routing, but it cannot be the sole state
store or release authority. Replacing Pi, a custom agent, or an LLM provider must
not change candidate identity or invalidate historical evidence.

Every Skill is a versioned capability contract with:

- typed inputs and outputs;
- deterministic or nondeterministic classification;
- model, tool, prompt, and policy versions;
- cost, permission, data-egress, and risk declarations;
- cache identity and artifact requirements;
- validation and regression fixtures;
- the graph proposal types it may return.

The same Skill may be benchmarked across a remote general LLM, an approved local
open model, and a smaller specialized model. Safety or availability failures route
to an authorized alternative or leave the task unevaluated; they do not authorize
prompt bypasses or silent evidence substitution.

## Convergence Semantics

Graph size is not the objective. The system optimizes useful, independent,
decision-changing evidence per unit cost. Convergence requires multiple signals:

- new search batches stop adding material Pareto-front candidates;
- top portfolios remain stable under model, weight, and context perturbations;
- retrieval stops producing independent claims or hypothesis families;
- model disagreement is reduced or explicitly classified as underdetermined;
- the best remaining action has expected value below its cost threshold;
- important mechanisms, candidate families, controls, and counterfactuals remain
  represented.

Failure to converge does not prove a hypothesis false. It may indicate missing
evidence, conflicting observations, hidden variables, evaluator limitations, or
insufficient budget. Convergence does not prove biological truth or an unrestricted
global optimum. It establishes a stable result within the declared design space,
evidence, models, objective policy, and compute boundary.

Compute scales graph coverage, but result quality also depends on hypothesis
expressiveness, evidence fidelity, evaluator calibration, search policy, and
real-world feedback. More compute may amplify a wrong prior unless these dimensions
remain explicit.

## Model Evaluation Strategy

Model choice is empirical rather than architectural. The Mock project will support
blind-recovery benchmarks that hide supplied construct rationales and measure:

- recovery of known literature-supported hypotheses;
- citation and evidence precision;
- accepted novel hypothesis rate;
- false or unsupported edge rate;
- human information required;
- compute cost per decision-changing result;
- portfolio stability and downstream hit rate when labels become available.

The same benchmark compares general commercial LLMs, open models, smaller models,
deterministic rules, and mixed policies. A model gateway selects an approved model
per Skill from measured quality, cost, latency, deployment, and data policies.

## Implementation Order

1. Define typed research node, edge, event, proposal, and invalidation schemas.
2. Build the Python graph kernel, event store, dependency closure, cache identity,
   and replay verifier.
3. Wrap current Stage 1-7 functions as capability operators over graph inputs and
   outputs without changing their scientific semantics.
4. Add the Research API and a thin Pi/custom-agent adapter.
5. Convert open-ended research and audit operations into versioned Skills.
6. Add a model gateway and run cross-model Mock benchmarks.
7. Log planner decisions and outcomes under a deterministic acquisition policy.
8. Train or distill a smaller action-ranking policy only after the trace benchmark
   demonstrates that the target labels are meaningful.
9. Add experiment and assay feedback as graph evidence before granting any learned
   scheduler broader authority.

## Consequences

The architecture can continue operating in deterministic-only mode, but its
open-world hypothesis expansion is explicitly unevaluated without a reasoning
model. Full offline operation requires approved local reasoning and scientific
models. Connected operation may use remote models through explicit data policies.

This design costs more schemas, artifacts, and orchestration work than a linear
agent loop. In return, research can branch, prune, resume, and converge without
losing auditability or binding the product to one framework, model vendor, or
conversation history.

## Rejected Alternatives

- **Agent-only control:** rejected because conversational state is not a canonical,
  replayable scientific record.
- **Small-model-first scheduling:** rejected until audited action/outcome traces
  exist and out-of-distribution behavior can be measured.
- **Skills as orchestration:** rejected because local capabilities do not own global
  dependencies, budgets, or convergence.
- **Python-only reasoning:** rejected because open-world retrieval, analogy, and
  hypothesis formation cannot be fully enumerated as scripts.
- **One unrestricted graph-writing LLM:** rejected because proposal, fact, state,
  execution, and release authority must remain separate.
