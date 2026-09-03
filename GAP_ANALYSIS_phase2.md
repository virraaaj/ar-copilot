# Phase 2 — Gap Analysis vs `capability_contract.yaml`

Date: 2026-09-03. Ranked by **blast radius**, not effort.
Every row carries a file:line. Nothing here is fixed yet.

Evidence gathered by 4 delegated workers; the three findings marked ✅VERIFIED
were re-checked personally against source before inclusion.

---

## BLOCKERS — can produce a wrong message to a real counterparty, or defeat a safety fence

### B1 — The recipient allowlist does not protect the unattended path ✅VERIFIED
| | |
|---|---|
| **Contract clause** | `policy_knobs.external_send_allowlist_enforced`, PM-F-02 |
| **Current behavior** | `executor.py` passes `case.get("customer_email")` to the pre-send guard, but the email is actually sent to `resolve_recipient(case, tactic)` — a *different address* for PM-directed tactics. |
| **file:line** | `loop/executor.py:179` (passes `customer_email`) vs `loop/executor.py:195` (real recipient). Correct version exists at `loop/traced_loop.py:343`. |
| **Why it's a blocker** | Two failures. (a) The allowlist validates the wrong address, so a PM-directed send is fenced by the *customer's* address. (b) `loop/guardrails.py:72` reads `if allowlist and recipient and ...` — when `customer_email` is empty (the normal case for first PM contact) `recipient` is falsy and **the allowlist check is skipped entirely**. `executor.py` is the automatic poller path — the one that runs unattended. |
| **Proposed fix** | Move `resolve_recipient` above the guard call in `executor.py` and pass the resolved recipient (mirror `traced_loop.py:309→343`). Separately, make `check_before_send` fail closed when an allowlist is configured and `recipient` is None. |

### B2 — A hostile reply gets another chase email, not an escalation ✅VERIFIED
| | |
|---|---|
| **Contract clause** | CUST-E-05 (hostility), CUST-E-02 (legal counsel) |
| **Current behavior** | `"sue"`, `"lawsuit"`, `"harass"`, `"ridiculous"` classify as `HOSTILE` at confidence 0.8. `apply_reply_signal` has **no branch** for it — it falls to the generic `else`, setting `state="customer_responded"`. `choose_objective` has no hostile branch either; confidence 0.8 also clears the `<0.55` clarify safety-net, so it falls through to the default `establish_contact` and the agent drafts another chase email. |
| **file:line** | classify `loop/reply_interpreter.py:122-123`; no branch `loop/signal_ingestion.py:178-179`; default objective `domain/goals.py:154`; tactics `domain/goals.py:217` |
| **Why it's a blocker** | A customer saying "we'll sue you" receives a payment chaser in reply. Also means a litigation threat is silently swallowed — it is the *only* place "sue"/"lawsuit" is detected inbound, and it leads nowhere. |
| **Proposed fix** | Add a `hostile` branch in `signal_ingestion.py` that sets an escalation state, and a `choose_objective` branch returning `escalate_handoff`. Split litigation language into its own signal (see M2). |

### B3 — No AI disclosure exists anywhere
| | |
|---|---|
| **Contract clause** | CUST-A-05, CUST-F-07 |
| **Current behavior** | Nothing. Zero matches across `app/` for AI/automated-sender disclosure. Every customer email presents as a person. |
| **file:line** | ABSENT — send paths `loop/executor.py:230-287`, `loop/traced_loop.py:358-409` |
| **Why it's a blocker** | Named requirement from the business owner; every external send is non-compliant today. |
| **Proposed fix** | Append disclosure deterministically at send time in **both** send paths (external recipients only — PM excluded per contract), plus a check that fails closed if a customer-bound body lacks it. Must not be prompt-only. |

### B4 — `max_postponements` is a policy setting that does nothing ✅VERIFIED
| | |
|---|---|
| **Contract clause** | MISSION ("when things drag on past the limits… escalate"), CUST-H-02, POLICY-01 |
| **Current behavior** | `consume_postponement()` increments `postponements_used` (`domain/budgets.py:29-30`), and both `blocker` and `checkback` replies call it (`signal_ingestion.py:157`, `:174`). But `AutonomyBudget.exhausted()` only tests `unanswered_used` and `misses_used` — **`postponements_used` is never read**. |
| **file:line** | `domain/types.py:145-149` (the omission); consumers `loop/signal_ingestion.py:157,174`; sole escalation trigger `domain/goals.py:82` |
| **Why it's a blocker** | A customer can postpone indefinitely and the agent never escalates. `max_postponements` is exposed as a tunable on the policy page (`config/policy_overrides.py:41`) — the business can set it, and it silently has no effect. This is exactly the "delayed too much → escalate → notify PM" behaviour that was specified. |
| **Proposed fix** | Add `postponements_used >= max_postponements` to `exhausted()`. Add a test that fails if the clause is removed. |

### B5 — The traced path has no "escalate instead of email" branch
| | |
|---|---|
| **Contract clause** | ENF-04, PM-A-06 |
| **Current behavior** | `executor.py` has a dedicated branch: if the planner selected an escalate-kind action, build an EscalationPack and send an escalation notice **instead of** emailing. `traced_loop.py` has no equivalent — grep finds zero references to `.kind` or `escalation_pack`; such a selection falls through to the normal draft/judge/send path. |
| **file:line** | present `loop/executor.py:216-229`; ABSENT in `loop/traced_loop.py` |
| **Why it's a blocker** | Same planner decision produces a handoff on one path and an outbound email on the other. Reachability not yet proven — see "must confirm" below. |
| **Proposed fix** | Port the escalate-kind branch into `traced_loop.py`, then add a shared test asserting both paths behave identically for an escalate-kind selection. |

---

## MAJOR — contract clauses with nothing behind them

| # | Clause | Current behavior | file:line | Proposed fix |
|---|---|---|---|---|
| M1 | CUST-E-03 insolvency | No detection of bankruptcy / insolvency / administration / receivership / liquidation / "can't pay" **anywhere in `app/`** | ABSENT (`loop/reply_interpreter.py` regexes) | New inbound signal → immediate escalate |
| M2 | CUST-E-02 legal counsel | No inbound detection of attorney/solicitor/legal counsel/litigation. `"attorney"` exists only as an **outbound** banned word | `services/chase_guardrails.py:44` (outbound only) | New inbound signal → immediate escalate |
| M3 | CUST-E-09 asks for a human | No detection of "speak to a human" or "is this a bot" | ABSENT | New inbound signal → escalate; pairs with B3 |
| M4 | CUST-F-02 debt terms | Banned list covers `discount`, `waive`, `reduced amount/balance/payment` only. **Not** covered: extension, more time, payment plan, instalment, revised due date, credit terms, "work something out" | `services/chase_guardrails.py:42-44` | Extend `_BANNED_PATTERNS`; add tests per phrase |
| M5 | CUST-F-03 consequence threats | Covers legal action/sue/lawsuit/attorney/collections agency/credit bureau. **Not** covered: suspend service, withhold work, put account on hold, general "or else" framing | `services/chase_guardrails.py:43-44` | Extend `_BANNED_PATTERNS`; add tests |
| M6 | ENF-02 tests | **15 of 20 guardrails have no test at all** — deleting them breaks nothing. Untested: ack_context, one_concrete_ask, no_multiple_asks, advances_objective, no_repeat_failed_ask, no_unsupported_claims, no_attachment_promise, frequency_ok, escalation_policy_ok, relationship_tone, invoice_mentioned_when_needed, paid_check, opt_out_check, dispute_check, placeholder_check | tests/ (absence) | One deletion-detecting test per check |
| M7 | CUST-F-06 off-topic | No detection in the live path. An `out_of_scope_request` intent exists only in `app/services/_archived_chase/` which is **dead code** — not imported from `app/main.py` | `_archived_chase/chase_parser.py:67` (unreachable) | New signal → acknowledge + escalate, ask nothing |
| M8 | PM-A-04 / SOR-F-03 provenance | `Commitment.source` defaults to `"customer"` and `signal_ingestion.py` hardcodes `"owner": "customer"` / `"source": "customer"` even when the reply came from the PM | `domain/types.py:103`, `loop/signal_ingestion.py:120-127,146-153,163-170` | Set source from `case["target"]` at creation |

---

## MINOR

| # | Finding | file:line | Note |
|---|---|---|---|
| m1 | Customer-stated dispute writes `world.status="disputed"` as fact | `loop/signal_ingestion.py:72-74` | Owner already agreed to change to a claim (OQ-01) |
| m2 | The safe-fallback template is **never critic-checked** — `judgment` is forced to `passed=True`; only `check_before_send` gates it | `loop/executor.py:163-172`, `loop/traced_loop.py:278-291` | Templates are static so risk is low today, but the structure means any future template bug ships unchecked |
| m3 | `force_threat` — live code in a production module returning *"Pay {inv} now or we will sue and send this to a collections agency."* Not in `TACTICS`, so the planner cannot select it; reachable only if a caller passes the tactic explicitly (one test does). The banned-language guard would catch it | `loop/communication.py:116-120` | Delete or move behind a test-only seam |
| m4 | Guard/recipient-resolution order is reversed between the two send paths | `executor.py:174→195` vs `traced_loop.py:309→338` | Subsumed by B1 fix |
| m5 | `ReplyType.HANDOFF` is dead — never produced by the interpreter, and the LLM path remaps it to `"unknown"` | `domain/types.py:91`, `adapters/llm_tools.py:363-366,377` | Remove, or wire it to CUST-E-09 |

---

## MISSION-SCOPE FINDINGS — needs a business decision, not a code fix

The stated mission is "obtain a payment date, or escalate." Four objectives/tactics
sit outside that literally:

| Objective / tactic | file:line | What it actually does |
|---|---|---|
| `verify_payment` / `verify_payment_ask` | `domain/goals.py:15,31` | Asks for remittance proof after a paid-claim. Reconciliation, not date-chasing. |
| `resolve_blocker` / `blocker_ack` | `domain/goals.py:17,30` | **The significant one.** When a customer says "we can't pay until X", the agent stops asking for a payment date, stores the customer's own resolution date as a commitment, and replies *"Understood on the blocker… I'll follow up on the expected resolution date."* Combined with B4 (postponements uncapped), this is de-facto accepting an open-ended delay. |
| `suppress_collections` | `domain/goals.py:20` | Exits the loop on paid/opted-out. Reasonable, but a third action beyond "ask or escalate". |
| `dispute_route` | `domain/goals.py:34`, `communication.py:112-114` | Sends *"We've logged a dispute… and paused collections outreach"* — to the **customer** by default (not in `PM_DIRECTED_TACTICS`). Likely unreachable since disputes route to `escalate_handoff` first, but unproven. |

---

## MUST CONFIRM BEFORE FIXING (reachability not yet proven)

1. Can `selected.kind == "escalate"` actually occur on the `traced_loop.py` path? If not, B5 is latent rather than live. Requires tracing `plan_next_action`.
2. Where does a blocker's `expected_resolution` date passing get marked `missed`? Untraced — decides whether blockers have *any* backstop today.
3. Is `dispute_route` reachable at all?
