# Round-Based Design Optimization

## The Operational Model

The pipeline is both a design engine and a quality-control line. Design starts at
Stage 1, not Stage 6:

```text
design brief + objectives + variables + prior feedback
                         |
                         v
                 Stage 2 proposal pool
                         |
                         v
             Stage 3-6 specialized evaluators
                         |
                         v
          evidence + structured redesign requests
                         |
                         v
             ranking / experiment / learning
                         |
                         v
                next immutable round
```

There is no in-place candidate mutation. A changed sequence is a child proposal in
a later round. Rejected candidates and failed constraints remain visible.

## Stage 1 Contracts

- `design_brief.json` defines the product question, round identity, success criteria,
  and accepted prior feedback.
- `design_variable_registry.json` declares what is fixed, searchable, deferred, or
  forbidden and where each variable enters the workflow.
- `objective_policy.json` separates hard gates, optimization objectives, monitoring
  metrics, and missing-evidence behavior.

These files are machine-validated and hash-snapshotted. `draft` contracts block the
Stage 1 to Stage 2 handoff. `approved_for_mock_execution` permits workflow testing
but never authorizes a scientific or synthesis release.

## Proposal And Feedback Contracts

`proposal_lineage.json` records the proposal round, generator, parameters, parents,
transformation, rationale, and consumed request IDs for each candidate.

`redesign_requests.json` is emitted by evaluators. A request names its candidate,
trigger, evidence reference, affected design variables, instruction, authority, and
review state. It is a proposal for the next round, not permission to edit the current
candidate.

## Kitchen Analogy

The restaurant owner defines what kind of meal should succeed and which tradeoffs
matter before the menu is written. Stage 2 chefs propose recipes. Stage 3-6 stations
test shape, intended diners, manufacturability, and product format. A failed station
does not alter the recipe on the counter; it writes a ticket for the next menu round.
The owner and scientific chef decide which tickets become new recipes.

The original nine recipes are the first tasting menu. They are enough to test the
kitchen line, not enough to claim that the restaurant explored every possible dish.

## Current Implementation Boundary

Version 0.15 implements the design-round contracts, approval gate, proposal
lineage, evaluator feedback artifacts, Stage 7 feedback aggregation, and an explicit
Stage 2 combinatorial generator. The first grammar-bounded expansion starts from
nine source/manual seeds, materializes 183 unique proposals, and skips one exact
duplicate of an existing manual construct. That 192-record batch remains the
preserved baseline. A second evidence-guided search freezes a 24,464-sequence
eligible fusion pool, materializes 2,048 new fusions, and selects 384 candidates for
Stage 3 under a versioned compute budget. All remain Mock-only and unapproved for
scientific release.

Scientific or model-driven generators must emit the same validated proposal schema.
Registering a model role does not mean that model ran: every adapter remains
`deferred` until its responsible stage, inputs, and authority constraints are ready.
