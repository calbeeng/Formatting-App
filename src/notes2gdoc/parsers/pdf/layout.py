"""Page-level geometry: drawn boxes, underlines, slide-deck detection and
bullet-indent clustering."""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf as fitz

# A drawn rectangle or line thinner than this (in points) is treated as a rule or
# border rather than a filled shape.
THIN = 1.6


@dataclass
class Segment:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def horizontal(self) -> bool:
        return (self.y1 - self.y0) <= THIN and (self.x1 - self.x0) > (self.y1 - self.y0)


@dataclass
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    def contains(self, x: float, y: float) -> bool:
        return self.x0 - 1 <= x <= self.x1 + 1 and self.y0 - 1 <= y <= self.y1 + 1


def analyse_drawings(page: fitz.Page, drawings: list[dict] | None = None) -> tuple[list[Box], list[Segment]]:
    """Find bordered boxes (e.g. a "notice" box) and underline candidates.

    Word and PowerPoint draw both box borders and underlines as very thin filled
    rectangles. We gather all thin segments, then join the ones that touch:
    * a connected group with both horizontal and vertical edges that encloses a
      reasonable area is a box;
    * a lone horizontal segment is a possible underline (or a decorative rule;
      extract.py only treats it as an underline if it sits right under text).
    Thick stroked rectangles are also accepted as boxes.
    """
    page_area = page.rect.width * page.rect.height
    segs: list[Segment] = []
    boxes: list[Box] = []

    for path in drawings if drawings is not None else page.get_drawings():
        for item in path["items"]:
            op = item[0]
            if op == "re":
                r = item[1]
                w, h = r.width, r.height
                if h <= THIN or w <= THIN:
                    segs.append(Segment(r.x0, r.y0, r.x1, r.y1))
                elif (
                    path.get("color") is not None  # has a visible outline
                    and w > 40 and h > 15
                    and w * h < 0.85 * page_area  # not a page background
                ):
                    boxes.append(Box(r.x0, r.y0, r.x1, r.y1))
            elif op == "l":
                p1, p2 = item[1], item[2]
                x0, x1 = sorted((p1.x, p2.x))
                y0, y1 = sorted((p1.y, p2.y))
                if (y1 - y0) <= THIN or (x1 - x0) <= THIN:
                    segs.append(Segment(x0, y0, x1, y1))

    # Union-find over touching segments
    parent = list(range(len(segs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def touches(a: Segment, b: Segment, tol: float = 2.0) -> bool:
        return not (
            a.x1 + tol < b.x0 or b.x1 + tol < a.x0 or a.y1 + tol < b.y0 or b.y1 + tol < a.y0
        )

    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            if touches(segs[i], segs[j]):
                parent[find(i)] = find(j)

    groups: dict[int, list[Segment]] = {}
    for i, s in enumerate(segs):
        groups.setdefault(find(i), []).append(s)

    underline_candidates: list[Segment] = []
    for members in groups.values():
        x0 = min(s.x0 for s in members)
        y0 = min(s.y0 for s in members)
        x1 = max(s.x1 for s in members)
        y1 = max(s.y1 for s in members)
        has_h = any(s.horizontal for s in members)
        has_v = any(not s.horizontal for s in members)
        if has_h and has_v and (x1 - x0) > 40 and (y1 - y0) > 15:
            boxes.append(Box(x0, y0, x1, y1))
        else:
            underline_candidates.extend(s for s in members if s.horizontal)
    return boxes, underline_candidates


def is_slide_deck(pages: list[fitz.Page], body_size: float) -> bool:
    """Slide decks have landscape pages and big body text (PowerPoint exports
    use 18pt+). Ordinary landscape documents with ~11pt text don't count."""
    if not pages:
        return False
    landscape = sum(1 for p in pages if p.rect.width > p.rect.height * 1.15)
    return landscape >= 0.8 * len(pages) and body_size >= 15


def cluster_positions(xs: list[float], tol: float = 6.0) -> list[float]:
    """Group nearby x-positions; returns sorted cluster centres.

    Used to turn bullet-glyph x positions into nesting levels: the leftmost
    cluster is level 0, the next one level 1, and so on. `tol` is how far apart
    (in points) two glyphs can be and still count as the same level.
    """
    clusters: list[list[float]] = []
    for x in sorted(xs):
        if clusters and x - clusters[-1][-1] <= tol:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    return [sum(c) / len(c) for c in clusters]


def level_for(x: float, centres: list[float]) -> int:
    if not centres:
        return 0
    return min(range(len(centres)), key=lambda i: abs(centres[i] - x))
