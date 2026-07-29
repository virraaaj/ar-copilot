"""Day-0 demo seed — sim date 2026-07-22, six showcase cases."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

SEED_SIM_DATE = datetime(2026, 7, 22, 9, 0, 0)

DAY0_CASES: List[Dict[str, Any]] = [
    {
        "case_id": "case-inv-4821",
        "case_key": "INV-4821",
        "invoice_no": "INV-4821",
        "project_number": "ACME-100",
        "customer_name": "Acme Industrial",
        "state": "blocked",
        "amount": 18400.0,
        "pm_email": "pm.acme@example.com",
        "customer_email": "ap.acme@example.com",
        "target": "customer",
        "next_action_at": "2026-07-28T09:00:00",
        "world": {
            "invoice_no": "INV-4821",
            "case_id": "case-inv-4821",
            "balance_due": 18400.0,
            "status": "open",
            "due_date": "2026-06-15",
            "consent_ok": True,
            "internal_owner": "pm.acme@example.com",
            "risk": "medium",
            "customer_name": "Acme Industrial",
            "project_number": "ACME-100",
            "amount_original": 18400.0,
            "source": "seed",
        },
        "dialogue": {
            "latest_inbound": "Travel approvals are holding payment — expect clearance next week.",
            "latest_outbound": "Checking on INV-4821 status.",
            "reply_type": "blocker",
            "sentiment": "cooperative",
            "open_questions": ["When will travel approval clear?"],
        },
        "budget": {
            "max_unanswered": 3,
            "unanswered_used": 0,
            "max_postponements": 3,
            "postponements_used": 1,
            "max_missed_promises": 3,
            "misses_used": 0,
            "min_days_between_emails": 3,
        },
        "goals": {
            "primary_outcome": "Collect outstanding balance while preserving relationship",
            "current_objective": "resolve_blocker",
            "selected_tactic": "blocker_ack",
            "objective_rationale": "Active travel-approval blocker",
        },
        "commitments": [
            {
                "id": "cmt-4821-fu",
                "case_id": "case-inv-4821",
                "type": "follow_up_date",
                "date": "2026-07-28",
                "owner": "customer",
                "status": "active",
                "source": "customer",
                "confidence": 0.75,
                "miss_consequence": "re-engage for payment date",
            }
        ],
        "blockers": [
            {
                "id": "blk-4821",
                "case_id": "case-inv-4821",
                "type": "travel_approval",
                "owner": "customer",
                "description": "Travel approvals holding payment",
                "expected_resolution": "2026-07-28",
                "status": "open",
            }
        ],
        "graph": {
            "invoice_node": "invoice:INV-4821",
            "customer_node": "customer:ACME-100",
            "edges": [
                {
                    "from": "invoice:INV-4821",
                    "rel": "INVOICE_BLOCKED_BY",
                    "to": "blocker:blk-4821",
                    "active": True,
                },
                {
                    "from": "invoice:INV-4821",
                    "rel": "INVOICE_HAS_COMMITMENT",
                    "to": "commitment:cmt-4821-fu",
                    "active": True,
                },
            ],
        },
    },
    {
        "case_id": "case-inv-7104",
        "case_key": "INV-7104",
        "invoice_no": "INV-7104",
        "project_number": "NORTH-200",
        "customer_name": "Northstar Logistics",
        "state": "waiting_for_customer",
        "amount": 9200.0,
        "pm_email": "pm.north@example.com",
        "customer_email": "ap.northstar@example.com",
        "target": "customer",
        "next_action_at": "2026-07-25T09:00:00",
        "world": {
            "invoice_no": "INV-7104",
            "case_id": "case-inv-7104",
            "balance_due": 9200.0,
            "status": "open",
            "due_date": "2026-06-01",
            "consent_ok": True,
            "risk": "high",
            "customer_name": "Northstar Logistics",
            "project_number": "NORTH-200",
            "amount_original": 9200.0,
            "source": "seed",
        },
        "dialogue": {
            "latest_outbound": "Following up on overdue INV-7104 ($9,200).",
            "open_questions": ["Is payment scheduled?"],
        },
        "budget": {
            "max_unanswered": 3,
            "unanswered_used": 1,
            "max_postponements": 3,
            "postponements_used": 0,
            "max_missed_promises": 3,
            "misses_used": 0,
            "min_days_between_emails": 3,
        },
        "goals": {
            "current_objective": "establish_contact",
            "selected_tactic": "soft_nudge",
            "objective_rationale": "Overdue with no commitment",
        },
        "commitments": [],
        "blockers": [],
    },
    {
        "case_id": "case-inv-8890",
        "case_key": "INV-8890",
        "invoice_no": "INV-8890",
        "project_number": "RIVER-300",
        "customer_name": "Riverbend Energy",
        "state": "promise_to_pay",
        "amount": 27500.0,
        "pm_email": "pm.river@example.com",
        "customer_email": "ap.riverbend@example.com",
        "target": "customer",
        "next_action_at": "2026-07-24T09:00:00",
        "world": {
            "invoice_no": "INV-8890",
            "case_id": "case-inv-8890",
            "balance_due": 27500.0,
            "status": "open",
            "due_date": "2026-05-20",
            "consent_ok": True,
            "risk": "high",
            "customer_name": "Riverbend Energy",
            "project_number": "RIVER-300",
            "amount_original": 27500.0,
            "source": "seed",
        },
        "dialogue": {
            "latest_inbound": "We'll pay by July 23.",
            "latest_outbound": "Thanks — I'll track July 23 for INV-8890.",
            "reply_type": "payment_date",
            "customer_promised_date": "2026-07-23",
            "last_ask_id": "ask-8890-1",
            "last_ask_tactic": "confirm_promise",
            "sentiment": "cooperative",
        },
        "budget": {
            "max_unanswered": 3,
            "unanswered_used": 0,
            "max_postponements": 3,
            "postponements_used": 0,
            "max_missed_promises": 3,
            "misses_used": 0,
            "min_days_between_emails": 3,
        },
        "goals": {
            "current_objective": "obtain_commitment",
            "selected_tactic": "confirm_promise",
            "objective_rationale": "Active promise at risk",
        },
        "commitments": [
            {
                "id": "cmt-8890",
                "case_id": "case-inv-8890",
                "type": "payment_date",
                "date": "2026-07-23",
                "owner": "customer",
                "status": "active",
                "source": "customer",
                "confidence": 0.85,
                "miss_consequence": "miss judged → reflexion + weight Δ",
            }
        ],
        "blockers": [],
    },
    {
        "case_id": "case-inv-2333",
        "case_key": "INV-2333",
        "invoice_no": "INV-2333",
        "project_number": "COBALT-400",
        "customer_name": "Cobalt Systems",
        "state": "waiting_for_customer",
        "amount": 41000.0,
        "pm_email": "pm.cobalt@example.com",
        "customer_email": "ap.cobalt@example.com",
        "target": "customer",
        "next_action_at": "2026-07-22T10:00:00",
        "world": {
            "invoice_no": "INV-2333",
            "case_id": "case-inv-2333",
            "balance_due": 41000.0,
            "status": "open",
            "due_date": "2026-04-10",
            "consent_ok": True,
            "risk": "critical",
            "customer_name": "Cobalt Systems",
            "project_number": "COBALT-400",
            "amount_original": 41000.0,
            "source": "seed",
        },
        "dialogue": {
            "latest_outbound": "Third follow-up on INV-2333 — please advise.",
            "open_questions": ["Any update?"],
            "last_ask_id": "ask-2333-3",
            "last_ask_tactic": "firm_reminder",
        },
        "budget": {
            "max_unanswered": 3,
            "unanswered_used": 2,
            "max_postponements": 3,
            "postponements_used": 0,
            "max_missed_promises": 3,
            "misses_used": 0,
            "min_days_between_emails": 3,
            "relationship_risk": 0.45,
        },
        "goals": {
            "current_objective": "establish_contact",
            "selected_tactic": "firm_reminder",
            "objective_rationale": "Near escalation — one unanswered left",
        },
        "commitments": [],
        "blockers": [],
    },
    {
        "case_id": "case-inv-4555",
        "case_key": "INV-4555",
        "invoice_no": "INV-4555",
        "project_number": "ACME-100",
        "customer_name": "Acme Industrial",
        "state": "paid",
        "amount": 0.0,
        "pm_email": "pm.acme@example.com",
        "customer_email": "ap.acme@example.com",
        "target": "customer",
        "next_action_at": None,
        "world": {
            "invoice_no": "INV-4555",
            "case_id": "case-inv-4555",
            "balance_due": 0.0,
            "status": "paid",
            "due_date": "2026-05-01",
            "consent_ok": True,
            "risk": "low",
            "customer_name": "Acme Industrial",
            "project_number": "ACME-100",
            "amount_original": 5600.0,
            "paid_at": "2026-07-18",
            "source": "seed",
        },
        "dialogue": {
            "latest_inbound": "Payment sent last week.",
            "sentiment": "positive",
        },
        "budget": {"max_unanswered": 3, "unanswered_used": 0},
        "goals": {
            "current_objective": "suppress_collections",
            "selected_tactic": "dispute_route",
            "objective_rationale": "Paid — no outreach",
        },
        "commitments": [],
        "blockers": [],
    },
    {
        "case_id": "case-inv-6001",
        "case_key": "INV-6001",
        "invoice_no": "INV-6001",
        "project_number": "NORTH-200",
        "customer_name": "Northstar Logistics",
        "state": "customer_responded",
        "amount": 15300.0,
        "pm_email": "pm.north@example.com",
        "customer_email": "ap.northstar@example.com",
        "target": "customer",
        "next_action_at": "2026-07-22T11:00:00",
        "world": {
            "invoice_no": "INV-6001",
            "case_id": "case-inv-6001",
            "balance_due": 15300.0,
            "status": "open",
            "due_date": "2026-06-20",
            "consent_ok": True,
            "risk": "medium",
            "customer_name": "Northstar Logistics",
            "project_number": "NORTH-200",
            "amount_original": 15300.0,
            "source": "seed",
        },
        "dialogue": {
            "latest_inbound": "We need to review the line items on this invoice.",
            "awaiting_interpretation": True,
            "interpretation_confidence": 0.6,
            "open_questions": ["Is this a dispute?"],
            "sentiment": "frustrated",
        },
        "budget": {
            "max_unanswered": 3,
            "unanswered_used": 0,
            "max_postponements": 3,
            "postponements_used": 0,
            "max_missed_promises": 3,
            "misses_used": 0,
        },
        "goals": {
            "current_objective": "clarify_date",
            "selected_tactic": "clarify_ask",
            "objective_rationale": "Reply awaiting interpretation",
        },
        "commitments": [],
        "blockers": [],
    },
]


async def load_day0_seed(case_store, ledger, graph_store=None) -> List[str]:
    """Wipe is caller's responsibility. Returns created case row ids."""
    from app.services import sim_clock

    await sim_clock.set_simulated_at(SEED_SIM_DATE, case_store.db_path)
    ids: List[str] = []
    for spec in DAY0_CASES:
        row_id = await case_store.create(
            spec["case_id"],
            case_key=spec.get("case_key"),
            invoice_no=spec.get("invoice_no"),
            project_number=spec.get("project_number"),
            customer_name=spec.get("customer_name"),
            state=spec["state"],
            next_action_at=spec.get("next_action_at"),
            amount=spec.get("amount"),
            world=spec.get("world"),
            dialogue=spec.get("dialogue"),
            budget=spec.get("budget"),
            goals=spec.get("goals"),
            commitments=spec.get("commitments"),
            blockers=spec.get("blockers"),
            pm_email=spec.get("pm_email"),
            customer_email=spec.get("customer_email"),
            target=spec.get("target"),
        )
        ids.append(row_id)
        await ledger.append(
            row_id,
            "seed_loaded",
            {"invoice_no": spec.get("invoice_no"), "state": spec["state"]},
            at=SEED_SIM_DATE.isoformat(),
            principles=["P12"],
        )
        if graph_store is not None:
            inv = f"invoice:{spec['invoice_no']}"
            cust = f"customer:{spec.get('project_number')}"
            await graph_store.upsert_node(
                cust, "Customer", label=spec.get("customer_name")
            )
            await graph_store.upsert_node(
                inv,
                "Invoice",
                label=spec.get("invoice_no"),
                attributes={"balance_due": (spec.get("world") or {}).get("balance_due")},
            )
            await graph_store.add_edge(cust, "CUSTOMER_HAS_INVOICE", inv)
            for blk in spec.get("blockers") or []:
                if blk.get("status") == "open":
                    bid = f"blocker:{blk['id']}"
                    await graph_store.upsert_node(
                        bid, "Blocker", label=blk.get("description")
                    )
                    await graph_store.add_edge(inv, "INVOICE_BLOCKED_BY", bid)
            for cmt in spec.get("commitments") or []:
                if cmt.get("status") == "active":
                    cid = f"commitment:{cmt['id']}"
                    await graph_store.upsert_node(
                        cid, "Commitment", label=cmt.get("date"), attributes=cmt
                    )
                    await graph_store.add_edge(inv, "INVOICE_HAS_COMMITMENT", cid)
    return ids
