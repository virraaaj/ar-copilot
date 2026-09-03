# Fix plan: stop the recurring "agent asserts something nobody said" class of bug

Prepared 2026-08-20 after a live guided-demo review found two new instances of a
bug class that has now been patched four separate times in two weeks.

---

## 1. What AR Copilot is trying to do

AR Copilot chases overdue invoices autonomously. Per case it runs a loop:

```
run_agent_tick
  -> build_context_packet   (world + dialogue + budget + uncertainty + commitments)
  -> build_goal_stack       (choose_objective -> choose_tactic)
  -> llm_draft              (write the actual email for that tactic)
  -> critique               (guardrails; block or send)
  -> send
...reply arrives...
  -> interpret_reply        (classify into ReplyType, extract dates)
  -> apply_reply_signal     (mutate state + commitments)
```

The product promise is that every message is **grounded**: the agent only says
things that are true of the recorded case state, and it escalates to a human
when it should. A PM or customer reading an agent email must be able to trust
that "you told us X" actually happened.

## 2. The bug history — and why it keeps coming back

Every bug fixed in `goals.py` / `reply_interpreter.py` / `guided_demo.py` since
2026-08-05 is the **same shape**:

> A semantically rich signal gets collapsed into a poorer representation
> upstream, so a downstream decision that needed the discarded detail guesses —
> and the LLM fills the gap by improvising from world state.

| Date | Symptom | Fix that shipped |
|---|---|---|
| 08-05 | blocker regex swallowed plain checkbacks | narrowed the regex |
| 08-06 | checkback -> `follow_up_scheduled` -> unconditionally `obtain_commitment`, chasing the PM like a customer | added `contact_target == "pm"` guard |
| 08-06 | that guard reset live conversations to the first-touch script | added `already_engaged_waiting` carve-out |
| 08-18 | `confirm_promise` structurally unreachable (alphabetical tie-break) | `-idx` tie-break |
| 08-19 | PM handoff with no date -> `obtain_commitment` -> LLM invented a payment date from `due_date` | added `has_active_commitment` gate |
| 08-19 | double-sends in all 3 demo scenarios | removed redundant beats |
| **08-20 (now)** | **checkback commitment -> `obtain_commitment` -> LLM again invented a payment date from `due_date`** | **— this plan** |
| **08-20 (now)** | **explicit dispute classified `blocker`, never escalated** | **— this plan** |

**Every one of those fixes was a narrow conditional added at the point of
failure.** None changed the representation. `choose_objective` is now ~80 lines
of ordered conditionals with five carve-outs, and the 08-19 fix's own comment
describes exactly the bug that recurred on 08-20 — because the gate it added
(`has_active_commitment`) is a *boolean*, and the new failure needed the
*type*.

**This plan fixes representations and adds an output-grounding gate, instead of
adding a sixth conditional.**

## 3. Root causes (verified in code, not inferred)

### RC1 — commitment TYPE is erased before routing  ← causes the phantom date

`app/outcome_agent/domain/types.py:68-71` defines three distinct kinds:

```python
class CommitmentType(str, Enum):
    PAYMENT_DATE = "payment_date"
    FOLLOW_UP_DATE = "follow_up_date"
    BLOCKER_RESOLUTION_DATE = "blocker_resolution_date"
```

The store keeps that. But `app/outcome_agent/memory/context_builder.py:51-63`
flattens it:

```python
commitments = [c for c in (case.get("commitments") or []) if c.get("status") == "active"]
...
has_active_commitment=bool(commitments),      # <-- TYPE ERASED HERE
```

So `choose_objective` cannot tell "they promised to PAY on the 29th" from "they
promised to GET BACK TO US on the 29th". Both are `True`.

Causal chain for the Silverline bug:

1. Customer: *"I'll check on this and get back to you in a week"* -> `CHECKBACK`,
   creates a `follow_up_date` commitment for 2026-08-29. Correct so far.
2. `has_active_commitment` -> `True` (type erased).
3. Clock reaches 08-29, state `follow_up_scheduled`.
4. `goals.py:113-115` -> `obtain_commitment` ("Follow-up due — seek payment date").
5. `goals.py:161` -> candidates `["confirm_promise", ...]`, `confirm_promise` wins.
6. `TACTICS["confirm_promise"]` = *"Acknowledge and calendar the promise"*. The
   LLM is told to acknowledge a **payment** promise. None exists. It reaches into
   world state, finds `due_date: 2026-07-15`, and writes:
   *"I've noted that Silverline Manufacturing is expected to pay by July..."*

Nobody ever said that. The agent fabricated a commitment and asserted it back to
the customer.

### RC2 — no guardrail validates factual claims, only capability claims

`app/outcome_agent/loop/critic.py:89`:

```python
add("no_unsupported_claims", "discount" not in draft.lower() and "waive" not in draft.lower())
```

That is the entire "unsupported claims" check. `no_unsupported_promise`
(critic.py:112-142) is thorough — but only about **capabilities the system lacks**
(calls, meetings, attachments, refunds). There is **no check anywhere** that a
date or commitment asserted in a draft corresponds to a real recorded
commitment. A fabricated date passes every guardrail.

Note `app/outcome_agent/adapters/llm_tools.py:417` already puts
`active_commitments` into the planner context — the data is present at the
drafting boundary, it is simply never used as a constraint.

### RC3 — single-label reply classification loses an overriding signal

`ReplyType` forces one label. The Silverline dispute — *"we're disputing this
invoice -- the amount billed doesn't match what we agreed to"* — was classified
**`blocker` at 90% confidence** by the LLM interpreter.

`goals.py:53-58` only escalates on `world.is_disputed or state == "disputed"`, so
the dispute never reached the escalation branch. It routed to `clarify_ask` and
asked a disputing customer for a payment date.

The deterministic interpreter would have got this right —
`reply_interpreter.py:96` matches `\bdispute\b` -> `DISPUTE` at 0.9. **The LLM
underperformed the regex on the most explicit phrasing possible**, and nothing
reconciles the two.

This is not just a demo defect: continuing to dun a customer who has formally
disputed is a real relationship and compliance risk.

### RC4 — demo fixture dates are hardcoded while the sim clock moves

`app/outcome_agent/loop/guided_demo.py:152` has the customer say
*"We can commit to paying by August 18th"*, but by the time that beat runs the
sim clock is at 2026-08-29. The customer commits to a date 11 days in the past.
The agent noticed but produced a muddled `clarify_ask`, and the beat narration
still claims *"Payment comes in on schedule."*

Underlying real-world gap: **a `payment_date` commitment whose date is already
past at creation time is accepted silently.** That is a genuine production case
too (*"we paid on the 18th"* received on the 29th).

---

## 4. The fix

Ordered by value. Fix 1 and Fix 2 are the ones that end the recurrence — Fix 2
is the backstop that catches this class **even if routing is wrong again**.

### Fix 1 — thread commitment type through to routing  (root cause)

**`domain/goals.py`**

- Replace the `has_active_commitment: bool` parameter on `choose_objective` and
  `build_goal_stack` with `active_commitment_type: Optional[str]` (values from
  `CommitmentType`). Keep a `has_active_commitment` derived local
  (`active_commitment_type is not None`) so existing branch logic reads the same
  where type genuinely does not matter.
- Route by type:
  - `payment_date` -> `obtain_commitment` (this is the only case where
    `confirm_promise` is legitimate).
  - `follow_up_date` that is now due -> **not** `obtain_commitment`. The correct
    move is "you said you'd come back to us by X — any update?" Use
    `establish_contact` with a new tactic (below), or a new objective if that
    reads cleaner. Do **not** route to `confirm_promise`.
  - `blocker_resolution_date` -> `resolve_blocker`.
- Add a tactic `followup_nudge` to `TACTICS`:
  *"Follow up on a promised check-back date without asserting a payment date"*,
  and make it the top candidate for the follow-up-due path.

**`memory/context_builder.py:51-63`** — pass the type through instead of `bool(...)`.
Pick the most relevant active commitment when several exist (prefer
`payment_date` > `blocker_resolution_date` > `follow_up_date`).

**Hard invariant to enforce in `choose_tactic`:** `confirm_promise` must be
unreachable unless an active `payment_date` commitment exists. Enforce it
structurally, not by comment.

### Fix 2 — ground drafts against recorded commitments  (the backstop)

**`loop/critic.py`** — new check `no_fabricated_commitment`:

- Extract date assertions from the draft (both `YYYY-MM-DD` and prose forms like
  "July 15", "August 20th" — reuse / factor out `reply_interpreter._extract_date`
  rather than writing a second date parser).
- Detect commitment-attribution language ("expected to pay by", "you committed
  to", "you indicated you would pay", "I've noted that ... will pay").
- **Fail** if the draft attributes a payment commitment to the counterparty and
  there is no active `payment_date` commitment whose date matches.
- Add `"no_fabricated_commitment"` to `_SAFETY_CRITICAL_CHECKS` in
  `adapters/llm_tools.py:30-37` so the LLM judge cannot wave it through.

**`adapters/llm_tools.py` draft prompt** — state the constraint explicitly:
the draft may only reference dates present in `active_commitments`; it must never
present the invoice `due_date` as something the counterparty promised.

This check alone would have caught the Silverline email regardless of routing.
Prefer a false block over a fabricated assertion.

### Fix 3 — dispute must win

- In the interpreter path, when high-confidence lexical dispute markers are
  present (`reply_interpreter.py:96`'s pattern), that classification **overrides**
  a conflicting LLM label. Same philosophy already used for
  `_SAFETY_CRITICAL_CHECKS`: for safety-critical signals the deterministic check
  is authoritative.
- Alternatively/additionally carry dispute as an overriding flag on
  `InterpretedReply` alongside the primary type, and have `apply_reply_signal`
  set `state = "disputed"` whenever it is set. Either is acceptable; the invariant
  is what matters:

  > **A reply containing explicit dispute language always ends up at
  > `escalate_handoff`, never at a tactic that asks for payment.**

### Fix 4 — dates relative to the clock; reject past-dated promises

- `guided_demo.py:152` — make the committed date relative to the sim clock
  instead of the literal `"August 18th"`, and fix the beat narration so it
  matches what actually happens.
- In `apply_reply_signal`, a `payment_date` commitment whose date is already past
  at creation should be flagged rather than silently accepted — treat as needing
  clarification ("did you already send it, or did you mean a future date?").
  That is genuinely the right real-world behaviour, not just a demo fix.

### Fix 5 — encode the invariants as tests, not just the scenarios

`tests/outcome_agent/` already covers behaviour (54 passing). Add
**invariant** tests — these are the things that keep regressing:

1. `confirm_promise` is never selected without an active `payment_date` commitment
   (property test across all states x commitment types).
2. No draft asserts a payment date absent from active commitments —
   assert `no_fabricated_commitment` fires on the exact Silverline draft text.
3. Explicit dispute language always routes to `escalate_handoff` —
   parametrise over several phrasings, and assert the LLM label cannot override it.
4. A `follow_up_date` commitment coming due never routes to `obtain_commitment`.

Scenario tests verify *this* transcript. Invariant tests verify *the class*.
That difference is why this bug came back.

---

## 5. Constraints for whoever implements this

- **Baseline: `.venv/Scripts/python.exe -m pytest tests/outcome_agent/ -q` is 54
  passed.** It must still be 54+ passed and 0 failed when done. Use that
  interpreter — the system `py` lacks `aiosqlite` and cannot load the conftest.
  (`tests/` at large has 30 pre-existing collection errors unrelated to this
  work; scope test runs to `tests/outcome_agent/`.)
- Do not add another conditional to `choose_objective` as the primary fix. If a
  new branch is genuinely needed, it must be justified by the type now flowing
  through, not by another special case.
- Match the existing comment style: these files document *why* a fix exists and
  what regression it prevents, with the date it was found. Keep that — it is how
  this history stayed reconstructible. Reference this file.
- Guided demo scenarios 1-3 must still run end to end. Scenario 2 must now
  actually reach escalation (its stated title is "escalated to a human"), and
  must not contain a fabricated payment date anywhere in its transcript.
