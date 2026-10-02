"""Everything that talks to (or imitates) Google Docs."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..model import Document
from .client import AppendError, AppendJob, AppendResult, DriveImageHost, PlaceholderImageHost, save_dry_run
from .fake import FakeDoc, FakeDocsService
from .target import DocHeading, doc_id_from_url, doc_url, end_state, headings


def friendly_http_error(exc: Exception) -> str | None:
    """Turn a Google API error into a sentence, or None if it isn't one."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        return None
    if status in (403, 404):
        return ("Couldn't open that Google Doc. Check the link, and that the Google account "
                "you're signed in with can edit it.")
    if status == 401:
        return "Your Google sign-in has expired. Please sign in again."
    if status == 429:
        return "Google is rate-limiting requests. Wait a minute and try again."
    return f"Google returned an error ({status}): {getattr(exc, 'reason', exc)}"


def append_document(document: Document, doc_id: str, settings: Settings, creds,
                    progress=None, after_heading: DocHeading | None = None) -> AppendResult:
    """Append for real, at the end of the doc or of the section under `after_heading`."""
    from .auth import services

    docs, drive = services(creds)
    return AppendJob(docs, doc_id, settings, DriveImageHost(drive), progress).run(
        document.blocks, after_heading=after_heading)


@dataclass
class DocInfo:
    doc_id: str
    title: str
    headings: list[DocHeading]


def fetch_doc_info(creds, doc_id: str) -> DocInfo:
    """The doc's title and headings (for the recent list and the heading picker)."""
    from .auth import services

    docs, _ = services(creds)
    doc = docs.documents().get(documentId=doc_id).execute()
    return DocInfo(doc_id, doc.get("title", "Untitled document"), headings(doc))


def dry_run(document: Document, doc_id: str, settings: Settings, out_path: str,
            creds=None, progress=None, after_heading: DocHeading | None = None) -> AppendResult:
    """Plan the append without changing anything, and save it as JSON.

    If signed in, the plan uses the real doc's current length so the indexes
    match what would be sent; otherwise it plans against an empty doc.
    """
    fake = FakeDoc()
    if creds is not None and doc_id:
        from .auth import services

        docs, _ = services(creds)
        real = docs.documents().get(documentId=doc_id).execute()
        if after_heading is not None:
            # Copy the doc's paragraph structure so the section can be found
            fake = FakeDoc.from_document(real)
        else:
            es = end_state(real)
            fake = FakeDoc.with_end_state(es.end_index, es.last_para_empty)
    elif after_heading is not None:
        after_heading = None  # no real doc to look the heading up in
    job = AppendJob(FakeDocsService(fake), doc_id or "dry-run", settings, PlaceholderImageHost(), progress)
    result = job.run(document.blocks, after_heading=after_heading)
    save_dry_run(out_path, result, fake.outline())
    return result


__all__ = [
    "AppendError", "AppendJob", "AppendResult", "FakeDoc", "FakeDocsService",
    "DocHeading", "DocInfo", "append_document", "doc_id_from_url", "fetch_doc_info", "doc_url", "dry_run", "friendly_http_error",
]
