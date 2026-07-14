"""
Phase 0 smoke test: logs into the local UAT backend and lists cases.

Run: python -m app.smoke
"""
from __future__ import annotations

import asyncio

from app.services.backend_client import get_backend_client


async def main() -> None:
    client = get_backend_client()
    try:
        cases = await client.list_cases(limit=5)
        print(f"Login OK. Backend returned {len(cases)} case(s).")
        for c in cases[:5]:
            print(f"  - {c.get('id', '?')}: stage={c.get('current_stage_code', '?')} status={c.get('case_status', '?')}")

        bus = await client.list_business_units()
        print(f"Business units: {len(bus)}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
