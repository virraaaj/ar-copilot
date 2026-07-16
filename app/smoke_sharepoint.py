"""
Phase 1B smoke test: authenticate to Graph and list the configured
SharePoint folder for real.

As of 2026-07-16 this fails with AADSTS7000215 (invalid client secret) — the
secret copied from Lummus's dev-viraj .env.backup has expired/is invalid.
Re-run this once a fresh SHAREPOINT_CLIENT_SECRET is in .env to confirm the
connector actually works; no code changes needed.

Run: python -m app.smoke_sharepoint
"""
from __future__ import annotations

import asyncio

from app.documents.sources.sharepoint import SharePointSource


async def main() -> None:
    async with SharePointSource() as source:
        docs = await source.list_documents()
        print(f"Auth OK. Found {len(docs)} file(s) in the configured SharePoint folder.")
        for d in docs[:10]:
            print(f"  - {d.filename} ({d.size_bytes} bytes, id={d.source_id[:12]}...)")


if __name__ == "__main__":
    asyncio.run(main())
