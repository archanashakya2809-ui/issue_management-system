from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.services import attachment_service


class _CountResult:
    def scalar_one(self) -> int:
        return 0


class _InsertResult:
    def __init__(self, row: dict):
        self.row = row

    def mappings(self) -> "_InsertResult":
        return self

    def one(self) -> dict:
        return self.row


class _DownloadResult:
    def __init__(self, row: dict | None):
        self.row = row

    def mappings(self) -> "_DownloadResult":
        return self

    def one_or_none(self) -> dict | None:
        return self.row


class _FakeDB:
    def __init__(self, insert_row: dict | None = None, download_row: dict | None = None):
        self.insert_row = insert_row or {}
        self.download_row = download_row
        self.insert_parameters: dict | None = None
        self.committed = False
        self.rolled_back = False

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if "COUNT(*)" in sql:
            return _CountResult()
        if "INSERT INTO public.ticket_attachments" in sql:
            self.insert_parameters = parameters
            return _InsertResult(self.insert_row)
        return _DownloadResult(self.download_row)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


def test_direct_upload_records_not_scanned_and_returns_metadata(tmp_path: Path, monkeypatch) -> None:
    company_id = uuid4()
    ticket_id = uuid4()
    user_id = uuid4()
    staging = tmp_path / ".staging"
    staging.mkdir()
    temporary_path = staging / "test-upload.part"
    temporary_path.write_bytes(b"%PDF-1.7\nfile content")
    final_path = tmp_path / str(company_id) / str(ticket_id) / "stored.pdf"

    prepared = attachment_service.PreparedUpload(
        original_filename="example.pdf",
        extension=".pdf",
        mime_type="application/pdf",
        size=21,
        checksum="a" * 64,
        temporary_path=temporary_path,
        storage_key=f"{company_id}/{ticket_id}/stored.pdf",
        stored_filename="stored.pdf",
        final_path=final_path,
    )

    async def fake_prepare_upload(upload, *, company_id, ticket_id):
        return prepared

    monkeypatch.setattr(attachment_service, "get_ticket", lambda *args, **kwargs: None)
    monkeypatch.setattr(attachment_service, "_prepare_upload", fake_prepare_upload)

    returned_row = {
        "id": uuid4(),
        "company_id": company_id,
        "ticket_id": ticket_id,
        "original_filename": "example.pdf",
        "mime_type": "application/pdf",
        "file_size": 21,
        "checksum": "a" * 64,
        "uploaded_by": user_id,
        "created_at": "2026-11-24T00:00:00Z",
    }
    db = _FakeDB(insert_row=returned_row)
    current_user = SimpleNamespace(company_id=company_id, id=user_id)

    result = asyncio.run(
        attachment_service.upload_ticket_attachments(
            db,
            current_user=current_user,
            ticket_id=ticket_id,
            uploads=[object()],
        )
    )

    assert db.committed is True
    assert db.insert_parameters is not None
    assert db.insert_parameters["scan_status"] == "not_scanned"
    assert "scan_status" not in result[0]
    assert final_path.is_file()
    assert final_path.read_bytes().startswith(b"%PDF-")


def test_download_does_not_require_clean_scan_status(tmp_path: Path, monkeypatch) -> None:
    company_id = uuid4()
    ticket_id = uuid4()
    attachment_id = uuid4()
    user_id = uuid4()
    storage_key = f"{company_id}/{ticket_id}/stored.png"
    stored_path = tmp_path / storage_key
    stored_path.parent.mkdir(parents=True)
    stored_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")

    # This row deliberately contains no scan_status field. Direct-upload downloads
    # must resolve from tenant/ticket ownership and private storage, not scan state.
    row = {
        "id": attachment_id,
        "original_filename": "example.png",
        "mime_type": "image/png",
        "storage_key": storage_key,
    }
    db = _FakeDB(download_row=row)
    current_user = SimpleNamespace(company_id=company_id, id=user_id)

    monkeypatch.setattr(attachment_service, "get_ticket", lambda *args, **kwargs: None)
    monkeypatch.setattr(attachment_service, "_storage_root", lambda: tmp_path)

    metadata, downloaded_path = attachment_service.get_attachment_file(
        db,
        current_user=current_user,
        ticket_id=ticket_id,
        attachment_id=attachment_id,
    )

    assert metadata["original_filename"] == "example.png"
    assert downloaded_path == stored_path
    assert downloaded_path.is_file()
