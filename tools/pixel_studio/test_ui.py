"""Small UI regressions: shared coordinates, picking, palette application and undo."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from pathlib import Path
from unittest.mock import patch
import tempfile
import numpy as np
from PySide6.QtCore import QPointF, Qt, QEvent
from PySide6.QtGui import QColor, QMouseEvent, QAction
from PySide6.QtWidgets import QApplication, QFileDialog
from .app import Preview, Window
from .core import new_project, ensure_group, convert_asset, save_png


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def preview(self):
        canvas = Preview()
        canvas.resize(800, 600)
        canvas.original = np.full((300, 500, 4), [20, 80, 140, 255], dtype=np.uint8)
        canvas.result = np.full((100, 100, 4), [20, 80, 140, 255], dtype=np.uint8)
        canvas.layout_spec = {"content": [0, 20, 100, 60]}
        return canvas

    def test_common_transform_and_panned_eyedropper(self):
        canvas = self.preview()
        canvas.zoom = 2.35
        canvas.pan = QPointF(45, -30)
        panes = canvas.panes()
        original, result = panes[0][3], panes[1][3]
        # Source (100, 150) maps to output (20, 50), with identical pane-local position.
        source_point = QPointF(original.x()+original.width()*.2, original.y()+original.height()*.5)
        output_point = QPointF(result.x()+result.width()*.2, result.y()+result.height()*.5)
        self.assertAlmostEqual(source_point.x(), output_point.x()-400)
        self.assertAlmostEqual(source_point.y(), output_point.y())
        center = original.center()
        self.assertEqual(canvas.pick_original(center).getRgb()[:3], (20, 80, 140))
        self.assertIsNone(canvas.pick_original(panes[1][2].center()))
        canvas.reset_view()
        self.assertEqual(canvas.pan, QPointF())
        self.assertEqual(canvas.zoom, 1)

    def test_right_drag_moves_both_panes_equally(self):
        canvas = self.preview()
        before = [p[3].topLeft() for p in canvas.panes()]
        for kind, position, button, buttons in [
            (QEvent.Type.MouseButtonPress, QPointF(200, 200), Qt.MouseButton.RightButton, Qt.MouseButton.RightButton),
            (QEvent.Type.MouseMove, QPointF(250, 225), Qt.MouseButton.NoButton, Qt.MouseButton.RightButton),
            (QEvent.Type.MouseButtonRelease, QPointF(250, 225), Qt.MouseButton.RightButton, Qt.MouseButton.NoButton)]:
            QApplication.sendEvent(canvas, QMouseEvent(kind, position, position, button, buttons, Qt.KeyboardModifier.NoModifier))
        for old, pane in zip(before, canvas.panes()):
            self.assertEqual(pane[3].topLeft()-old, QPointF(50, 25))

    def test_color_dialog_applies_on_ok_and_undo_redo_save(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/"sprite.png"
            save_png(source, np.full((3, 3, 4), [100, 90, 80, 255], dtype=np.uint8))
            window = Window()
            window.project = new_project()
            window.project["assets"] = [{"id":"sprite", "source":str(source), "group":"actor"}]
            ensure_group(window.project, "actor")["palette"] = [[100, 90, 80], [255, 255, 255]]
            window.refresh_assets()
            window.timer.stop()
            window.add_color()
            dialog = window.color_dialog
            dialog.pick()
            self.assertTrue(window.canvas.picking)
            dialog.picked(QColor(240, 5, 10))
            self.assertFalse(window.canvas.picking)
            dialog.accept()
            window.timer.stop()
            converted = convert_asset(window.project, window.current())
            self.assertEqual(converted["color"][50, 50, :3].tolist(), [240, 5, 10])
            window.undo(); window.timer.stop()
            self.assertEqual(window.project["groups"]["actor"]["palette"][0], [100, 90, 80])
            window.undo(True); window.timer.stop()
            self.assertEqual(window.project["groups"]["actor"]["palette"][0], [240, 5, 10])
            actions = {a.shortcut().toString(): a for a in window.findChildren(QAction) if not a.shortcut().isEmpty()}
            self.assertTrue({"Ctrl+S", "Ctrl+Z", "Ctrl+Shift+Z"}.issubset(actions))
            target = Path(directory)/"saved.pixel.json"
            with patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "")):
                actions["Ctrl+S"].trigger()
            self.assertTrue(target.is_file())
            self.assertFalse(window.dirty)
            window.hide()


if __name__ == "__main__":
    unittest.main()
