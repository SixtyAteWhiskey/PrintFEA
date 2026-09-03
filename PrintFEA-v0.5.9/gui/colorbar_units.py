"""Small screen-space label for FreeCAD's native FEM color scale.

FreeCAD's VTK result pipeline intentionally renders its color bar in SI units.
The bar itself does not expose a public Python API for adding a quantity/unit
caption, so PrintFEA places a lightweight Qt label next to the native bar.
"""

import FreeCADGui as Gui
from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:
    QtWidgets = QtGui


class ColorBarUnitOverlay(QtCore.QObject):
    """Keep a quantity + unit caption anchored near the native FEM color bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._viewer_widget = None
        self._label = None
        self._quantity = None

    @staticmethod
    def _active_viewer_widget():
        try:
            gui_doc = Gui.activeDocument()
            if gui_doc is None:
                return None
            view = getattr(gui_doc, "ActiveView", None)
            if view is None and hasattr(gui_doc, "activeView"):
                view = gui_doc.activeView()
            if view is None:
                return None
            viewer = view.getViewer()
            if viewer is None:
                return None
            return viewer.getWidget()
        except Exception:
            return None

    def _ensure_label(self):
        widget = self._active_viewer_widget()
        if widget is None:
            return False

        if self._viewer_widget is not widget or self._label is None:
            self.hide()
            self._viewer_widget = widget
            self._label = QtWidgets.QLabel(widget)
            self._label.setObjectName("PrintFEAColorBarUnits")
            self._label.setWordWrap(False)
            self._label.setStyleSheet(
                "QLabel#PrintFEAColorBarUnits {"
                " color: white;"
                " background-color: rgba(0, 0, 0, 175);"
                " border: 1px solid rgba(255, 255, 255, 90);"
                " border-radius: 4px;"
                " padding: 5px 7px;"
                "}"
            )
            try:
                attr = QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents
            except AttributeError:
                attr = QtCore.Qt.WA_TransparentForMouseEvents
            self._label.setAttribute(attr, True)
            widget.installEventFilter(self)
        return True

    def show_quantity(self, quantity):
        """Show the SI quantity/unit used by FreeCAD's native pipeline scale."""
        if quantity not in ("stress", "displacement", "utilization"):
            self.hide()
            return False
        if not self._ensure_label():
            return False

        self._quantity = quantity
        if quantity == "stress":
            self._label.setText("Von Mises stress\nNative color scale: Pa")
            self._label.setToolTip(
                "FreeCAD's post-pipeline color scale is SI: stress is shown in pascals (Pa). "
                "PrintFEA's result summary converts stress to MPa."
            )
        elif quantity == "utilization":
            self._label.setText("FDM failure utilization\nDimensionless ratio\n1.0 = directional allowable")
            self._label.setToolTip(
                "Layer-aware FDM utilization is dimensionless. 1.0 means the selected directional material allowable has been reached."
            )
        else:
            self._label.setText("Displacement magnitude\nNative color scale: m")
            self._label.setToolTip(
                "FreeCAD's post-pipeline color scale is SI: displacement is shown in metres (m). "
                "PrintFEA's result summary converts displacement to mm."
            )

        self._label.adjustSize()
        self._position()
        self._label.show()
        self._label.raise_()
        return True

    def _position(self):
        if self._viewer_widget is None or self._label is None:
            return
        try:
            self._label.adjustSize()
            # FreeCAD draws the vertical native color scale close to the right
            # edge of the 3D viewport. Put the caption immediately to its left,
            # near the top of the scale, while avoiding the navigation cube.
            x = max(10, self._viewer_widget.width() - self._label.width() - 120)
            y = max(85, int(self._viewer_widget.height() * 0.24))
            self._label.move(x, y)
            self._label.raise_()
        except RuntimeError:
            self._viewer_widget = None
            self._label = None

    def eventFilter(self, watched, event):
        try:
            event_type = event.type()
            qevent_type = getattr(QtCore.QEvent, "Type", None)
            resize = getattr(qevent_type, "Resize", None) if qevent_type else None
            show = getattr(qevent_type, "Show", None) if qevent_type else None
            if resize is None:
                resize = QtCore.QEvent.Resize
            if show is None:
                show = QtCore.QEvent.Show
            if event_type in (resize, show):
                self._position()
        except Exception:
            pass
        return False

    def hide(self):
        if self._viewer_widget is not None:
            try:
                self._viewer_widget.removeEventFilter(self)
            except (RuntimeError, AttributeError):
                pass
        if self._label is not None:
            try:
                self._label.hide()
                self._label.deleteLater()
            except RuntimeError:
                pass
        self._viewer_widget = None
        self._label = None
        self._quantity = None
