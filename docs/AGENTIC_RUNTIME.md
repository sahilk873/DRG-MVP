# Agentic runtime: deterministic RAG + self-learning

This is the plan for making the agents materially more capable — authoring across the stack, learning
from experience, using fewer tokens — **without** giving up determinism, reproducibility, or the audit
trail that makes the product deployable in a billing/PHI context.

## Two planes

- **Authoring / design plane (agentic).** Agents examine bounded data profiles, write code, and — in the
  target design — run it *only in an OS-level sandbox* (a separate `python -I -S` subprocess with
  `RLIMIT_CPU/AS/FSIZE/NOFILE`, a wall-clock kill, empty env, fresh cwd, and no network), iterating
  generate → run → evaluate → self-correct against golden samples until a deterministic scorer passes.
- **Execution plane (deterministic).** Only a **frozen, hash-pinned, verified** artifact is admitted to
  the code path that touches real data. Promotion requires an executable status, a passing score, a
  fingerprint match, and re-verification that the artifact hash equals what was scored. The execution
  plane loads artifacts only from a content-addressed registry with a hash-chained audit ledger.

Net: the agent writes and runs code freely upstream; what mutates real claims stays reproducible,
versioned, human-approvable, and auditable. Model output is never authoritative until it clears the gate.

## What is built now (`src/revenue_integrity/runtime/`)

The deterministic **learning substrate** — the foundation the rest builds on — is implemented and tested
(`tests/test_runtime.py`):

- **`knowledge.py` — `KnowledgeStore`.** An append-only, content-addressed, hash-chained ledger of
  verified experience (`Exemplar`s): approved adapter mappings, transforms, ontology deltas, groundings,
  and labeled reviewer outcomes. Idempotent on content, tamper-evident (`verify_chain`), serializable.
  "Self-learning" = this store grows with verified records; there is no opaque model drift.
- **`retrieval.py` — `DeterministicRetriever` (RAG).** Given a new task's feature tokens (e.g., a new
  provider's column names, or a finding's rule + codes), it returns the top-k most relevant prior
  exemplars by transparent Jaccard overlap with a content-hash tiebreak — fully reproducible, with a
  `retrieval_digest` for provenance. `retrieval_pack` + `estimate_tokens` show the token win: inject a
  couple of relevant precedents instead of the whole ontology/library.
- **`promotion.py` — verify-then-promote + write-back.** `ArtifactScore` + `admit_artifact` gate an
  authored artifact (executable status + score thresholds) before it is recorded. `learn_from_decision`
  writes each reviewer outcome back as retrievable precedent.
- **`sandbox.py` — OS sandbox.** `run_sandboxed` executes agent-authored code (a `transform(row)`
  function) in a fresh isolated subprocess (`python -I -S`, empty env, throwaway cwd, `RLIMIT_CPU/
  FSIZE/NOFILE` + `RLIMIT_AS` on Linux, wall-clock kill). Runaway loops, crashes, non-serializable
  output, and per-row errors are contained and reported — never fatal to the host. Tests prove an
  infinite loop is killed and host env vars are invisible to authored code. (Hard network/syscall
  isolation is out of scope for this interface and should be added via a container/seccomp in prod.)
- **`self_eval.py` — scoring loop.** `score_artifact` runs authored code through the *same* sandbox on
  golden samples and returns an `ArtifactScore` (parse rate, conformance, exact match); `evaluate_and_admit`
  closes generate → run → evaluate → promote in one call, recording the exact code that was scored.

### How this hits the goals

- **Token efficiency + accuracy (RAG):** agents are prompted with a small, relevant retrieval pack, not
  everything. Pairs with the ontology-subgraph retrieval already in `agent/src/ontology-subgraph.ts`.
- **Self-learning:** approved artifacts and reviewer decisions accumulate as verified exemplars, so the
  next similar onboarding/finding retrieves precedent — the system measurably improves over time
  (`test_store_improves_as_verified_experience_accumulates`).
- **Determinism preserved:** retrieval, hashing, and promotion are all deterministic; learning is
  accumulation of *verified* records, not weight updates.

## Integration points

- ✅ **Adapter designer** (`agent/src/agents/adapter-designer.ts`): now takes an optional
  `precedentLibrary`; when supplied it retrieves (RAG, `agent/src/runtime/retrieval.ts`) the top-k
  most similar approved adapters for the profile and sends **only those** as `prior_templates`
  instead of every prior template — fewer tokens, better priors. A test drives the live wiring.
- ✅ **Robust ontology** (`src/revenue_integrity/ontology_extension.py`): an agent proposes an
  additive-only `OntologyDelta` (data, not code); `verify_promotion_preflight` fails closed unless
  the proposal is additive-only, internally valid, version-bumped, and digest-recomputable.
- ✅ **Reviewer write-back** (`runtime.learn_from_review_log`): a deterministic projection of an audited
  review packet + its decision log into `review_outcome` exemplars, so future similar findings retrieve
  how comparable ones were resolved. Built from the audited artifacts (not a side-effect of `submit`),
  and idempotent on replay.

## Roadmap (from the capability-expansion workflow)

Build order after this substrate (✅ = done):
1. ✅ **OS sandbox** (`runtime/sandbox.py`) + **self-eval harness** (`runtime/self_eval.py`) — run authored
   code safely and score it against golden samples (generate → run → evaluate → promote).
2. ✅ **Graceful-degradation quarantine plane** (`ingestion/models.py` `DegradationPolicy` +
   `ingestion/adapter.py`) — opt-in `mode="quarantine"` routes recoverable per-encounter faults
   (duplicate encounter, multiple/zero claims, orphan rows referencing an unknown encounter,
   admission-after-discharge) to a quarantine list and keeps processing the clean encounters, with a
   `max_quarantined` circuit breaker that aborts a too-broken batch. Default stays fail-closed.
3. ✅ **Robust ontology**: `ontology_extension.py` preflight gate — agent proposes an *additive-only*
   delta; Python verifies additive-only + internal validity + version bump + recomputed digest before
   promotion (cross-language digest parity is guaranteed by the shared byte-for-byte digest algorithm).
4. ✅ **Authored readers** (`runtime/authored_reader.py`): the agent authors a `read(raw)` parser for a
   novel format; sandbox-scored against golden rows, promoted as a hash-pinned `AuthoredReaderDefinition`,
   executed only in the sandbox with a code-hash freeze. Rows flow into the profiler/adapter pipeline.
5. ✅ **Authored transforms** (`runtime/authored_transform.py`): same pattern for field transforms the
   fixed DSL can't express — regex extraction, currency→cents, arithmetic, conditional — sandbox-scored,
   promoted hash-pinned, executed in-sandbox.
6. ✅ **Data-model fidelity** (`models.py`, `financial.py`): the `Claim` now carries optional, additive
   `diagnosis_details` (sequence + per-diagnosis POA) and `charge_lines` (activating `financial.ClaimLine`),
   with `principal_diagnosis()` and a `charges_from_lines()` reducer. Backward-compatible (legacy cases
   unaffected); a formal case `schema_version` bump + a sequence-aware grouper are the follow-ups.

All planned items are now built. Everything above stays inside the two-plane model: agentic upstream,
deterministic and audited downstream. Remaining hardening (production, not new capability): OS-level
network/syscall jailing of the sandbox (container/seccomp), a formal case `schema_version` migration for
the new claim fidelity, and a sequence-/POA-aware grouper.
