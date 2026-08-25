"""Disk-map rendering and hit testing for Fluxctl Studio."""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QBrush
from PySide6.QtWidgets import QToolTip, QWidget

from .reports.map import DiskMap

class DiskMapWidget(QWidget):
    sectorClicked = Signal(int, int, int)
    sectorDiagnosticRequested = Signal(int, int, int)
    STATE_COLORS = {
        "good": QColor("#0072b2"),
        "weak": QColor("#e69f00"),
        "bad": QColor("#d55e00"),
        "unused": QColor("#4f5b6f"),
        "bam_file": QColor("#009e73"),
        "bam_system": QColor("#56b4e9"),
        "bam_used": QColor("#e69f00"),
        "bam_free": QColor("#4f5b6f"),
    }
    STATE_BRUSH_STYLES = {
        "good": Qt.SolidPattern,
        "weak": Qt.BDiagPattern,
        "bad": Qt.Dense4Pattern,
        "unused": Qt.Dense6Pattern,
        "bam_file": Qt.SolidPattern,
        "bam_system": Qt.FDiagPattern,
        "bam_used": Qt.BDiagPattern,
        "bam_free": Qt.Dense6Pattern,
    }
    HIGHLIGHT_COLOR = QColor("#ff8a3d")
    LEGEND_LABELS = {
        "good": "Good",
        "weak": "Weak",
        "bad": "Bad",
        "unused": "Unused/free",
        "bam_file": "File",
        "bam_system": "System",
        "bam_used": "Allocated",
        "bam_free": "Free",
    }
    STATE_GLYPHS = {
        "good": "G",
        "weak": "W",
        "bad": "X",
        "unused": ".",
        "bam_file": "F",
        "bam_system": "S",
        "bam_used": "U",
        "bam_free": ".",
    }

    def __init__(self) -> None:
        super().__init__()
        self.disk_map = None
        self._head_layouts: list[dict[str, object]] = []
        self._focused_sector: Optional[tuple[int, int]] = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Disk map")
        self.setAccessibleDescription("Interactive disk map. Use arrow keys to select a sector, Enter to view HEX, and D for preservation diagnostics.")
        self.setMinimumHeight(520)

    def set_disk_map(self, disk_map) -> None:
        self.disk_map = disk_map
        self.update()

    def legend_items(self) -> list[tuple[str, str]]:
        if self.disk_map and getattr(self.disk_map, "render_style", "radial") == "grid":
            items = [(state, self.LEGEND_LABELS[state]) for state in ("bam_file", "bam_system", "bam_used", "bam_free")]
        else:
            items = [(state, self.LEGEND_LABELS[state]) for state in ("good", "weak", "bad", "unused")]
        if self.disk_map and getattr(self.disk_map, "highlighted_sectors", None):
            items.append(("selected_file", "Selected file"))
        return items

    @staticmethod
    def head_groups(disk_map) -> list[tuple[int, list[tuple[int, tuple[int, int], list[str]]]]]:
        """Group map rows by physical head while preserving track order."""

        if not disk_map or not disk_map.tracks:
            return []
        track_ids = disk_map.track_ids or [(idx, 0) for idx, _ in enumerate(disk_map.tracks)]
        grouped: dict[int, list[tuple[int, tuple[int, int], list[str]]]] = {}
        for row_index, (track_id, sectors) in enumerate(zip(track_ids, disk_map.tracks)):
            track, head = track_id
            grouped.setdefault(head, []).append((row_index, (track, head), sectors))
        return [
            (head, sorted(rows, key=lambda item: item[1][0]))
            for head, rows in sorted(grouped.items(), key=lambda item: item[0])
        ]

    def sector_detail_text(self, row_index: int, sector_index: int) -> str:
        if not self.disk_map:
            return ""
        track_ids = self.disk_map.track_ids or [(idx, 0) for idx, _ in enumerate(self.disk_map.tracks)]
        track, head = track_ids[row_index]
        detail = None
        if self.disk_map.sector_details and row_index < len(self.disk_map.sector_details):
            details = self.disk_map.sector_details[row_index]
            if sector_index < len(details):
                detail = details[sector_index]
        state = self.disk_map.tracks[row_index][sector_index]
        if detail is None:
            return f"Track {track} Head {head}\nSector position {sector_index + 1}\nState: {state}"
        crc = "n/a" if detail.state == "unused" or detail.state.startswith("bam_") else ("ok" if detail.crc_ok else "bad")
        state_label = self.LEGEND_LABELS.get(detail.state, detail.state)
        data = "yes" if detail.has_data else "no"
        deleted = "yes" if detail.deleted else "no"
        cbm_error = f"\nCBM DOS: {detail.cbm_dos_error}" if detail.cbm_dos_error else ""
        return (
            f"Track {track}  Head {head}\n"
            f"Sector ID {detail.sector_id}  Position {sector_index + 1}\n"
            f"State: {state_label}  CRC: {crc}\n"
            f"Confidence: {detail.confidence:.2f}\n"
            f"Size: {detail.size} bytes  Data: {data}  Deleted: {deleted}{cbm_error}\n"
            "Double-click for preservation diagnostics"
        )

    def paintEvent(self, _event) -> None:  # pragma: no cover - visual rendering.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0c1018"))
        self._head_layouts = []
        if not self.disk_map or not self.disk_map.tracks:
            painter.setPen(QPen(QColor("#788296"), 1))
            painter.drawText(self.rect(), Qt.AlignCenter, "Open an image to render the disk map")
            return

        head_groups = self.head_groups(self.disk_map)
        if not head_groups:
            return
        if getattr(self.disk_map, "render_style", "radial") == "grid":
            self._paint_grid_map(painter, width=self.width(), height=self.height(), head_groups=head_groups)
            return

        width = self.width()
        height = self.height()
        colors = self.STATE_COLORS
        columns = len(head_groups)
        gap = 28
        label_height = 34
        legend_height = 34
        column_width = max((width - gap * (columns + 1)) / columns, 1)
        usable_height = max(height - label_height - legend_height - 20, 1)

        for column, (head, rows) in enumerate(head_groups):
            left = gap + column * (column_width + gap)
            cx = left + column_width / 2
            cy = label_height + usable_height / 2
            max_radius = min(column_width, usable_height) * 0.49
            track_count = max(len(rows), 1)
            ring_width = max(max_radius / track_count, 2.0)
            self._head_layouts.append(
                {
                    "head": head,
                    "cx": cx,
                    "cy": cy,
                    "max_radius": max_radius,
                    "ring_width": ring_width,
                    "rows": rows,
                }
            )

            painter.setPen(QPen(QColor("#dce7f7"), 1))
            painter.setFont(QFont("Arial", 12, QFont.Bold))
            painter.drawText(int(left), 8, int(column_width), 24, Qt.AlignCenter, f"Head {head}")
            for track_idx, (_row_index, _track_id, sectors) in enumerate(rows):
                radius = max_radius - (track_idx * ring_width)
                if radius <= 4:
                    break
                sector_count = max(len(sectors), 1)
                for sector_idx, state in enumerate(sectors):
                    painter.setBrush(QBrush(colors.get(state, QColor("#6b7280")), self.STATE_BRUSH_STYLES.get(state, Qt.SolidPattern)))
                    pen = QPen(self.HIGHLIGHT_COLOR, 3.0) if self._sector_is_highlighted(_row_index, sector_idx) or self._sector_is_focused(_row_index, sector_idx) else QPen(QColor("#101823"), 1.35)
                    painter.setPen(pen)
                    start = int((90 - (360 * sector_idx / sector_count)) * 16)
                    span = int(-(360 / sector_count) * 16)
                    rect_size = radius * 2
                    painter.drawPie(
                        int(cx - radius),
                        int(cy - radius),
                        int(rect_size),
                        int(rect_size),
                        start,
                        span,
                    )
                painter.setBrush(QColor("#0c1018"))
                painter.setPen(QPen(QColor("#1f2b3a"), 1.2))
                inner_radius = max(radius - ring_width + 1, 0)
                painter.drawEllipse(
                    int(cx - inner_radius),
                    int(cy - inner_radius),
                    int(inner_radius * 2),
                    int(inner_radius * 2),
                )
        self._draw_legend(painter, width, height)

    def _paint_grid_map(
        self,
        painter: QPainter,
        *,
        width: int,
        height: int,
        head_groups: list[tuple[int, list[tuple[int, tuple[int, int], list[str]]]]],
    ) -> None:  # pragma: no cover - visual rendering.
        self._head_layouts = []
        if not any(rows for _head, rows in head_groups):
            return
        outer_gap = 18
        column_gap = 28
        top = 44
        legend_height = 36
        row_label_width = 40
        panes = self.grid_panes(head_groups, self.disk_map)
        columns = max(len(panes), 1)
        column_width = max((width - outer_gap * 2 - column_gap * (columns - 1)) / columns, 1)
        grid_height = max(height - top - legend_height - 12, 1)

        painter.setFont(QFont("Arial", 10))
        painter.setPen(QPen(QColor("#dce7f7"), 1))
        for column, (head, title, rows) in enumerate(panes):
            if not rows:
                continue
            left = outer_gap + column * (column_width + column_gap)
            grid_width = max(column_width - row_label_width, 1)
            max_cols = max(len(sectors) for _row_index, _track_id, sectors in rows)
            cell = max(4.0, min(grid_width / max(max_cols, 1), grid_height / max(len(rows), 1)))
            cell = min(cell, 18.0)
            painter.setPen(QPen(QColor("#dce7f7"), 1))
            painter.setFont(QFont("Arial", 12, QFont.Bold))
            painter.drawText(int(left), 8, int(column_width), 24, Qt.AlignCenter, title)
            painter.setFont(QFont("Arial", 10))
            for display_row, (row_index, (track, _head), sectors) in enumerate(rows):
                y = top + display_row * cell
                painter.setPen(QPen(QColor("#dce7f7"), 1))
                painter.drawText(
                    int(left),
                    int(y),
                    row_label_width - 4,
                    int(cell),
                    Qt.AlignRight | Qt.AlignVCenter,
                    self.track_label(track, self.disk_map),
                )
                for sector_index, state in enumerate(sectors):
                    x = left + row_label_width + sector_index * cell
                    painter.setBrush(QBrush(self.STATE_COLORS.get(state, QColor("#6b7280")), self.STATE_BRUSH_STYLES.get(state, Qt.SolidPattern)))
                    pen = QPen(self.HIGHLIGHT_COLOR, 2.2) if self._sector_is_highlighted(row_index, sector_index) or self._sector_is_focused(row_index, sector_index) else QPen(QColor("#111b28"), 1.25)
                    painter.setPen(pen)
                    painter.drawRect(int(x), int(y), max(int(cell - 1), 1), max(int(cell - 1), 1))

            self._head_layouts.append(
                {
                    "head": head,
                    "grid": True,
                    "left": left + row_label_width,
                    "top": top,
                    "cell": cell,
                    "rows": rows,
                }
            )
        self._draw_legend(painter, width, height)

    @classmethod
    def grid_panes(
        cls,
        head_groups: list[tuple[int, list[tuple[int, tuple[int, int], list[str]]]]],
        disk_map: DiskMap | None,
    ) -> list[tuple[int, str, list[tuple[int, tuple[int, int], list[str]]]]]:
        panes: list[tuple[int, str, list[tuple[int, tuple[int, int], list[str]]]]] = []
        for head, rows in head_groups:
            if not rows:
                continue
            chunk_size = 40 if len(rows) > 45 else len(rows)
            chunks = [rows[index : index + chunk_size] for index in range(0, len(rows), chunk_size)]
            for chunk in chunks:
                title = f"Head {head}"
                if len(chunks) > 1:
                    start_track = chunk[0][1][0]
                    end_track = chunk[-1][1][0]
                    title = f"{title} {cls.track_label(start_track, disk_map)}-{cls.track_label(end_track, disk_map)}"
                panes.append((head, title, chunk))
        return panes

    @staticmethod
    def track_label(track: int, disk_map: DiskMap | None) -> str:
        if disk_map is not None and getattr(disk_map, "address_style", "physical") == "cbm_logical":
            return f"T{track:02d}"
        return f"T{track + 1:02d}"

    def _sector_is_highlighted(self, row_index: int, sector_index: int) -> bool:
        if not self.disk_map or not getattr(self.disk_map, "highlighted_sectors", None):
            return False
        track_ids = self.disk_map.track_ids or [(idx, 0) for idx, _ in enumerate(self.disk_map.tracks)]
        if row_index >= len(track_ids) or not self.disk_map.sector_details or row_index >= len(self.disk_map.sector_details):
            return False
        details = self.disk_map.sector_details[row_index]
        if sector_index >= len(details):
            return False
        track, head = track_ids[row_index]
        return (track, head, details[sector_index].sector_id) in self.disk_map.highlighted_sectors

    def _sector_is_focused(self, row_index: int, sector_index: int) -> bool:
        return self._focused_sector == (row_index, sector_index)

    def _focused_address(self) -> Optional[tuple[int, int, int]]:
        if self._focused_sector is None:
            return None
        return self.sector_address_for_indices(*self._focused_sector)

    def sector_address_for_indices(self, row_index: int, sector_index: int) -> Optional[tuple[int, int, int]]:
        if not self.disk_map:
            return None
        track_ids = self.disk_map.track_ids or [(idx, 0) for idx, _ in enumerate(self.disk_map.tracks)]
        if row_index < 0 or row_index >= len(track_ids) or not self.disk_map.sector_details or row_index >= len(self.disk_map.sector_details):
            return None
        details = self.disk_map.sector_details[row_index]
        if sector_index < 0 or sector_index >= len(details):
            return None
        detail = details[sector_index]
        if detail.state.startswith("bam_") or not detail.has_data:
            return None
        track, head = track_ids[row_index]
        return track, head, detail.sector_id

    def _move_focus(self, row_delta: int, sector_delta: int) -> None:
        if not self.disk_map or not self.disk_map.tracks:
            return
        row, sector = self._focused_sector or (0, 0)
        row = max(0, min(len(self.disk_map.tracks) - 1, row + row_delta))
        sectors = self.disk_map.tracks[row]
        if not sectors:
            return
        sector = max(0, min(len(sectors) - 1, sector + sector_delta))
        self._focused_sector = (row, sector)
        self.update()

    def keyPressEvent(self, event) -> None:  # pragma: no cover - GUI interaction.
        if event.key() == Qt.Key_Left:
            self._move_focus(0, -1)
        elif event.key() == Qt.Key_Right:
            self._move_focus(0, 1)
        elif event.key() == Qt.Key_Up:
            self._move_focus(-1, 0)
        elif event.key() == Qt.Key_Down:
            self._move_focus(1, 0)
        elif event.key() in {Qt.Key_Return, Qt.Key_Enter, Qt.Key_D}:
            address = self._focused_address()
            if address is not None:
                if event.key() == Qt.Key_D:
                    self.sectorDiagnosticRequested.emit(*address)
                else:
                    self.sectorClicked.emit(*address)
        else:
            super().keyPressEvent(event)

    def _draw_legend(self, painter: QPainter, width: int, height: int) -> None:  # pragma: no cover - visual rendering.
        painter.setFont(QFont("Arial", 11))
        painter.setPen(QPen(QColor("#dce7f7"), 1))
        y = max(height - 28, 8)
        x = 18
        for state, label in self.legend_items():
            if state == "selected_file":
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(self.HIGHLIGHT_COLOR, 2.2))
                painter.drawRoundedRect(x, y + 4, 14, 14, 3, 3)
            else:
                painter.setBrush(QBrush(self.STATE_COLORS[state], self.STATE_BRUSH_STYLES.get(state, Qt.SolidPattern)))
                painter.setPen(QPen(QColor("#dce7f7"), 1))
                painter.drawRoundedRect(x, y + 4, 14, 14, 3, 3)
                painter.setFont(QFont("Arial", 8, QFont.Bold))
                painter.drawText(x, y + 4, 14, 14, Qt.AlignCenter, self.STATE_GLYPHS.get(state, "?"))
            painter.setPen(QPen(QColor("#dce7f7"), 1))
            painter.drawText(x + 20, y, max(width - x - 20, 1), 24, Qt.AlignLeft | Qt.AlignVCenter, label)
            x += 138 if state == "selected_file" else (116 if state != "unused" else 150)

    def mouseMoveEvent(self, event) -> None:  # pragma: no cover - GUI interaction.
        hit = self._hit_test(event.position().x(), event.position().y())
        if hit is None:
            QToolTip.hideText()
            return
        row_index, sector_index = hit
        QToolTip.showText(event.globalPosition().toPoint(), self.sector_detail_text(row_index, sector_index), self)

    def leaveEvent(self, _event) -> None:  # pragma: no cover - GUI interaction.
        QToolTip.hideText()

    def mousePressEvent(self, event) -> None:  # pragma: no cover - GUI interaction.
        if event.button() != Qt.LeftButton:
            return
        address = self.sector_address_at(event.position().x(), event.position().y())
        if address is not None:
            track, head, sector_id = address
            self.sectorClicked.emit(track, head, sector_id)

    def mouseDoubleClickEvent(self, event) -> None:  # pragma: no cover - GUI interaction.
        if event.button() != Qt.LeftButton:
            return
        address = self.sector_address_at(event.position().x(), event.position().y())
        if address is not None:
            self.sectorDiagnosticRequested.emit(*address)

    def sector_address_at(self, x: float, y: float) -> Optional[tuple[int, int, int]]:
        hit = self._hit_test(x, y)
        if hit is None or not self.disk_map:
            return None
        row_index, sector_index = hit
        track_ids = self.disk_map.track_ids or [(idx, 0) for idx, _ in enumerate(self.disk_map.tracks)]
        if row_index >= len(track_ids):
            return None
        track, head = track_ids[row_index]
        if not self.disk_map.sector_details or row_index >= len(self.disk_map.sector_details):
            return None
        details = self.disk_map.sector_details[row_index]
        if sector_index >= len(details):
            return None
        detail = details[sector_index]
        if detail.state.startswith("bam_") or not detail.has_data:
            return None
        return track, head, detail.sector_id

    def _hit_test(self, x: float, y: float) -> Optional[tuple[int, int]]:
        for layout in self._head_layouts:
            if layout.get("grid"):
                cell = float(layout["cell"])
                col = int((x - float(layout["left"])) // cell)
                row = int((y - float(layout["top"])) // cell)
                rows = layout["rows"]
                if row < 0 or row >= len(rows):
                    continue
                row_index, _track_id, sectors = rows[row]
                if col < 0 or col >= len(sectors):
                    continue
                return row_index, col
            dx = x - float(layout["cx"])
            dy = y - float(layout["cy"])
            distance = math.hypot(dx, dy)
            max_radius = float(layout["max_radius"])
            ring_width = float(layout["ring_width"])
            if distance > max_radius or distance <= max(max_radius - ring_width * len(layout["rows"]), 0):
                continue
            track_idx = int((max_radius - distance) // ring_width)
            rows = layout["rows"]
            if track_idx < 0 or track_idx >= len(rows):
                continue
            row_index, _track_id, sectors = rows[track_idx]
            if not sectors:
                continue
            angle = (90 - math.degrees(math.atan2(dy, dx))) % 360
            sector_index = int(angle / (360 / len(sectors)))
            sector_index = max(0, min(sector_index, len(sectors) - 1))
            return int(row_index), sector_index
        return None
