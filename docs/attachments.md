# Ticket attachments

Ticket attachments use the existing `public.ticket_attachments` table and the existing private file-storage directory. The direct-upload implementation does not create a replacement table.

## API

- `GET /api/v1/tickets/{ticket_id}/attachments` — list attachment metadata visible to the current user.
- `POST /api/v1/tickets/{ticket_id}/attachments` — upload one or more files as multipart form data. The multipart field name is `files`.
- `GET /api/v1/tickets/{ticket_id}/attachments/{attachment_id}/download` — download an uploaded attachment. Downloads are not held for antivirus scanning.

The API derives company and uploader identity from the authenticated user. It never accepts `company_id`, `uploaded_by`, `storage_key`, or `stored_filename` from the browser. Existing ticket permissions and row-level security remain in force.

## File validation and storage

- Permitted extensions: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.txt`, `.docx`.
- Maximum size: 10 MiB per file.
- Maximum count: 5 files per ticket, including previous uploads.
- The server checks that file contents match the allowed format, rejects empty files, generates server-side storage names, computes SHA-256, and enforces the configured size limit while streaming the upload.
- Files are stored below `ATTACHMENT_STORAGE_ROOT` (default: `./private_uploads`).
- The storage directory must remain private and must not be served by Vite, FastAPI static files, or a reverse proxy as a public directory.
- Uploaded files are immediately available for download after the database transaction succeeds.

## Antivirus behavior

ClamAV and all antivirus-scanning dependencies have been removed from the application. The application does not scan uploaded files. The legacy database `scan_status` column is retained for compatibility and historical records; new uploads use `not_scanned`. The API no longer exposes this field and the download endpoint no longer blocks files based on it.

**Security note:** file extension, MIME mapping, size limits, and file-signature/content checks are not malware detection. Only allow trusted users to upload attachments, keep storage private, retain authorization checks, and use endpoint/file handling safeguards. Do not describe an uploaded file as malware-scanned or virus-free.

## Required configuration

```dotenv
ATTACHMENT_STORAGE_ROOT=./private_uploads
ATTACHMENT_MAX_FILE_SIZE_BYTES=10485760
ATTACHMENT_MAX_FILES_PER_TICKET=5
```

Use an absolute path for `ATTACHMENT_STORAGE_ROOT` in production. Back up the attachment directory together with the database; database backups alone contain metadata, not file contents.

## Database update required once

Before running the updated backend against the existing database, apply:

```text
migrations/V023__direct_ticket_attachment_upload.sql
```

Run it once as the database owner or an authorized migration role (for example, in pgAdmin Query Tool). It extends the existing `scan_status` constraint to accept `not_scanned` and changes the column default. It preserves existing attachment rows and their historical scan values. The application does not apply this migration at startup.

Then run `migrations/V023__direct_ticket_attachment_upload_verify.sql` to inspect the column, constraint, and status counts.

## Database permissions and isolation

The application login (normally `ticket_app`) needs `SELECT` and `INSERT` on `public.ticket_attachments`. RLS policies remain the authority for tenant and permission scoping. Confirm the runtime application role is not the table owner and does not have `BYPASSRLS`.

The `attachment.upload` permission must be granted to the tenant roles that should upload files. The frontend role map is only a display aid; backend permission checks and RLS policies remain authoritative.
