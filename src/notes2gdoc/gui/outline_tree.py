"""The tick-box outline of the extracted content.

Headings nest by level (Heading 1 > Heading 2 > ...), bullets nest under the
bullet above them, and everything else sits under the heading it follows.
Unticking a heading unticks everything inside it; ticking one child of an
unticked heading makes the heading half-ticked (it's then included too, since
its content needs it).

Each tree item remembers its block's position in the Document. Ticks are
written back to `Block.selected`, combined with the page range: items outside
the range are greyed out and left out.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from ..config import Settings
from ..model import Block, Document

ROLE_INDEX = Qt.UserRole
MAX_LABEL = 110


def _label(block: Block, settings: Settings) -> str:
    if block.kind == "table":
        t = block.table
        first = t.cells[0].text if t.cells else ""
        return f"▦ Table ({t.n_rows}×{t.n_cols}) {first[:60]}"
    if block.kind == "image":
        return "🖼 Picture (diagram)"
    text = block.text
    if block.kind == "bullet":
        text = "• " + text
    elif block.kind == "heading":
        rank = settings.heading_rank(block.style_key)
        text = (f"H{rank}  " if rank else "") + text
    return text if len(text) <= MAX_LABEL else text[:MAX_LABEL - 1] + "…"


class OutlineTree(QTreeWidget):
    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["Content", "Page"])
        self.setColumnWidth(0, 330)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.doc: Document | None = None
        self.page_range: tuple[int, int] = (1, 10**6)
        self._building = False
        # Ticking a heading changes many items; wait until they've all changed
        self._debounce = QTimer(self, singleShot=True, interval=60)
        self._debounce.timeout.connect(self._sync_blocks)
        self.itemChanged.connect(self._item_changed)

    # ------------------------------------------------------------------ #
    def set_document(self, doc: Document, settings: Settings) -> None:
        self._building = True
        self.clear()
        self.doc = doc
        heading_stack: list[tuple[int, QTreeWidgetItem]] = []   # (rank, item)
        bullet_stack: list[tuple[int, QTreeWidgetItem]] = []    # (level, item)

        def container() -> QTreeWidgetItem | None:
            return heading_stack[-1][1] if heading_stack else None

        items: list[tuple[QTreeWidgetItem, Block]] = []
        for i, b in enumerate(doc.blocks):
            item = QTreeWidgetItem([_label(b, settings), str(b.page)])
            item.setData(0, ROLE_INDEX, i)
            item.setToolTip(0, b.text[:500] or b.kind)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            items.append((item, b))

            if b.kind == "heading" and settings.heading_rank(b.style_key):
                rank = settings.heading_rank(b.style_key)
                while heading_stack and heading_stack[-1][0] >= rank:
                    heading_stack.pop()
                parent = container()
                heading_stack.append((rank, item))
                bullet_stack.clear()
                font = QFont(item.font(0))
                font.setBold(True)
                item.setFont(0, font)
            elif b.kind == "bullet":
                while bullet_stack and bullet_stack[-1][0] >= b.level:
                    bullet_stack.pop()
                parent = bullet_stack[-1][1] if bullet_stack else container()
                bullet_stack.append((b.level, item))
            else:
                parent = container()
                bullet_stack.clear()
            (parent.addChild(item) if parent else self.addTopLevelItem(item))

        # Parents first, so their tick doesn't overwrite their children's ticks
        for item, b in items:
            if item.childCount():
                item.setFlags(item.flags() | Qt.ItemIsAutoTristate)
                item.setCheckState(0, Qt.Checked if b.selected else Qt.Unchecked)
        for item, b in items:
            if not item.childCount():
                item.setCheckState(0, Qt.Checked if b.selected else Qt.Unchecked)
        self.expandToDepth(1)
        self._building = False
        self._apply_page_range()
        self._sync_blocks()

    def set_page_range(self, first: int, last: int) -> None:
        self.page_range = (first, last)
        self._apply_page_range()
        self._sync_blocks()

    def set_all(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for i in range(self.topLevelItemCount()):
            self.topLevelItem(i).setCheckState(0, state)

    # ------------------------------------------------------------------ #
    def _iter_items(self):
        stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while stack:
            item = stack.pop()
            yield item
            stack.extend(item.child(i) for i in range(item.childCount()))

    def _apply_page_range(self) -> None:
        if not self.doc:
            return
        first, last = self.page_range
        grey = QBrush(QColor("#9AA0A6"))
        for item in self._iter_items():
            b = self.doc.blocks[item.data(0, ROLE_INDEX)]
            outside = not (first <= b.page <= last)
            for col in (0, 1):
                item.setForeground(col, grey if outside else QBrush())

    def _item_changed(self, *_):
        if not self._building:
            self._debounce.start()

    def _sync_blocks(self) -> None:
        """Write ticks + page range back to the blocks, then tell the window."""
        if not self.doc:
            return
        first, last = self.page_range
        for item in self._iter_items():
            b = self.doc.blocks[item.data(0, ROLE_INDEX)]
            ticked = item.checkState(0) != Qt.Unchecked
            b.selected = ticked and first <= b.page <= last
        self.selection_changed.emit()

    def counts(self) -> tuple[int, int]:
        """(ticked, total) blocks."""
        if not self.doc:
            return 0, 0
        return sum(1 for b in self.doc.blocks if b.selected), len(self.doc.blocks)
