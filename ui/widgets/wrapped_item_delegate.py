from __future__ import annotations

import math

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QListView, QStyleOptionViewItem, QStyledItemDelegate


class WrappedItemDelegate(QStyledItemDelegate):
    def __init__(
        self,
        parent=None,
        *,
        max_lines: int = 2,
        minimum_height: int = 42,
        horizontal_padding: int = 30,
        vertical_padding: int = 14,
    ) -> None:
        super().__init__(parent)
        self.max_lines = max_lines
        self.minimum_height = minimum_height
        self.horizontal_padding = horizontal_padding
        self.vertical_padding = vertical_padding

    def initStyleOption(self, option, index) -> None:  # noqa: N802
        super().initStyleOption(option, index)
        option.features |= QStyleOptionViewItem.ViewItemFeature.WrapText
        option.textElideMode = Qt.TextElideMode.ElideRight

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        prepared = QStyleOptionViewItem(option)
        self.initStyleOption(prepared, index)
        view = prepared.widget
        viewport_width = (
            view.viewport().width()
            if isinstance(view, QListView) and view.viewport().width() > 0
            else max(1, prepared.rect.width())
        )
        check_width = 26 if index.data(Qt.ItemDataRole.CheckStateRole) is not None else 0
        text_width = max(40, viewport_width - self.horizontal_padding - check_width)
        line_height = max(1, prepared.fontMetrics.lineSpacing())
        bounds = prepared.fontMetrics.boundingRect(
            QRect(0, 0, text_width, line_height * (self.max_lines + 2)),
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
            | Qt.TextFlag.TextWordWrap,
            prepared.text,
        )
        natural_lines = max(1, math.ceil(bounds.height() / line_height))
        visible_lines = min(self.max_lines, natural_lines)
        height = max(
            self.minimum_height, visible_lines * line_height + self.vertical_padding
        )
        return QSize(text_width, height)


def configure_wrapped_list(
    view: QListView,
    *,
    max_lines: int = 2,
    minimum_height: int = 42,
) -> None:
    view.setWordWrap(True)
    view.setTextElideMode(Qt.TextElideMode.ElideRight)
    view.setUniformItemSizes(False)
    view.setResizeMode(QListView.ResizeMode.Adjust)
    view.setItemDelegate(
        WrappedItemDelegate(
            view,
            max_lines=max_lines,
            minimum_height=minimum_height,
        )
    )
