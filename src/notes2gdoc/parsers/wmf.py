"""A small renderer for Windows Metafiles (.wmf), enough for the equation
previews that old Equation Editor / MathType objects store in Office files.

WMF is an old Windows-only drawing format, which Macs and Google Docs can't
show, so we draw it ourselves with PyMuPDF and produce a PNG. Supported:
text (fonts, sizes, italic/bold, Symbol-font Greek and maths signs, text
alignment, colour), lines (fraction bars, roots), polylines/polygons and
rectangles. Anything else is ignored, so unusual pictures may look incomplete.
"""

from __future__ import annotations

import struct

import pymupdf as fitz

PLACEABLE_KEY = 0x9AC6CDD7

# Text alignment flags
TA_UPDATECP, TA_RIGHT, TA_CENTER, TA_BOTTOM, TA_BASELINE = 1, 2, 6, 8, 24


def is_wmf(blob: bytes) -> bool:
    if len(blob) < 18:
        return False
    if struct.unpack_from("<I", blob, 0)[0] == PLACEABLE_KEY:
        return True
    kind, header_words = struct.unpack_from("<HH", blob, 0)
    return kind in (1, 2) and header_words == 9


def wmf_to_png(blob: bytes, dpi: int = 300) -> bytes | None:
    try:
        return _render(blob, dpi)
    except Exception:
        return None


def _render(blob: bytes, dpi: int) -> bytes | None:
    off = 0
    bbox = None
    inch = 1440
    if struct.unpack_from("<I", blob, 0)[0] == PLACEABLE_KEY:
        left, top, right, bottom, inch = struct.unpack_from("<hhhhH", blob, 6)
        bbox = (left, top, right, bottom)
        off = 22
    header_words = struct.unpack_from("<H", blob, off + 2)[0]
    pos = off + header_words * 2

    records = []
    while pos + 6 <= len(blob):
        size, func = struct.unpack_from("<IH", blob, pos)
        if size < 3 or func == 0:
            break
        records.append((func, blob[pos + 6: pos + size * 2]))
        pos += size * 2

    # Logical coordinate space: the window set by the file, else the bbox
    org = [0, 0]
    ext = None
    for func, data in records:
        if func == 0x020B:
            y, x = struct.unpack_from("<hh", data)
            org = [x, y]
        elif func == 0x020C:
            y, x = struct.unpack_from("<hh", data)
            ext = [x, y]
    if bbox is None and ext is None:
        return None
    if bbox is None:
        bbox = (org[0], org[1], org[0] + ext[0], org[1] + ext[1])
    if ext is None:
        org, ext = [bbox[0], bbox[1]], [bbox[2] - bbox[0], bbox[3] - bbox[1]]
    w_pt = abs(bbox[2] - bbox[0]) / inch * 72
    h_pt = abs(bbox[3] - bbox[1]) / inch * 72
    if w_pt < 1 or h_pt < 1:
        return None
    sx, sy = w_pt / ext[0], h_pt / ext[1]

    def pt(x, y):
        return fitz.Point((x - org[0]) * sx, (y - org[1]) * sy)

    doc = fitz.open()
    page = doc.new_page(width=w_pt, height=h_pt)
    objects: dict[int, tuple] = {}
    font = ("Times New Roman", -12, False, False)
    pen = (0, 0, 0)
    pen_width = 1.0
    text_colour = (0, 0, 0)
    align = 0
    cur = [0, 0]

    def add_object(obj):
        i = 0
        while i in objects:
            i += 1
        objects[i] = obj

    for func, data in records:
        if func == 0x02FB:  # CreateFontIndirect
            height, _w, _esc, _ori, weight = struct.unpack_from("<hhhhh", data)
            italic = data[10] != 0
            face = data[18:50].split(b"\x00")[0].decode("latin-1", "replace")
            add_object(("font", (face, height, italic, weight >= 600)))
        elif func == 0x02FA:  # CreatePenIndirect
            style, wx, _wy = struct.unpack_from("<HhH", data)
            r, g, b = data[6], data[7], data[8]
            add_object(("pen", ((r / 255, g / 255, b / 255), max(abs(wx) * sx, 0.4), style == 5)))
        elif func in (0x02FC, 0x0142, 0x00F7, 0x06FF):  # brush, DIB brush, palette, region
            add_object(("other", None))
        elif func == 0x012D:  # SelectObject
            obj = objects.get(struct.unpack_from("<H", data)[0])
            if obj and obj[0] == "font":
                font = obj[1]
            elif obj and obj[0] == "pen":
                pen, pen_width, invisible = obj[1]
                if invisible:
                    pen = None
        elif func == 0x01F0:  # DeleteObject
            objects.pop(struct.unpack_from("<H", data)[0], None)
        elif func == 0x012E:  # SetTextAlign
            align = struct.unpack_from("<H", data)[0]
        elif func == 0x0209:  # SetTextColor
            text_colour = (data[0] / 255, data[1] / 255, data[2] / 255)
        elif func == 0x0214:  # MoveTo
            y, x = struct.unpack_from("<hh", data)
            cur = [x, y]
        elif func == 0x0213:  # LineTo
            y, x = struct.unpack_from("<hh", data)
            if pen:
                page.draw_line(pt(*cur), pt(x, y), color=pen, width=pen_width)
            cur = [x, y]
        elif func in (0x0324, 0x0325):  # Polygon, Polyline
            n = struct.unpack_from("<h", data)[0]
            coords = struct.unpack_from(f"<{2 * n}h", data, 2)
            points = [pt(coords[i], coords[i + 1]) for i in range(0, 2 * n, 2)]
            if len(points) >= 2 and pen:
                page.draw_polyline(points, color=pen, width=pen_width,
                                   fill=pen if func == 0x0324 else None, closePath=func == 0x0324)
        elif func == 0x041B:  # Rectangle
            bottom, right, top, left = struct.unpack_from("<hhhh", data)
            if pen:
                page.draw_rect(fitz.Rect(pt(left, top), pt(right, bottom)), color=pen, width=pen_width)
        elif func in (0x0A32, 0x0521):  # ExtTextOut, TextOut
            if func == 0x0A32:
                y, x, count, options = struct.unpack_from("<hhhH", data)
                text_start = 8 + (8 if options & 0x0006 else 0)
            else:
                count = struct.unpack_from("<h", data)[0]
                text_start = 2
                y, x = struct.unpack_from("<hh", data, text_start + ((count + 1) // 2) * 2)
            raw = data[text_start: text_start + count]
            # ExtTextOut may give each character's advance (in logical units):
            # equation editors use it to space out letters like "U E r A".
            dx = None
            if func == 0x0A32:
                dx_start = text_start + count + (count % 2)
                if len(data) >= dx_start + 2 * count:
                    dx = struct.unpack_from(f"<{count}h", data, dx_start)
            if align & TA_UPDATECP:
                x, y = cur
            if dx and count > 1:
                width = 0.0
                cx = x
                for i, ch in enumerate(raw):
                    _draw_text(page, bytes([ch]), font, pt, cx, y, align & ~TA_CENTER & ~TA_RIGHT, sy, text_colour)
                    cx += dx[i]
                width = (cx - x) * sx
            else:
                width = _draw_text(page, raw, font, pt, x, y, align, sy, text_colour)
            if align & TA_UPDATECP:
                cur = [x + width / sx, y]

    pix = page.get_pixmap(dpi=dpi, alpha=False)
    png = pix.tobytes("png")
    doc.close()
    return png


def _draw_text(page, raw: bytes, font, pt, x, y, align, sy, colour) -> float:
    """Draw one string; returns its width in points."""
    face, height, italic, bold = font
    size = abs(height) * sy * (1.0 if height < 0 else 0.82)
    if size <= 0.5 or not raw:
        return 0.0
    if "symbol" in face.lower():
        fontname = "symb"   # the PDF Symbol font understands Symbol-font codes directly
        text = raw.decode("latin-1")
    else:
        fontname = {(False, False): "tiro", (True, False): "tiit", (False, True): "tibo",
                    (True, True): "tibi"}[(italic, bold)]
        if "times" not in face.lower():
            fontname = {"tiro": "helv", "tiit": "heit", "tibo": "hebo", "tibi": "hebi"}[fontname]
        text = raw.decode("cp1252", "replace")
    width = fitz.get_text_length(text, fontname=fontname, fontsize=size)
    origin = pt(x, y)
    if (align & TA_BASELINE) == TA_BASELINE:
        baseline = origin.y
    elif align & TA_BOTTOM:
        baseline = origin.y - size * 0.2
    else:  # TA_TOP
        baseline = origin.y + size * 0.8
    left = origin.x
    if (align & TA_CENTER) == TA_CENTER:
        left -= width / 2
    elif align & TA_RIGHT:
        left -= width
    page.insert_text(fitz.Point(left, baseline), text, fontname=fontname, fontsize=size, color=colour)
    return width
