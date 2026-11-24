from __future__ import annotations

import hashlib
import os
import tempfile
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User
from app.services.ticket_service import get_ticket


CHUNK_SIZE = 64 * 1024
LEGACY_SCAN_STATUS = "not_scanned"
ALLOWED_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass
class PreparedUpload:
    original_filename: str
    extension: str
    mime_type: str
    size: int
    checksum: str
    temporary_path: Path
    storage_key: str
    stored_filename: str
    final_path: Path


def _storage_root() -> Path:
    root = Path(settings.attachment_storage_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        root.chmod(0o700)
    return root


def _safe_original_filename(filename: str | None) -> str:
    raw = (filename or "attachment").replace("\\", "/").split("/")[-1]
    cleaned = "".join(
        char for char in raw
        if unicodedata.category(char) != "Cc" and char not in {"\u202e", "\u202d"}
    ).strip()
    if not cleaned or cleaned in {".", ".."}:
        cleaned = "attachment"
    return cleaned[:255]


def _validate_file_content(path: Path, extension: str) -> None:
    with path.open("rb") as stream:
        header = stream.read(16)

    valid = False
    if extension == ".pdf":
        valid = header.startswith(b"%PDF-")
    elif extension == ".png":
        valid = header.startswith(b"\x89PNG\r\n\x1a\n")
    elif extension in {".jpg", ".jpeg"}:
        valid = header.startswith(b"\xff\xd8\xff")
    elif extension == ".txt":
        try:
            content = path.read_bytes()
            content.decode("utf-8-sig")
            valid = b"\x00" not in content
        except UnicodeDecodeError:
            valid = False
    elif extension == ".docx":
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                valid = (
                    "[Content_Types].xml" in names
                    and "word/document.xml" in names
                )
        except (OSError, zipfile.BadZipFile):
            valid = False

    if not valid:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"The contents do not match the permitted {extension or 'file'} format.",
        )



async def _prepare_upload(upload: UploadFile, *, company_id: UUID, ticket_id: UUID) -> PreparedUpload:
    original_filename = _safe_original_filename(upload.filename)
    extension = Path(original_filename).suffix.lower()
    mime_type = ALLOWED_MIME_TYPES.get(extension)
    if mime_type is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Allowed file types: PDF, PNG, JPG, JPEG, TXT and DOCX.",
        )

    root = _storage_root()
    staging = root / ".staging"
    staging.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        staging.chmod(0o700)
    descriptor, temp_name = tempfile.mkstemp(prefix="upload-", suffix=".part", dir=staging)
    temporary_path = Path(temp_name)
    digest = hashlib.sha256()
    size = 0

    try:
        with os.fdopen(descriptor, "wb") as destination:
            while chunk := await upload.read(CHUNK_SIZE):
                size += len(chunk)
                if size > settings.attachment_max_file_size_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=(
                            f"Each attachment must be at most "
                            f"{settings.attachment_max_file_size_bytes // (1024 * 1024)} MB."
                        ),
                    )
                digest.update(chunk)
                destination.write(chunk)

        if size == 0:
            raise HTTPException(status_code=400, detail="Empty files cannot be uploaded.")

        _validate_file_content(temporary_path, extension)

        stored_filename = f"{uuid.uuid4().hex}{extension}"
        storage_key = PurePosixPath(str(company_id), str(ticket_id), stored_filename).as_posix()
        final_path = (root / storage_key).resolve()
        if root not in final_path.parents:
            raise HTTPException(status_code=400, detail="Invalid storage path.")

        return PreparedUpload(
            original_filename=original_filename,
            extension=extension,
            mime_type=mime_type,
            size=size,
            checksum=digest.hexdigest(),
            temporary_path=temporary_path,
            storage_key=storage_key,
            stored_filename=stored_filename,
            final_path=final_path,
        )
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


def list_ticket_attachments(
    db: Session, *, current_user: User, ticket_id: UUID
) -> list[dict]:
    get_ticket(db, company_id=current_user.company_id, ticket_id=ticket_id)
    rows = db.execute(
        text(
            """
            SELECT id, company_id, ticket_id, original_filename, mime_type,
                   file_size, checksum, uploaded_by, created_at
            FROM public.ticket_attachments
            WHERE company_id = :company_id AND ticket_id = :ticket_id
            ORDER BY created_at, id
            """
        ),
        {"company_id": current_user.company_id, "ticket_id": ticket_id},
    ).mappings().all()
    return [dict(row) for row in rows]


async def upload_ticket_attachments(
    db: Session,
    *,
    current_user: User,
    ticket_id: UUID,
    uploads: list[UploadFile],
) -> list[dict]:
    get_ticket(db, company_id=current_user.company_id, ticket_id=ticket_id)

    if not uploads:
        raise HTTPException(status_code=400, detail="Select at least one file.")
    if len(uploads) > settings.attachment_max_files_per_ticket:
        raise HTTPException(
            status_code=400,
            detail=f"You can upload at most {settings.attachment_max_files_per_ticket} files per ticket.",
        )

    existing_count = db.execute(
        text(
            """
            SELECT COUNT(*)
            FROM public.ticket_attachments
            WHERE company_id = :company_id AND ticket_id = :ticket_id
            """
        ),
        {"company_id": current_user.company_id, "ticket_id": ticket_id},
    ).scalar_one()
    if existing_count + len(uploads) > settings.attachment_max_files_per_ticket:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This ticket can have at most {settings.attachment_max_files_per_ticket} "
                "attachments in total."
            ),
        )

    prepared: list[PreparedUpload] = []
    moved_paths: list[Path] = []
    try:
        for upload in uploads:
            prepared.append(
                await _prepare_upload(
                    upload,
                    company_id=current_user.company_id,
                    ticket_id=ticket_id,
                )
            )

        for item in prepared:
            item.final_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                item.final_path.parent.chmod(0o700)
            os.replace(item.temporary_path, item.final_path)
            moved_paths.append(item.final_path)

        inserted: list[dict] = []
        for item in prepared:
            row = db.execute(
                text(
                    """
                    INSERT INTO public.ticket_attachments (
                        company_id, ticket_id, original_filename, stored_filename,
                        storage_key, mime_type, file_size, checksum, scan_status,
                        uploaded_by
                    )
                    VALUES (
                        :company_id, :ticket_id, :original_filename, :stored_filename,
                        :storage_key, :mime_type, :file_size, :checksum, :scan_status,
                        :uploaded_by
                    )
                    RETURNING id, company_id, ticket_id, original_filename, mime_type,
                              file_size, checksum, uploaded_by, created_at
                    """
                ),
                {
                    "company_id": current_user.company_id,
                    "ticket_id": ticket_id,
                    "original_filename": item.original_filename,
                    "stored_filename": item.stored_filename,
                    "storage_key": item.storage_key,
                    "mime_type": item.mime_type,
                    "file_size": item.size,
                    "checksum": item.checksum,
                    "scan_status": LEGACY_SCAN_STATUS,
                    "uploaded_by": current_user.id,
                },
            ).mappings().one()
            inserted.append(dict(row))

        db.commit()
        return inserted
    except Exception:
        db.rollback()
        for item in prepared:
            item.temporary_path.unlink(missing_ok=True)
        for path in moved_paths:
            path.unlink(missing_ok=True)
        raise


def get_attachment_file(
    db: Session,
    *,
    current_user: User,
    ticket_id: UUID,
    attachment_id: UUID,
) -> tuple[dict, Path]:
    get_ticket(db, company_id=current_user.company_id, ticket_id=ticket_id)
    row = db.execute(
        text(
            """
            SELECT id, original_filename, mime_type, storage_key
            FROM public.ticket_attachments
            WHERE id = :attachment_id
              AND company_id = :company_id
              AND ticket_id = :ticket_id
            """
        ),
        {
            "attachment_id": attachment_id,
            "company_id": current_user.company_id,
            "ticket_id": ticket_id,
        },
    ).mappings().one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    root = _storage_root()
    file_path = (root / row["storage_key"]).resolve()
    if root not in file_path.parents or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Stored attachment file was not found.")

    return dict(row), file_path
