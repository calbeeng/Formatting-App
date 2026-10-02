"""Append a Document to a Google Doc: the step-by-step "conversation" with the
Docs API. The request contents come from writer.py.

The content is written in segments (a run of text, a table, a picture). Before
each one we re-read the document to find where it ends, because tables and
pictures change its length in ways that are easiest to read back than predict.

    1. Separator: make sure there's a blank line after the existing content and
       an empty paragraph to fill. Existing paragraphs are never edited.
    2. For each segment:
       - text:    fill the empty last paragraph (writer.text_requests)
       - picture: upload the PNG to the user's Drive (only this app's own
                  files are visible to it), insert it from its link, then
                  delete the upload. Docs keeps its own copy of the image.
       - table:   insert an empty table, merge any merged cells, re-read the
                  doc to find each cell, then fill cells from last to first.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable

from ..config import Settings
from ..model import Block
from . import target, writer

ProgressFn = Callable[[int, int, str], None]  # (done, total, message)


class AppendError(Exception):
    """A problem to show the user as a plain message."""


@dataclass
class AppendResult:
    doc_id: str
    batches: list[list[dict]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Picture hosting
# --------------------------------------------------------------------------- #

class DriveImageHost:
    """Uploads a picture to the user's Drive just long enough for Docs to copy it.

    insertInlineImage needs a public link, so the file is shared "anyone with
    the link" (the link is long and random) and deleted as soon as the picture
    is in the doc, typically within a few seconds.
    """

    def __init__(self, drive_service):
        self.drive = drive_service

    def upload(self, png: bytes, name: str) -> tuple[list[str], Callable[[], None]]:
        from googleapiclient.http import MediaInMemoryUpload

        f = self.drive.files().create(
            body={"name": name, "description": "Temporary upload by notes2gdoc; safe to delete."},
            media_body=MediaInMemoryUpload(png, mimetype="image/png"),
            fields="id",
        ).execute()
        file_id = f["id"]
        self.drive.permissions().create(fileId=file_id, body={"type": "anyone", "role": "reader"}).execute()

        def cleanup():
            try:
                self.drive.files().delete(fileId=file_id).execute()
            except Exception:
                pass  # leaving a temporary file behind is harmless

        # Two link styles; Docs sometimes can't fetch the first, so the second is a fallback.
        uris = [
            f"https://drive.google.com/uc?export=download&id={file_id}",
            f"https://lh3.googleusercontent.com/d/{file_id}",
        ]
        return uris, cleanup


class PlaceholderImageHost:
    """For dry runs: no upload, just a placeholder link in the plan."""

    def __init__(self):
        self.count = 0

    def upload(self, png: bytes, name: str):
        self.count += 1
        return [f"https://example.invalid/{name}"], lambda: None


# --------------------------------------------------------------------------- #
# The append job
# --------------------------------------------------------------------------- #

def _insert(index: int, text: str) -> dict:
    return {"insertText": {"location": {"index": index}, "text": text}}


def _is_retryable(exc: Exception) -> bool:
    status = getattr(getattr(exc, "resp", None), "status", None)
    return status in (429, 500, 502, 503, 504)


class AppendJob:
    def __init__(self, docs_service, doc_id: str, settings: Settings, image_host,
                 progress: ProgressFn | None = None, sleep=time.sleep):
        self.docs = docs_service
        self.doc_id = doc_id
        self.settings = settings
        self.images = image_host
        self.progress = progress or (lambda *_: None)
        self.sleep = sleep
        self.result = AppendResult(doc_id)

    # --- API calls with retry ----------------------------------------------
    def _call(self, request):
        delay = 2.0
        for attempt in range(6):
            try:
                return request.execute()
            except Exception as exc:
                if attempt < 5 and _is_retryable(exc):
                    # Google limits writes to ~60 a minute per user; wait and retry.
                    self.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
                raise

    def _get(self) -> dict:
        return self._call(self.docs.documents().get(documentId=self.doc_id))

    def _batch(self, requests: list[dict]) -> None:
        if not requests:
            return
        self.result.batches.append(requests)
        self._call(self.docs.documents().batchUpdate(documentId=self.doc_id, body={"requests": requests}))

    # --- main ---------------------------------------------------------------
    def run(self, blocks: list[Block], after_heading: target.DocHeading | None = None) -> AppendResult:
        """Write the ticked blocks at the end of the doc, or at the end of the
        section under `after_heading` (just before the next heading of the
        same or higher level)."""
        doc = self._get()
        preset = target.bullet_preset(doc)
        max_width = target.usable_width_pt(doc)
        self._page_width = max_width
        segs = writer.segments([b for b in blocks if b.selected])
        if not segs:
            raise AppendError("Nothing is ticked to append.")
        total = len(segs) + 1

        # Where existing content resumes after our insert (None = end of doc).
        # Everything we write goes in just before it, so as the doc grows the
        # anchor moves down by exactly the amount we've added.
        self._anchor0 = target.section_end(doc, after_heading) if after_heading else None
        self._end0 = target.body_end(doc)

        self.progress(0, total, "Preparing the document…")
        self._batch(self._separator_requests(self._slot(doc)))

        for n, (kind, seg_blocks) in enumerate(segs, start=1):
            self.progress(n, total, f"Writing part {n} of {len(segs)}…")
            slot = self._slot(self._get())
            if kind == "text":
                prep, index = self._ensure_fill_paragraph(slot)
                reqs, _ = writer.text_requests(seg_blocks, index, self.settings, preset)
                self._batch(prep + reqs)
            elif kind == "image":
                self._write_image(seg_blocks[0], slot, max_width, n)
            else:
                self._write_table(seg_blocks[0], slot, preset)
        self.progress(total, total, "Done")
        return self.result

    def _slot(self, doc: dict) -> target.Slot:
        anchor = None
        if self._anchor0 is not None:
            anchor = self._anchor0 + (target.body_end(doc) - self._end0)
        return target.slot_before(doc, anchor)

    @staticmethod
    def _separator_requests(slot: target.Slot) -> list[dict]:
        """Leave a blank line after the existing content and an empty paragraph
        to write into. A completely empty doc needs neither."""
        if slot.doc_is_empty:
            return []
        if slot.prev_is_table:
            # The section ends with a table: add the two lines in front of the next heading
            a = slot.anchor
            return [_insert(a, "\n\n")] + writer.plain_paragraph_requests(a, a + 2)
        nl = slot.prev_newline
        if slot.prev_empty:  # there's already a blank line: use it as the gap
            return [_insert(nl, "\n")] + writer.plain_paragraph_requests(nl + 1, nl + 2)
        return [_insert(nl, "\n\n")] + writer.plain_paragraph_requests(nl + 1, nl + 3)

    @staticmethod
    def _ensure_fill_paragraph(slot: target.Slot) -> tuple[list[dict], int]:
        """(requests, index of an empty paragraph of ours to fill).

        After the separator step, an empty paragraph right before the slot is
        always one we made, so it's safe to fill."""
        if slot.prev_is_table:
            a = slot.anchor
            return [_insert(a, "\n")] + writer.plain_paragraph_requests(a, a + 1), a
        if slot.prev_empty:
            return [], slot.prev_start
        nl = slot.prev_newline
        return [_insert(nl, "\n")], nl + 1

    def _write_image(self, block: Block, slot: target.Slot, max_width: float, n: int) -> None:
        img = block.image
        prep, index = self._ensure_fill_paragraph(slot)
        uris, cleanup = self.images.upload(img.png, f"notes2gdoc-diagram-{n}.png")
        try:
            last_exc = None
            for uri in uris:
                try:
                    self._batch(prep + writer.image_requests(index, uri, img.width_pt, img.height_pt, max_width))
                    return
                except Exception as exc:  # e.g. Docs couldn't fetch this link style
                    # A batch is all-or-nothing, so nothing was applied; try the next link.
                    if _is_retryable(exc):
                        raise
                    last_exc = exc
            raise AppendError(f"Couldn't insert a diagram picture: {last_exc}")
        finally:
            cleanup()

    def _write_table(self, block: Block, slot: target.Slot, preset: str) -> None:
        t = block.table
        # A table takes on the indent of the paragraph it's inserted into, so
        # inserting it right after a bullet indents the whole table (and the
        # line after it). So: always start from our own plain, empty paragraph.
        # This leaves one blank line above the table.
        # The table's line also gets an explicit zero indent: Google positions
        # a table by its line's indent, and a doc's Normal text style may have one.
        prep, index = self._ensure_fill_paragraph(slot)
        prep += writer.plain_paragraph_requests(index, index + 1)
        prep.append(writer.zero_indent_request(index, index + 1))
        self._batch(prep + [{"insertTable": {"rows": t.n_rows, "columns": t.n_cols,
                                             "location": {"index": index}}}])
        doc = self._get()
        table_el = target.find_table_at(doc, index)
        if table_el is None:
            raise AppendError("The table was inserted but couldn't be found again to fill it.")

        merges = [c for c in t.cells if c.rowspan > 1 or c.colspan > 1]
        if merges:
            self._batch([{"mergeTableCells": {"tableRange": {
                "tableCellLocation": {"tableStartLocation": {"index": table_el["startIndex"]},
                                      "rowIndex": c.row, "columnIndex": c.col},
                "rowSpan": c.rowspan, "columnSpan": c.colspan}}} for c in merges])
            doc = self._get()
            table_el = target.find_table_at(doc, index)

        rows = table_el["table"]["tableRows"]
        # Full page width, in the source's column proportions
        reqs: list[dict] = writer.table_width_requests(
            table_el["startIndex"], t.col_widths, t.n_cols, self._page_width)
        reqs += writer.cell_background_requests(table_el["startIndex"], t.cells, self.settings.strip_colour)
        # The empty line Docs leaves after a table copies the style of the
        # paragraph the table was inserted into (maybe a bullet); make it plain.
        after = target.element_starting_at(doc, table_el["endIndex"])
        if after and "paragraph" in after and not target.paragraph_text(after).strip():
            reqs += writer.plain_paragraph_requests(after["startIndex"], after["endIndex"])
            reqs.append(writer.zero_indent_request(after["startIndex"], after["endIndex"]))
        # ...and the blank line just above the table, which the table hangs off
        before = target.element_ending_at(doc, table_el["startIndex"])
        if before and "paragraph" in before and not target.paragraph_text(before).strip():
            reqs.append(writer.zero_indent_request(before["startIndex"], before["endIndex"]))
        for cell in sorted(t.cells, key=lambda c: (c.row, c.col), reverse=True):
            blocks = [b for b in cell.blocks if b.text.strip() or b.spacer]
            while blocks and blocks[-1].spacer:
                blocks.pop()
            if not blocks:
                continue
            try:
                start = rows[cell.row]["tableCells"][cell.col]["content"][0]["startIndex"]
            except (IndexError, KeyError):
                continue
            cell_reqs, _ = writer.text_requests(blocks, start, self.settings, preset, flush_left=True)
            reqs.extend(cell_reqs)
        self._batch(reqs)


def save_dry_run(path: str, result: AppendResult, preview: list) -> None:
    """Write the planned requests (and what the doc would look like) to JSON."""
    def strip_images(batches):
        return json.loads(json.dumps(batches, default=str))

    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "document_id": result.doc_id,
            "note": "Dry run: these requests were NOT sent. Indexes assume the doc ends where it did when planned.",
            "batches": strip_images(result.batches),
            "preview": preview,
        }, fh, indent=2, ensure_ascii=False)
