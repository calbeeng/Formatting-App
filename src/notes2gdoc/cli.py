"""Command-line interface.

    notes2gdoc outline FILE [--plain] [--debug] [--pages 2-5] [--hide-skipped]
"""

from __future__ import annotations

import argparse
import sys

from .config import Settings
from .outline import render_outline
from .parsers import ParseError, parse_file


def _page_range(text: str) -> tuple[int, int]:
    try:
        if "-" in text:
            a, b = text.split("-", 1)
            return int(a), int(b)
        n = int(text)
        return n, n
    except ValueError:
        raise argparse.ArgumentTypeError("use a page number or range like 3 or 2-5")


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to a legacy code page; curly quotes and bullets need UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(prog="notes2gdoc", description="Study files -> Google Docs")
    sub = ap.add_subparsers(dest="command", required=True)

    p_out = sub.add_parser("outline", help="print the extracted outline as Markdown-like text")
    p_out.add_argument("file")
    p_out.add_argument("--plain", action="store_true", help="no inline style markers")
    p_out.add_argument("--debug", action="store_true", help="show page and why each block was classified")
    p_out.add_argument("--pages", type=_page_range, help="only blocks starting on these pages, e.g. 2-5")
    p_out.add_argument("--hide-skipped", action="store_true", help="omit blocks unticked by default")
    p_out.add_argument("--strip-colour", action="store_true", help="ignore text colour")
    p_out.add_argument("-o", "--output", help="write to this file (UTF-8) instead of the screen")

    sub.add_parser("login", help="sign in to Google (opens your browser)")
    sub.add_parser("logout", help="forget the saved Google sign-in")
    p_app = sub.add_parser("append", help="append a file to the end of a Google Doc")
    p_app.add_argument("file")
    p_app.add_argument("doc", help="Google Doc link or ID")
    p_app.add_argument("--dry-run", metavar="OUT.json", help="don't change the doc; save the planned requests")
    p_app.add_argument("--strip-colour", action="store_true")

    args = ap.parse_args(argv)
    settings = Settings.load()

    if args.command in ("login", "logout", "append"):
        return _google_command(args, settings)

    if args.command == "outline":
        if args.strip_colour:
            settings.strip_colour = True
        try:
            doc = parse_file(args.file, settings)
        except ParseError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if args.pages:
            lo, hi = args.pages
            doc.blocks = [b for b in doc.blocks if lo <= b.page <= hi]
        for w in doc.warnings:
            print(f"Warning: {w}", file=sys.stderr)
        text = f"<!-- {doc.layout}, {doc.page_count} pages, {len(doc.blocks)} blocks -->\n" + render_outline(
            doc, settings, plain=args.plain, debug=args.debug, include_skipped=not args.hide_skipped
        )
        if args.output:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            print(f"Wrote {args.output}")
        else:
            sys.stdout.write(text)
    return 0


def _google_command(args, settings) -> int:
    from . import gdocs
    from .gdocs import auth

    try:
        if args.command == "logout":
            auth.sign_out()
            print("Signed out.")
            return 0
        if args.command == "login":
            auth.sign_in()
            print(f"Signed in as {auth.signed_in_email() or 'your Google account'}.")
            return 0

        # append
        doc_id = gdocs.doc_id_from_url(args.doc)
        if not doc_id:
            print("Error: that doesn't look like a Google Doc link.", file=sys.stderr)
            return 1
        if args.strip_colour:
            settings.strip_colour = True
        document = parse_file(args.file, settings)
        creds = auth.load_credentials()

        def progress(done, total, msg):
            print(f"[{done}/{total}] {msg}")

        if args.dry_run:
            gdocs.dry_run(document, doc_id, settings, args.dry_run, creds, progress)
            print(f"Dry run saved to {args.dry_run} (nothing was changed).")
            return 0
        if creds is None:
            creds = auth.sign_in()
        gdocs.append_document(document, doc_id, settings, creds, progress)
        print(f"Done: {gdocs.doc_url(doc_id)}")
        return 0
    except (ParseError, gdocs.AppendError, auth.AuthError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        friendly = gdocs.friendly_http_error(exc)
        if friendly:
            print(f"Error: {friendly}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
