"""
Multi-trip chase demo against Azure Postgres + Cosmos Gremlin.

Shows memory buildup for ONE invoice across trips:
  T0 seed → T1 soft nudge → T2 PO blocker reply → T3 promise → T4 learning score

Usage (from repo root, az logged in):
  python scripts/azure/multi_trip_chase_demo.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.outcome_agent.azure.cosmos_gremlin import CosmosGremlinGraph
from app.outcome_agent.azure.outbox import flush_graph_outbox
from app.outcome_agent.azure.pg_store import PostgresMemoryStore
from app.outcome_agent.azure.secrets import AzureMemorySettings, dump_settings_public

INVOICE = "INV-DEMO-001"


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _print_trip(title: str, snap: dict, graph_nb: dict | None = None) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)
    case = snap["case"]
    print(f"STATE: {case['state']}")
    print(f"COMMITMENTS: {json.dumps(case['commitments'], indent=2)}")
    print(f"BLOCKERS:    {json.dumps(case['blockers'], indent=2)}")
    print(f"EVENTS ({len(snap['events'])}):")
    for e in snap["events"]:
        print(f"  - {e['at'][:19]}  {e['kind']:22}  {json.dumps(e['detail'])[:100]}")
    print(f"FACTS ({len(snap['facts'])}):")
    for f in snap["facts"]:
        print(f"  - {f['kind']}/{f['key']} = {f['value']}")
    print(f"MAILBOX ({len(snap['mailbox'])}):")
    for m in snap["mailbox"]:
        print(f"  - {m['direction']:8} {m['subject'][:60]}")
    print(f"TACTIC WEIGHTS: {snap['tactic_weights']}")
    print(f"GRAPH OUTBOX PENDING: {snap['pending_outbox']}")
    if graph_nb is not None:
        print(f"GREMLIN OUT-EDGES: {json.dumps(graph_nb.get('out_edges'), default=str)[:400]}")


async def main() -> None:
    settings = AzureMemorySettings.from_env_or_vault()
    print("Azure settings:")
    print(dump_settings_public(settings))

    pg = PostgresMemoryStore(settings.database_url)
    graph = CosmosGremlinGraph(
        settings.gremlin_host,
        settings.gremlin_username,
        settings.gremlin_password,
    )

    print("\n[1] Applying Postgres schema…")
    await pg.apply_schema()
    print("    schema ok")

    print("[2] Gremlin healthcheck…")
    print("   ", await asyncio.to_thread(graph.healthcheck))

    print(f"[3] Reset demo case {INVOICE}…")
    await pg.wipe_demo_case(INVOICE)

    # ---- T0 seed ----
    case = await pg.create_case(
        invoice_no=INVOICE,
        customer_name="Acme Manufacturing",
        amount=18450.0,
        state="overdue",
    )
    case_id = case["id"]
    inv_node = f"invoice:{INVOICE}"
    await pg.append_event(case_id, "seed", {"invoice": INVOICE, "amount": 18450.0}, ["P1", "P12"])
    await pg.enqueue_graph(
        "upsert_vertex",
        {"id": inv_node, "label": "Invoice", "pk": INVOICE, "props": {"amount": "18450"}},
    )
    flush = await flush_graph_outbox(pg, graph)
    snap = await pg.snapshot(case_id)
    nb = await asyncio.to_thread(graph.neighborhood, inv_node, INVOICE)
    _print_trip("T0 SEED — case created in Postgres + invoice vertex in Gremlin", snap, nb)
    print("outbox flush:", flush)

    # ---- T1 soft nudge ----
    ask_id = str(uuid.uuid4())
    subject = f"[{case['subject_token']}] Invoice {INVOICE} — payment date?"
    body = (
        f"Hi Acme team,\n\nCould you confirm a payment date for {INVOICE} "
        f"(${case['amount']:,.2f})?\n\nThanks,\nCollections"
    )
    await pg.add_mailbox(case_id, "outbound", subject, body)
    await pg.append_event(
        case_id,
        "outbound_email",
        {"ask_id": ask_id, "tactic": "soft_nudge", "subject": subject},
        ["P2", "P12"],
    )
    await pg.upsert_fact(case_id, "outreach", "last_ask_tactic", "soft_nudge")
    await pg.upsert_fact(case_id, "outreach", "last_ask_id", ask_id)
    case = await pg.update_case(
        case_id,
        state="waiting_for_customer",
        dialogue={"latest_outbound": body, "last_ask_id": ask_id, "last_ask_tactic": "soft_nudge"},
        last_outreach_at=_now(),
        last_decision={"tactic": "soft_nudge", "objective": "obtain_payment_date"},
    )
    snap = await pg.snapshot(case_id)
    _print_trip("T1 AGENT OUTREACH — soft_nudge email (operational + episodic + semantic)", snap)

    # ---- T2 customer: needs PO (blocker) ----
    reply1 = "We need the PO number on the invoice before AP can schedule payment."
    await pg.add_mailbox(case_id, "inbound", f"Re: {subject}", reply1)
    await pg.append_event(case_id, "inbound_email", {"text": reply1}, ["P1", "P6"])
    blocker = {
        "id": str(uuid.uuid4()),
        "type": "missing_po",
        "status": "open",
        "detail": "Customer needs PO on invoice",
    }
    await pg.upsert_fact(case_id, "reply", "last_reply_type", "needs_info")
    await pg.upsert_fact(case_id, "blocker", "open_blocker", blocker)
    case = await pg.update_case(
        case_id,
        state="blocked",
        blockers=[blocker],
        dialogue={**case["dialogue"], "latest_inbound": reply1},
        goals={"current_objective": "clear_blocker"},
    )
    await pg.append_event(case_id, "blocker_opened", blocker, ["P6", "P7"])
    blk_node = f"blocker:{blocker['id']}"
    await pg.enqueue_graph(
        "supersede_edge",
        {
            "from_id": inv_node,
            "rel": "INVOICE_BLOCKED_BY",
            "to_id": blk_node,
            "valid_from": _now(),
            "pk": INVOICE,
            "to_label": "Blocker",
            "to_props": {"type": "missing_po"},
        },
    )
    flush = await flush_graph_outbox(pg, graph)
    snap = await pg.snapshot(case_id)
    nb = await asyncio.to_thread(graph.neighborhood, inv_node, INVOICE)
    _print_trip("T2 CUSTOMER REPLY — missing PO blocker (facts + graph edge)", snap, nb)
    print("outbox flush:", flush)

    # ---- T3 customer: promise after PO cleared ----
    reply2 = "PO 998877 is on file. We can pay on 2026-08-15."
    await pg.add_mailbox(case_id, "inbound", f"Re: {subject}", reply2)
    await pg.append_event(case_id, "inbound_email", {"text": reply2}, ["P1", "P6"])
    blocker["status"] = "cleared"
    commitment = {
        "id": str(uuid.uuid4()),
        "date": "2026-08-15",
        "status": "active",
        "source": "customer_email",
        "amount": case["amount"],
    }
    await pg.upsert_fact(case_id, "reply", "last_reply_type", "promise_to_pay")
    await pg.upsert_fact(case_id, "commitment", "active_commitment", commitment)
    await pg.upsert_fact(case_id, "po", "po_number", "998877")
    await pg.upsert_fact(case_id, "blocker", "open_blocker", blocker)
    case = await pg.update_case(
        case_id,
        state="promise_to_pay",
        blockers=[blocker],
        commitments=[commitment],
        dialogue={**case["dialogue"], "latest_inbound": reply2},
        goals={"current_objective": "confirm_payment"},
    )
    await pg.append_event(case_id, "commitment_recorded", commitment, ["P2", "P6"])
    # close blocker edge, open commitment edge
    await pg.enqueue_graph(
        "supersede_edge",
        {
            "from_id": inv_node,
            "rel": "INVOICE_BLOCKED_BY",
            "to_id": blk_node,
            "valid_from": _now(),
            "pk": INVOICE,
            "to_label": "Blocker",
            "to_props": {"type": "missing_po", "status": "cleared"},
        },
    )
    # Immediately close that edge as cleared by setting valid_to via another supersede to commitment
    cmt_node = f"commitment:{commitment['id']}"
    await pg.enqueue_graph(
        "supersede_edge",
        {
            "from_id": inv_node,
            "rel": "INVOICE_HAS_COMMITMENT",
            "to_id": cmt_node,
            "valid_from": _now(),
            "pk": INVOICE,
            "to_label": "Commitment",
            "to_props": {"date": "2026-08-15"},
        },
    )
    # score prior soft_nudge ask as got_commitment
    learn = await pg.record_learning(
        ask_id, case_id, tactic="soft_nudge", outcome="got_commitment", objective="obtain_payment_date"
    )
    flush = await flush_graph_outbox(pg, graph)
    snap = await pg.snapshot(case_id)
    nb = await asyncio.to_thread(graph.neighborhood, inv_node, INVOICE)
    _print_trip(
        "T3 PROMISE — commitment + PO fact + learning credit (graph supersession)",
        snap,
        nb,
    )
    print("learning:", learn, "outbox flush:", flush)

    # ---- T4 confirmation outbound ----
    ack = (
        f"Thanks — confirming payment date 2026-08-15 for {INVOICE} "
        f"(PO 998877). We'll follow up if not received."
    )
    await pg.add_mailbox(case_id, "outbound", f"Re: {subject}", ack)
    await pg.append_event(
        case_id,
        "outbound_email",
        {"tactic": "confirm_commitment", "subject": f"Re: {subject}"},
        ["P2", "P12"],
    )
    await pg.upsert_fact(case_id, "outreach", "last_ask_tactic", "confirm_commitment")
    case = await pg.update_case(
        case_id,
        state="promise_to_pay",
        dialogue={**case["dialogue"], "latest_outbound": ack, "last_ask_tactic": "confirm_commitment"},
        last_outreach_at=_now(),
    )
    snap = await pg.snapshot(case_id)
    nb = await asyncio.to_thread(graph.neighborhood, inv_node, INVOICE)
    _print_trip("T4 CONFIRMATION — second outbound; memory fully built for this invoice", snap, nb)

    print("\n" + "#" * 72)
    print("DONE — one invoice, four trips. Stores used:")
    print("  Postgres: oa_cases, oa_events, oa_memory_facts, oa_learning, oa_tactic_weights, oa_mailbox_messages, oa_graph_outbox")
    print("  Cosmos Gremlin: invoice / blocker / commitment vertices + edges")
    print("#" * 72)

    await asyncio.to_thread(graph.close)
    await pg.close()


if __name__ == "__main__":
    asyncio.run(main())
