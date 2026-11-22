"""Desktop UI. All conversion/export work runs off the GUI thread."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import json
import math
import sys

import numpy as np
from PySide6.QtCore import Qt, Signal, QThread, QTimer, QRectF, QPointF
from PySide6.QtGui import QAction, QColor, QImage, QPainter, QPen, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QListWidget, QListWidgetItem, QFormLayout, QSpinBox, QDoubleSpinBox, QComboBox,
    QCheckBox, QPushButton, QLabel, QFileDialog, QMessageBox, QInputDialog, QSlider,
    QProgressBar, QColorDialog, QAbstractItemView, QScrollArea, QDialog, QDialogButtonBox,
    QPlainTextEdit, QGroupBox)

from .core import (Settings, new_project, load_project, save_project, add_pngs, ensure_group,
                   generate_group, convert_asset, export_batch, lit_preview, load_rgba, WEIGHTS)


class Job(QThread):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.completed.emit(self.operation(self))
        except Exception as exc:
            self.failed.emit(str(exc))


def qimage(array):
    rgba = np.ascontiguousarray(array)
    return QImage(rgba.data, rgba.shape[1], rgba.shape[0], rgba.strides[0], QImage.Format.Format_RGBA8888).copy()


class Preview(QWidget):
    light_changed = Signal(float)
    color_picked = Signal(object)
    pick_cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(400, 320)
        self.original = None
        self.result = None
        self.normal = None
        self.mode = "color"
        self.compare = True
        self.tile = False
        self.zoom = 1.0
        self.focus_content = True
        self.angle = 35
        self.layout_spec = None
        self.pan = QPointF()
        self.drag_position = None
        self.picking = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    def reset_view(self):
        self.zoom = 1.0
        self.pan = QPointF()
        self.update()

    def panes(self):
        """Both panes share one output-space frame, crop and continuous scale."""
        if self.result is None:
            return []
        display = self.result
        if self.mode == "normal" and self.normal is not None:
            display = self.normal
        elif self.mode == "light":
            display = lit_preview(self.result, self.normal, self.angle)
        h, w = self.result.shape[:2]
        content = QRectF(*(self.layout_spec or {}).get("content", [0, 0, w, h]))
        frame = QRectF(0, 0, w, h)
        view = QRectF(frame)
        if self.focus_content and self.original is not None:
            yy, xx = np.where(self.original[..., 3] > 0)
            if len(xx):
                oh, ow = self.original.shape[:2]
                bounds = QRectF(content.x()+xx.min()/ow*content.width(),
                                content.y()+yy.min()/oh*content.height(),
                                (xx.max()-xx.min()+1)/ow*content.width(),
                                (yy.max()-yy.min()+1)/oh*content.height())
                margin = max(bounds.width(), bounds.height())*.08
                view = bounds.adjusted(-margin, -margin, margin, margin).intersected(frame)
        images = [(self.original, "원본", content),
                  (display, f"결과 · {w} × {h} px", frame)] if self.compare else [(display, "변환 결과", frame)]
        pane_width = self.width()/len(images)
        result = []
        for i, (rgba, title, image_box) in enumerate(images):
            clip = QRectF(i*pane_width+12, 44, pane_width-24, self.height()-60)
            scale = min(clip.width()/view.width(), clip.height()/view.height())*self.zoom/(3 if self.tile else 1)
            origin = clip.center()+self.pan-QPointF(view.center().x()*scale, view.center().y()*scale)
            target = QRectF(origin.x()+image_box.x()*scale, origin.y()+image_box.y()*scale,
                            image_box.width()*scale, image_box.height()*scale)
            result.append((rgba, title, clip, target, QPointF(w*scale, h*scale)))
        return result

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#171d28"))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        if self.result is None:
            painter.setPen(QColor("#b9c6d9"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "PNG를 불러오면 여기에 변환 결과가 표시됩니다.\n\n휠: 확대/축소 · 우클릭 드래그: 함께 이동\n조명 보기에서 왼쪽 드래그: 광원 이동")
            return
        panes = self.panes()
        pane_width = self.width()/len(panes)
        for index, (rgba, title, rect, target, stride) in enumerate(panes):
            if rgba is None:
                continue
            painter.save()
            painter.setClipRect(rect)
            for y in range(int(rect.top()), int(rect.bottom())+16, 16):
                for x in range(int(rect.left()), int(rect.right())+16, 16):
                    painter.fillRect(x, y, 16, 16, QColor("#303949" if (x//16+y//16)%2 else "#252d3a"))
            positions = range(-1, 2) if self.tile else [0]
            picture = qimage(rgba)
            for ty in positions:
                for tx in positions:
                    painter.drawImage(target.translated(tx*stride.x(), ty*stride.y()), picture)
            painter.restore()
            painter.setPen(QColor("#d7e4f7"))
            painter.drawText(QRectF(index*pane_width+14, 8, pane_width-28, 28), Qt.AlignmentFlag.AlignLeft, title)

    def wheelEvent(self, event):
        previous = self.zoom
        self.zoom = min(16, max(.125, self.zoom * (1.25 if event.angleDelta().y() > 0 else .8)))
        self.pan *= self.zoom/previous
        self.update()
        event.accept()

    def pick_original(self, position):
        if not self.compare or self.original is None:
            return None
        rgba, _, clip, target, stride = self.panes()[0]
        if not clip.contains(position):
            return None
        for ty in (range(-1, 2) if self.tile else [0]):
            for tx in (range(-1, 2) if self.tile else [0]):
                rect = target.translated(tx*stride.x(), ty*stride.y())
                if rect.contains(position):
                    x = min(rgba.shape[1]-1, int((position.x()-rect.x())/rect.width()*rgba.shape[1]))
                    y = min(rgba.shape[0]-1, int((position.y()-rect.y())/rect.height()*rgba.shape[0]))
                    return QColor(*map(int, rgba[y, x, :3])) if rgba[y, x, 3] else None
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.drag_position = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self.picking and event.button() == Qt.MouseButton.LeftButton:
            color = self.pick_original(event.position())
            if color is not None:
                self.color_picked.emit(color)
        self.setFocus()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.drag_position = None
            self.setCursor(Qt.CursorShape.CrossCursor if self.picking else Qt.CursorShape.ArrowCursor)

    def keyPressEvent(self, event):
        if self.picking and event.key() == Qt.Key.Key_Escape:
            self.pick_cancelled.emit()
        else:
            super().keyPressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.RightButton and self.drag_position is not None:
            self.pan += event.position()-self.drag_position
            self.drag_position = event.position()
            self.update()
        elif not self.picking and self.mode == "light" and event.buttons() & Qt.MouseButton.LeftButton:
            self.angle = event.position().x() / max(1, self.width()) * 360
            self.light_changed.emit(self.angle)
            self.update()


class ColorChooser(QDialog):
    """Hide the chooser while sampling the original preview, then resume it."""
    def __init__(self, canvas, initial, selected_index, parent):
        super().__init__(parent)
        self.canvas = canvas
        self.setWindowTitle("색 추가 / 적용")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        layout = QVBoxLayout(self)
        self.colors = QColorDialog(initial, self)
        self.colors.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
        self.colors.setOption(QColorDialog.ColorDialogOption.NoButtons)
        self.colors.setWindowFlags(Qt.WindowType.Widget)
        layout.addWidget(self.colors)
        eyedropper = QPushButton("⌖ 스포이트 · 원본에서 색 추출")
        eyedropper.clicked.connect(self.pick)
        layout.addWidget(eyedropper)
        self.apply_mode = QComboBox()
        self.apply_mode.addItem("가장 비슷한 기존 색 교체 · 즉시 적용", "nearest")
        if selected_index >= 0:
            self.apply_mode.addItem("선택한 팔레트 색 교체 · 즉시 적용", "selected")
        self.apply_mode.addItem("팔레트에 추가만 · 가까운 원본 픽셀에 자동 사용", "append")
        layout.addWidget(self.apply_mode)
        note = QLabel("OK를 누르면 묶음 팔레트와 미리보기에 반영됩니다.\n추가만 선택하면 원본 색과 먼 새 색은 사용되지 않을 수 있습니다.")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        canvas.color_picked.connect(self.picked)
        canvas.pick_cancelled.connect(self.resume)
        self.finished.connect(self.cleanup)
        self.finished.connect(self.deleteLater)
        self.previous_compare = canvas.compare

    def pick(self):
        self.hide()
        self.canvas.compare = True
        self.canvas.picking = True
        self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        self.canvas.setFocus()
        self.canvas.update()
        self.parent().statusBar().showMessage("원본 미리보기의 색을 왼쪽 클릭하세요 · 우클릭 드래그로 이동 · Esc로 색 선택창 복귀")

    def picked(self, color):
        if self.canvas.picking:
            self.colors.setCurrentColor(color)
            self.resume()

    def resume(self):
        if not self.canvas.picking:
            return
        self.canvas.picking = False
        self.canvas.setCursor(Qt.CursorShape.ArrowCursor)
        self.canvas.compare = self.previous_compare
        self.canvas.update()
        self.show()
        self.raise_()
        self.activateWindow()

    def cleanup(self, *_):
        self.canvas.picking = False
        self.canvas.setCursor(Qt.CursorShape.ArrowCursor)
        self.canvas.compare = self.previous_compare
        self.canvas.update()
        self.canvas.color_picked.disconnect(self.picked)
        self.canvas.pick_cancelled.disconnect(self.resume)
        self.parent().statusBar().showMessage("색 선택 완료")


class Window(QMainWindow):
    def __init__(self, project_path=None):
        super().__init__()
        self.project = new_project()
        self.path = None
        self.dirty = False
        self.job = None
        self.pending = False
        self.revision = 0
        self.busy_export = False
        self.failed_ids = None
        self.output = None
        self.history = []
        self.future = []
        self.last_result = None
        self.color_dialog = None
        self.setWindowTitle("Pixel Studio · PNG 에셋 작업실")
        self.resize(1420, 900)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.preview)
        self.animation = QTimer(self)
        self.animation.setInterval(160)
        self.animation.timeout.connect(self.next_frame)
        toolbar = self.addToolBar("파일")
        for label, slot in [("빈 프로젝트", self.blank_project), ("PNG 추가", self.import_png), ("폴더 추가", self.import_folder),
                            ("프로젝트 열기", self.open_project), ("카탈로그 가져오기", self.import_catalog),
                            ("프로젝트 저장", self.save), ("다른 이름 저장", lambda: self.save(True))]:
            action = toolbar.addAction(label, slot)
            if label == "프로젝트 저장":
                action.setShortcut(QKeySequence("Ctrl+S"))
        undo = QAction("실행 취소", self)
        undo.setShortcut(QKeySequence("Ctrl+Z"))
        undo.triggered.connect(self.undo)
        self.addAction(undo)
        toolbar.addAction(undo)
        redo = QAction("다시 실행", self)
        redo.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        redo.triggered.connect(lambda: self.undo(True))
        self.addAction(redo)
        toolbar.addAction(redo)
        splitter = QSplitter()
        self.setCentralWidget(splitter)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("에셋 · 같은 객체의 방향/프레임은 같은 묶음"))
        self.assets = QListWidget()
        self.assets.setWordWrap(False)
        self.assets.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.assets.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.assets.currentRowChanged.connect(self.selection_changed)
        ll.addWidget(self.assets)
        for label, slot in [("선택 항목 묶음 지정", self.group_selected), ("선택 항목 노멀 연결", self.link_normal),
                            ("프레임 / 피벗 / 개별 설정", self.edit_asset), ("목록에서 제거", self.remove_assets)]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            ll.addWidget(button)
        splitter.addWidget(left)
        middle = QWidget()
        ml = QVBoxLayout(middle)
        view_controls = QHBoxLayout()
        self.view_mode = QComboBox()
        self.view_mode.addItems(["컬러", "노멀", "조명"])
        self.view_mode.currentIndexChanged.connect(self.view_changed)
        view_controls.addWidget(self.view_mode)
        compare = QCheckBox("원본 비교")
        compare.setChecked(True)
        compare.toggled.connect(lambda v: self.set_view("compare", v))
        view_controls.addWidget(compare)
        focus = QCheckBox("내용 확대")
        focus.setChecked(True)
        focus.setToolTip("미리보기의 투명 여백만 숨깁니다. 출력 크기와 피벗은 유지됩니다.")
        focus.toggled.connect(lambda v: self.set_view("focus_content", v))
        view_controls.addWidget(focus)
        tile = QCheckBox("3×3 반복")
        tile.toggled.connect(lambda v: self.set_view("tile", v))
        view_controls.addWidget(tile)
        play = QPushButton("묶음 재생 / 정지")
        play.clicked.connect(lambda: self.animation.stop() if self.animation.isActive() else self.animation.start())
        view_controls.addWidget(play)
        reset = QPushButton("화면 맞춤")
        reset.clicked.connect(lambda: self.canvas.reset_view())
        view_controls.addWidget(reset)
        ml.addLayout(view_controls)
        self.canvas = Preview()
        ml.addWidget(self.canvas, 1)
        self.info = QLabel("원본을 보존하며 PNG로 출력합니다.")
        self.info.setWordWrap(True)
        ml.addWidget(self.info)
        self.progress = QProgressBar()
        self.progress.hide()
        ml.addWidget(self.progress)
        splitter.addWidget(middle)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setMinimumWidth(330)
        right = QWidget()
        rl = QVBoxLayout(right)
        self.controls = {}
        box = QGroupBox("프로젝트 변환 규약")
        form = QFormLayout(box)
        preset = QComboBox()
        preset.addItems(["사용자 설정", "거친 레트로 · 64px / 16색", "중간 레트로 · 100px / 32색", "세밀한 레트로 · 160px / 64색"])
        preset.activated.connect(self.preset)
        form.addRow("스타일", preset)
        for key, label, items in [("mode", "해상도 방식", [("고정 출력 크기", "target"), ("원본 픽셀 간격", "step")]),
                                  ("fit", "크기 맞춤", [("비율 유지 + 투명 여백", "contain"), ("가로·세로 맞춤", "stretch")]),
                                  ("alpha_mode", "투명도", [("불투명 / 투명", "binary"), ("반투명 유지", "preserve")])]:
            control = QComboBox()
            for title, value in items:
                control.addItem(title, value)
            self.controls[key] = control
            control.currentIndexChanged.connect(self.settings_changed)
            form.addRow(label, control)
        for key, label, low, high in [("width", "출력 가로 px", 1, 4096), ("height", "출력 세로 px", 1, 4096),
                                      ("pixel_step", "원본 픽셀 간격", 1, 128), ("colors", "자동 팔레트 색 수", 2, 255),
                                      ("alpha_threshold", "투명도 경계", 1, 255), ("normal_levels", "노멀 방향 단계 (0: 유지)", 0, 32)]:
            control = QSpinBox()
            control.setRange(low, high)
            self.controls[key] = control
            control.valueChanged.connect(self.settings_changed)
            form.addRow(label, control)
        dither = QDoubleSpinBox()
        dither.setRange(0, 1)
        dither.setSingleStep(.1)
        dither.valueChanged.connect(self.settings_changed)
        self.controls["dither"] = dither
        form.addRow("디더링", dither)
        for key, label in [("outline", "안쪽 1px 외곽선"), ("normal_flip_y", "노멀 Y 반전"), ("normal_flip_x", "노멀 X 반전")]:
            control = QCheckBox(label)
            control.toggled.connect(self.settings_changed)
            self.controls[key] = control
            form.addRow(control)
        rl.addWidget(box)
        self.group_label = QLabel("묶음 팔레트")
        rl.addWidget(self.group_label)
        self.palette = QListWidget()
        self.palette.setViewMode(QListWidget.ViewMode.IconMode)
        self.palette.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.palette.setMaximumHeight(185)
        self.palette.setMinimumHeight(115)
        self.palette.itemDoubleClicked.connect(self.edit_color)
        rl.addWidget(self.palette)
        for label, slot in [("자동 팔레트 생성 / 재분석", self.regenerate), ("색 추가", self.add_color),
                            ("선택 색 삭제", self.delete_color), ("선택 색 잠금 / 해제", self.lock_color),
                            ("묶음별 설정 편집", self.edit_group)]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            rl.addWidget(button)
        note = QLabel("확정 팔레트는 자동으로 덮어쓰지 않습니다.\n색 수 변경 후 재분석하면 잠근 색을 유지합니다.")
        note.setWordWrap(True)
        rl.addWidget(note)
        self.incremental = QCheckBox("변경된 항목만 출력")
        self.incremental.setChecked(True)
        rl.addWidget(self.incremental)
        for label, slot in [("선택 PNG 출력", lambda: self.export(False)), ("전체 PNG 출력", lambda: self.export(True)),
                            ("실패 항목 재시도", self.retry), ("작업 취소", self.cancel)]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            rl.addWidget(button)
        rl.addStretch()
        right_scroll.setWidget(right)
        splitter.addWidget(right_scroll)
        splitter.setSizes([260, 780, 340])
        self.refresh_controls()
        self.statusBar().showMessage("준비됨 · PNG 입출력 · 원본 보존")
        if project_path:
            self.read_project(Path(project_path))

    def current(self):
        row = self.assets.currentRow()
        return self.project["assets"][row] if 0 <= row < len(self.project["assets"]) else None

    def selected(self):
        return [self.project["assets"][self.assets.row(item)] for item in self.assets.selectedItems()]

    def checkpoint(self):
        self.history.append(deepcopy(self.project))
        self.history = self.history[-30:]
        self.future.clear()
        self.dirty = True

    def changed(self):
        self.dirty = True
        self.revision += 1
        self.timer.start()
        self.setWindowTitle("Pixel Studio · " + (self.path.name if self.path else "새 프로젝트") + " *")

    def refresh_assets(self):
        row = self.assets.currentRow()
        self.assets.blockSignals(True)
        self.assets.clear()
        for asset in self.project["assets"]:
            filename = Path(asset["source"]).name
            item = QListWidgetItem(filename + (" · 노멀" if asset.get("normal") else ""))
            item.setToolTip(f"파일: {filename}\n묶음: {asset['group']}\n원본: {asset['source']}")
            self.assets.addItem(item)
        self.assets.blockSignals(False)
        self.assets.setCurrentRow(min(max(row, 0), self.assets.count()-1))
        if not self.assets.count():
            self.canvas.original = self.canvas.result = self.canvas.normal = None
            self.canvas.update()

    def refresh_controls(self):
        for key, control in self.controls.items():
            control.blockSignals(True)
            value = self.project["settings"][key]
            if isinstance(control, QComboBox):
                control.setCurrentIndex(control.findData(value))
            elif isinstance(control, QCheckBox):
                control.setChecked(value)
            else:
                control.setValue(value)
            control.blockSignals(False)

    def settings_changed(self, *_):
        if self.busy_export:
            self.refresh_controls()
            return
        self.checkpoint()
        for key, control in self.controls.items():
            if isinstance(control, QComboBox):
                value = control.currentData()
            elif isinstance(control, QCheckBox):
                value = control.isChecked()
            else:
                value = control.value()
            self.project["settings"][key] = value
        self.changed()

    def preset(self, index):
        if index and not self.busy_export:
            self.checkpoint()
            size, colors = [(64, 16), (100, 32), (160, 64)][index-1]
            self.project["settings"].update(mode="target", width=size, height=size, colors=colors, dither=0.0)
            self.refresh_controls()
            self.changed()

    def selection_changed(self, *_):
        self.revision += 1
        self.refresh_palette()
        self.timer.start()

    def refresh_palette(self):
        self.palette.clear()
        asset = self.current()
        if not asset:
            return
        group = ensure_group(self.project, asset["group"])
        self.group_label.setText(f"묶음: {asset['group']} · {len(group['palette'])}색")
        for i, rgb in enumerate(group["palette"]):
            color = QColor(*rgb)
            item = QListWidgetItem(("🔒 " if i in group.get("locked", []) else "") + color.name())
            item.setBackground(color)
            item.setForeground(QColor("#111111" if color.lightness() > 128 else "#ffffff"))
            self.palette.addItem(item)

    def start_job(self, operation, callback):
        if self.job:
            return False
        self.job = Job(operation, self)
        self.job.completed.connect(callback)
        self.job.failed.connect(self.error)
        self.job.progress.connect(self.show_progress)
        self.job.finished.connect(self.job_finished)
        self.job.start()
        return True

    def job_finished(self):
        self.job.deleteLater()
        self.job = None
        self.busy_export = False
        self.progress.hide()
        if self.pending:
            self.pending = False
            self.timer.start()

    def preview(self):
        asset = self.current()
        if not asset:
            return
        if self.job:
            self.pending = True
            return
        project, member, revision = deepcopy(self.project), deepcopy(asset), self.revision
        def work(job):
            result = convert_asset(project, member)
            return result, project, revision
        def completed(payload):
            result, snapshot, requested = payload
            if requested != self.revision:
                self.pending = True
                return
            # Only auto-generated palettes are copied back; edits never race a job.
            self.project["groups"] = snapshot["groups"]
            self.last_result = result
            self.canvas.original, self.canvas.result, self.canvas.normal = result["source"], result["color"], result["normal"]
            self.canvas.layout_spec = result["layout"]
            self.canvas.update()
            w, h = result["layout"]["size"]
            maps = 2 if result["normal"] is not None else 1
            source_bytes = result["source"].shape[0]*result["source"].shape[1]*4*maps
            output_bytes = w*h*4*maps
            self.info.setText(f"출력 {w} × {h}px · 팔레트 {len(result['palette'])}색 · RGBA8 메모리 추정 {output_bytes/1024:.1f} KiB"
                              f" (원본 대비 {output_bytes/source_bytes:.1%}, 밉맵 제외)\nPNG 파일 크기는 출력 후 보고서에서 확인합니다.")
            self.refresh_palette()
            self.statusBar().showMessage("미리보기 갱신 완료")
        self.start_job(work, completed)

    def show_progress(self, done, total, name):
        self.progress.show()
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.statusBar().showMessage(f"{done}/{total} · {name}")

    def error(self, message):
        self.statusBar().showMessage(message)
        self.info.setText(message)

    def import_png(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "컬러 PNG 추가", "", "PNG (*.png)")
        if paths:
            self.add(paths)

    def import_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "PNG 폴더 추가")
        if folder:
            self.add([str(p) for p in Path(folder).rglob("*") if p.suffix.lower() == ".png"])

    def add(self, paths):
        if self.busy_export:
            return
        self.checkpoint()
        count = add_pngs(self.project, paths)
        self.refresh_assets()
        self.changed()
        self.statusBar().showMessage(f"{count}개 추가 · 같은 객체의 이미지들을 선택해 묶음을 지정하세요.")

    def group_selected(self):
        if not self.selected() or self.busy_export:
            return
        name, ok = QInputDialog.getText(self, "묶음 지정", "같은 객체 / 팔레트를 공유할 묶음 이름", text=self.selected()[0]["group"])
        if ok and name.strip():
            self.checkpoint()
            for asset in self.selected():
                asset["group"] = name.strip()
            ensure_group(self.project, name.strip())
            self.refresh_assets()
            self.changed()

    def link_normal(self):
        asset = self.current()
        if not asset or self.busy_export:
            return
        path, _ = QFileDialog.getOpenFileName(self, "대응하는 노멀 PNG", "", "PNG (*.png)")
        if path:
            self.checkpoint()
            asset["normal"] = path
            self.refresh_assets()
            self.changed()

    def remove_assets(self):
        if self.busy_export:
            return
        ids = {a["id"] for a in self.selected()}
        self.checkpoint()
        self.project["assets"] = [a for a in self.project["assets"] if a["id"] not in ids]
        self.refresh_assets()
        self.changed()

    def json_dialog(self, title, value):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(550, 460)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(json.dumps(value, ensure_ascii=False, indent=2))
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            try:
                result = json.loads(text.toPlainText())
                if not isinstance(result, dict):
                    raise ValueError("객체 형식이어야 합니다.")
                return result
            except Exception as exc:
                self.error(str(exc))
        return None

    def edit_asset(self):
        asset = self.current()
        if not asset or self.busy_export:
            return
        result = self.json_dialog("원본 영역 region [x,y,w,h] · 피벗 [x,y] · 설정", {
            "region": asset.get("region"), "pivot": asset.get("pivot"), "metadata": asset.get("metadata", {}),
            "settings": asset.get("settings", {}), "normal": asset.get("normal")})
        if result is not None:
            try:
                Settings.from_dict({**self.project["settings"], **result.get("settings", {})})
                for key, length in (("region", 4), ("pivot", 2)):
                    if result.get(key) is not None and (not isinstance(result[key], list) or len(result[key]) != length):
                        raise ValueError(f"{key}: 숫자 {length}개가 필요합니다.")
                self.checkpoint()
                for key in ("region", "pivot", "metadata", "settings", "normal"):
                    if result.get(key) is not None:
                        asset[key] = result[key]
                    else:
                        asset.pop(key, None)
                self.changed()
            except Exception as exc:
                self.error(str(exc))

    def edit_group(self):
        asset = self.current()
        if not asset or self.busy_export:
            return
        group = self.project["groups"][asset["group"]]
        result = self.json_dialog("이 묶음에만 적용할 설정 · 비워두면 프로젝트 규약 사용", group.get("settings", {}))
        if result is not None:
            try:
                Settings.from_dict({**self.project["settings"], **result})
                self.checkpoint()
                group["settings"] = result
                self.changed()
            except Exception as exc:
                self.error(str(exc))

    def regenerate(self):
        asset = self.current()
        if not asset or self.job:
            return
        self.checkpoint()
        project, name, revision = deepcopy(self.project), asset["group"], self.revision
        def work(job):
            generate_group(project, name, True)
            return project, revision
        def complete(payload):
            snapshot, requested = payload
            if self.revision == requested:
                self.project["groups"][name] = snapshot["groups"][name]
                self.changed()
                self.refresh_palette()
            self.pending = True
        self.start_job(work, complete)

    def mutate_color(self, operation):
        asset = self.current()
        if not asset or self.busy_export:
            return
        self.checkpoint()
        operation(self.project["groups"][asset["group"]])
        self.refresh_palette()
        self.changed()

    def edit_color(self, item):
        index = self.palette.row(item)
        group = self.project["groups"][self.current()["group"]]
        color = QColorDialog.getColor(QColor(*group["palette"][index]), self)
        if color.isValid():
            self.mutate_color(lambda g: g["palette"].__setitem__(index, [color.red(), color.green(), color.blue()]))

    def add_color(self):
        if not self.current() or self.busy_export:
            return
        if self.color_dialog is not None:
            self.color_dialog.show()
            self.color_dialog.raise_()
            return
        self.animation.stop()
        group_id = self.current()["group"]
        group = ensure_group(self.project, group_id)
        index = self.palette.currentRow()
        initial = QColor(*group["palette"][index]) if 0 <= index < len(group["palette"]) else QColor("#ffffff")
        project = self.project
        dialog = ColorChooser(self.canvas, initial, index, self)
        self.color_dialog = dialog
        def apply():
            if self.project is project and group_id in self.project["groups"]:
                self.apply_chosen_color(group_id, dialog.colors.currentColor(), dialog.apply_mode.currentData(), index)
        dialog.accepted.connect(apply)
        dialog.finished.connect(lambda _: setattr(self, "color_dialog", None))
        dialog.show()

    def apply_chosen_color(self, group_id, color, mode, index=-1):
        if self.busy_export or not color.isValid():
            return
        group = self.project["groups"][group_id]
        palette = group["palette"]
        rgb = [color.red(), color.green(), color.blue()]
        if mode == "append" or not palette:
            if len(palette) >= 255:
                return self.error("최대 255색입니다.")
            if rgb in palette:
                return
            index = len(palette)
        elif mode == "nearest":
            index = int(np.argmin(((np.asarray(palette)-rgb)**2) @ WEIGHTS))
        elif not 0 <= index < len(palette):
            return self.error("교체할 팔레트 색을 다시 선택하세요.")
        self.checkpoint()
        if index == len(palette):
            palette.append(rgb)
        else:
            palette[index] = rgb
        self.refresh_palette()
        self.changed()

    def delete_color(self):
        index = self.palette.currentRow()
        if index < 0 or self.palette.count() <= 1:
            return
        def remove(group):
            group["palette"].pop(index)
            group["locked"] = [i if i < index else i-1 for i in group.get("locked", []) if i != index]
        self.mutate_color(remove)

    def lock_color(self):
        index = self.palette.currentRow()
        if index < 0:
            return
        def toggle(group):
            locked = group.setdefault("locked", [])
            locked.remove(index) if index in locked else locked.append(index)
        self.mutate_color(toggle)

    def view_changed(self, index):
        self.set_view("mode", ["color", "normal", "light"][index])

    def set_view(self, key, value):
        setattr(self.canvas, key, value)
        self.canvas.update()

    def next_frame(self):
        asset = self.current()
        if not asset or self.job:
            return
        members = [i for i, a in enumerate(self.project["assets"]) if a["group"] == asset["group"]]
        self.assets.setCurrentRow(members[(members.index(self.assets.currentRow())+1) % len(members)])

    def undo(self, redo=False):
        if self.busy_export:
            return
        source, target = (self.future, self.history) if redo else (self.history, self.future)
        if source:
            target.append(deepcopy(self.project))
            self.project = source.pop()
            self.refresh_controls()
            self.refresh_assets()
            self.changed()

    def save(self, save_as=False):
        path = str(self.path) if self.path and not save_as else QFileDialog.getSaveFileName(self, "프로젝트 저장", "project.pixel.json", "Pixel project (*.pixel.json)")[0]
        if not path:
            return False
        try:
            save_project(Path(path), self.project)
            self.path = Path(path)
            self.dirty = False
            self.setWindowTitle("Pixel Studio · " + self.path.name)
            self.statusBar().showMessage("저장 완료 · 월드 에디터에서 이 프로젝트를 불러올 수 있습니다.")
            return True
        except Exception as exc:
            self.error(str(exc))
            return False

    def can_replace(self):
        if not self.dirty:
            return True
        answer = QMessageBox.question(self, "프로젝트 저장", "변경한 프로젝트를 저장할까요?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        return self.save() if answer == QMessageBox.StandardButton.Save else answer == QMessageBox.StandardButton.Discard

    def open_project(self):
        if self.job or not self.can_replace():
            return
        path, _ = QFileDialog.getOpenFileName(self, "프로젝트 열기", "", "Pixel project (*.json)")
        if path:
            self.read_project(Path(path))

    def blank_project(self):
        if self.busy_export:
            self.statusBar().showMessage("출력이 끝나거나 취소된 후 빈 프로젝트를 시작하세요.")
            return
        if not self.can_replace():
            return
        self.animation.stop()
        self.timer.stop()
        self.revision += 1  # Discard an in-flight preview from the previous project.
        self.pending = False
        self.project = new_project()
        self.path = None
        self.dirty = False
        self.history.clear()
        self.future.clear()
        self.failed_ids = None
        self.output = None
        self.last_result = None
        self.refresh_controls()
        self.refresh_assets()
        self.palette.clear()
        self.group_label.setText("묶음 팔레트")
        self.view_mode.setCurrentIndex(0)
        self.canvas.reset_view()
        self.progress.hide()
        self.info.setText("원본을 보존하며 PNG로 출력합니다.")
        self.setWindowTitle("Pixel Studio · 새 프로젝트")
        self.statusBar().showMessage("빈 프로젝트 · PNG를 추가하세요.")

    def read_project(self, path):
        try:
            project = load_project(path)
            self.project, self.path = project, path
            self.history.clear()
            self.future.clear()
            self.refresh_controls()
            self.refresh_assets()
            self.revision += 1
            self.dirty = False
            self.timer.start()
            self.setWindowTitle("Pixel Studio · " + path.name)
        except Exception as exc:
            self.error(str(exc))

    def import_catalog(self):
        if self.busy_export:
            return
        path, _ = QFileDialog.getOpenFileName(self, "에셋 카탈로그 가져오기", "", "JSON (*.json)")
        if not path:
            return
        try:
            from .catalog import import_catalog
            self.checkpoint()
            report = import_catalog(self.project, Path(path))
            self.refresh_assets()
            self.changed()
            self.info.setText(report)
        except Exception as exc:
            self.error(str(exc))

    def export(self, all_assets=False, retry_ids=None):
        if self.job:
            return self.error("현재 미리보기 처리가 끝난 뒤 출력하세요.")
        selected = self.project["assets"] if all_assets else self.selected()
        ids = retry_ids if retry_ids is not None else [a["id"] for a in selected]
        if not ids:
            return self.error("출력할 에셋을 선택하세요.")
        folder = str(self.output) if retry_ids is not None and self.output else QFileDialog.getExistingDirectory(self, "출력 폴더 선택")
        if not folder:
            return
        self.output = Path(folder)
        self.animation.stop()
        snapshot = deepcopy(self.project)
        incremental = self.incremental.isChecked()
        self.busy_export = True
        def work(job):
            report = export_batch(snapshot, self.output, ids,
                lambda d, t, n: job.progress.emit(d, t, n), job.isInterruptionRequested, incremental)
            return report, snapshot
        def complete(payload):
            report, project = payload
            self.project["groups"] = project["groups"]
            self.dirty = True
            self.failed_ids = [a["id"] for a in report["failed"]]
            message = f"출력 {report['written']} · 변경 없음 {report['skipped']} · 실패 {len(report['failed'])}"
            if report["cancelled"]:
                message += " · 취소됨 (완료 항목 보존)"
            self.info.setText(message + "\n" + str(self.output) + "\n" + "\n".join(f"{a['id']}: {a['error']}" for a in report["failed"]))
            self.statusBar().showMessage(message)
            self.refresh_palette()
        self.start_job(work, complete)

    def retry(self):
        if self.failed_ids:
            self.export(retry_ids=self.failed_ids)

    def cancel(self):
        if self.job:
            self.job.requestInterruption()
            self.statusBar().showMessage("현재 항목을 마친 후 취소합니다.")

    def closeEvent(self, event):
        if self.job:
            self.cancel()
            event.ignore()
            self.statusBar().showMessage("현재 처리가 끝나면 창을 닫을 수 있습니다.")
        elif self.can_replace():
            event.accept()
        else:
            event.ignore()


def run(project=None):
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("Pixel Studio")
    application.setStyle("Fusion")
    application.setStyleSheet("""
        QMainWindow, QWidget { background: #202835; color: #e0e8f4; }
        QListWidget, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #151c27; padding: 4px; }
        QPushButton { background: #34465f; border: 1px solid #4a607e; border-radius: 4px; padding: 6px; }
        QPushButton:hover { background: #466386; }
        QGroupBox { border: 1px solid #435168; border-radius: 5px; margin-top: 12px; padding-top: 12px; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; }
        QListWidget::item:selected { border: 2px solid #6cb8ff; background: #36587c; color: #ffffff; }
        QToolBar { spacing: 8px; padding: 7px; }
        QProgressBar::chunk { background: #60a7d8; }
    """)
    window = Window(project)
    window.show()
    return application.exec()
