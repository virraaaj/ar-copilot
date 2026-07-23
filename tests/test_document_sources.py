"""
Phase 1B tests: document sources.

ManualUploadSource needs no mocking — it's real local-disk I/O, tested
against a temp directory. SharePointSource is tested against a mocked Graph
API (respx) — its real-credential path is verified separately by
app/smoke_sharepoint.py, not here, since that needs a live network call.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from app.documents.sources.manual_upload import ManualUploadSource
from app.documents.sources.sharepoint import SharePointError, SharePointSource


@pytest.mark.asyncio
async def test_manual_upload_save_then_list(tmp_path):
    source = ManualUploadSource(base_dir=str(tmp_path))

    ref = await source.save("cert.pdf", b"pdf bytes here", doc_type="vendor_certification", project_number="PN-1")

    assert ref.source == "manual_upload"
    assert ref.filename == "cert.pdf"
    assert ref.doc_type == "vendor_certification"
    assert ref.size_bytes == len(b"pdf bytes here")
    assert ref.project_number == "PN-1"

    listed = await source.list_documents()
    assert len(listed) == 1
    assert listed[0].filename == "cert.pdf"


@pytest.mark.asyncio
async def test_manual_upload_list_filters_by_doc_type(tmp_path):
    source = ManualUploadSource(base_dir=str(tmp_path))
    await source.save("cert.pdf", b"a", doc_type="vendor_certification", project_number="PN-1")
    await source.save("manual.pdf", b"b", doc_type="equipment_manual", project_number="PN-1")

    certs = await source.list_documents(doc_type="vendor_certification")

    assert len(certs) == 1
    assert certs[0].filename == "cert.pdf"


@pytest.mark.asyncio
async def test_manual_upload_list_filters_by_project(tmp_path):
    """Two different projects can each have a file named the same thing --
    no collision on disk or in the metadata sidecar -- and listing one
    project's folder never leaks the other's documents."""
    source = ManualUploadSource(base_dir=str(tmp_path))
    await source.save("report.pdf", b"project one's report", project_number="PN-1")
    await source.save("report.pdf", b"project two's report", project_number="PN-2")

    pn1_docs = await source.list_documents(project_number="PN-1")
    pn2_docs = await source.list_documents(project_number="PN-2")

    assert len(pn1_docs) == 1 and len(pn2_docs) == 1
    assert await source.fetch(pn1_docs[0].source_id) == b"project one's report"
    assert await source.fetch(pn2_docs[0].source_id) == b"project two's report"


@pytest.mark.asyncio
async def test_manual_upload_no_project_lands_in_unfiled_folder(tmp_path):
    source = ManualUploadSource(base_dir=str(tmp_path))
    ref = await source.save("legacy.pdf", b"data")

    assert ref.project_number is None
    unfiled = await source.list_documents(project_number="_unfiled")
    assert len(unfiled) == 1
    assert unfiled[0].filename == "legacy.pdf"


@pytest.mark.asyncio
async def test_manual_upload_fetch_roundtrips_bytes(tmp_path):
    source = ManualUploadSource(base_dir=str(tmp_path))
    ref = await source.save("doc.pdf", b"exact bytes", doc_type=None, project_number="PN-1")

    fetched = await source.fetch(ref.source_id)

    assert fetched == b"exact bytes"


@pytest.mark.asyncio
async def test_manual_upload_fetch_missing_raises(tmp_path):
    source = ManualUploadSource(base_dir=str(tmp_path))

    with pytest.raises(FileNotFoundError):
        await source.fetch("nope.pdf")


@pytest.mark.asyncio
async def test_manual_upload_survives_reopen(tmp_path):
    """Metadata persists across process restarts (it's a file, not memory)."""
    source1 = ManualUploadSource(base_dir=str(tmp_path))
    await source1.save("doc.pdf", b"data", doc_type="quote", project_number="PN-1")

    source2 = ManualUploadSource(base_dir=str(tmp_path))
    listed = await source2.list_documents()

    assert len(listed) == 1
    assert listed[0].doc_type == "quote"


# --------------------------------------------------------------------------- SharePoint (mocked)

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"


@pytest.fixture
def sharepoint(monkeypatch) -> SharePointSource:
    monkeypatch.setenv("SHAREPOINT_TENANT_ID", "tenant-1")
    monkeypatch.setenv("SHAREPOINT_CLIENT_ID", "client-1")
    monkeypatch.setenv("SHAREPOINT_CLIENT_SECRET", "secret-1")
    monkeypatch.setenv("SHAREPOINT_DRIVE_ID", "drive-1")
    monkeypatch.setenv("SHAREPOINT_FOLDER_ID", "folder-1")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    from app.config import Settings
    import app.config as config_module

    # setattr (not a bare assignment) so monkeypatch reverts this after the
    # test, instead of leaking a mutated singleton into later tests.
    monkeypatch.setattr(config_module, "_settings", Settings())
    return SharePointSource()


@pytest.mark.asyncio
@respx.mock
async def test_sharepoint_list_documents(sharepoint: SharePointSource) -> None:
    respx.post(f"{LOGIN}/tenant-1/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json={"access_token": "graph-token"})
    )
    respx.get(f"{GRAPH}/drives/drive-1/items/folder-1/children").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {"id": "item-1", "name": "spec.pdf", "file": {}, "size": 1234, "lastModifiedDateTime": "2026-01-01T00:00:00Z"},
                    {"id": "item-2", "name": "Subfolder"},  # no "file" key -- a folder, must be skipped
                ]
            },
        )
    )

    docs = await sharepoint.list_documents()

    assert len(docs) == 1
    assert docs[0].filename == "spec.pdf"
    assert docs[0].source == "sharepoint"
    await sharepoint.close()


@pytest.mark.asyncio
@respx.mock
async def test_sharepoint_auth_failure_raises_clear_error(sharepoint: SharePointSource) -> None:
    respx.post(f"{LOGIN}/tenant-1/oauth2/v2.0/token").mock(
        return_value=httpx.Response(401, json={"error": "invalid_client", "error_description": "bad secret"})
    )

    with pytest.raises(SharePointError, match="Graph auth failed"):
        await sharepoint.list_documents()
    await sharepoint.close()


@pytest.mark.asyncio
@respx.mock
async def test_sharepoint_fetch_downloads_bytes(sharepoint: SharePointSource) -> None:
    respx.post(f"{LOGIN}/tenant-1/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json={"access_token": "graph-token"})
    )
    respx.get(f"{GRAPH}/drives/drive-1/items/item-1/content").mock(
        return_value=httpx.Response(200, content=b"pdf file bytes")
    )

    content = await sharepoint.fetch("item-1")

    assert content == b"pdf file bytes"
    await sharepoint.close()
