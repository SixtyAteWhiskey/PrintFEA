"""Side-by-side comparison of saved PrintFEA result summaries."""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui
try:
    from PySide import QtWidgets
except ImportError:
    QtWidgets = QtGui

from gui.results_dialog import recent_summary_objects


def _f(obj, name, default=None):
    try:
        return float(getattr(obj, name))
    except Exception:
        return default


def _pct(v):
    return "—" if v is None else f"{v*100.0:.1f}%"


def _num(v, suffix="", digits=2):
    return "—" if v is None else f"{v:.{digits}f}{suffix}"


def _label(obj):
    return f"{getattr(obj, 'RunTimestamp', '')} — {getattr(obj, 'Verdict', '')} — {getattr(obj, 'MaterialProfile', '')}"


class CompareRunsDialog(QtWidgets.QDialog):
    def __init__(self, doc=None, parent=None):
        super().__init__(parent or Gui.getMainWindow())
        self.doc = doc or App.ActiveDocument
        self.setWindowTitle("PrintFEA — Compare Runs")
        self.setModal(False)
        self.resize(780, 520)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("<h2>Compare PrintFEA runs</h2><p>Compare saved runs from the active FreeCAD document. Differences are shown as Run B minus Run A.</p>")
        title.setWordWrap(True)
        root.addWidget(title)
        selectors = QtWidgets.QGridLayout()
        self.a = QtWidgets.QComboBox(); self.b = QtWidgets.QComboBox()
        self.a.currentIndexChanged.connect(self._render)
        self.b.currentIndexChanged.connect(self._render)
        selectors.addWidget(QtWidgets.QLabel("Run A"),0,0); selectors.addWidget(self.a,0,1)
        selectors.addWidget(QtWidgets.QLabel("Run B"),1,0); selectors.addWidget(self.b,1,1)
        root.addLayout(selectors)

        self.table = QtWidgets.QTableWidget(0,4)
        self.table.setHorizontalHeaderLabels(["Metric","Run A","Run B","Δ B − A"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        try:
            self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
            self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
            self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
            self.table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        except Exception:
            pass
        root.addWidget(self.table,1)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.close)
        root.addWidget(close)

    def refresh(self):
        runs = recent_summary_objects(self.doc)
        self.a.blockSignals(True); self.b.blockSignals(True)
        self.a.clear(); self.b.clear()
        for obj in runs:
            self.a.addItem(_label(obj), obj.Name); self.b.addItem(_label(obj), obj.Name)
        self.a.blockSignals(False); self.b.blockSignals(False)
        if len(runs)>1:
            self.a.setCurrentIndex(1); self.b.setCurrentIndex(0)
        elif runs:
            self.a.setCurrentIndex(0); self.b.setCurrentIndex(0)
        self._render()

    def _obj(self, combo):
        if combo.currentIndex()<0 or self.doc is None: return None
        return self.doc.getObject(str(combo.itemData(combo.currentIndex())))

    def _render(self,*_args):
        a,b=self._obj(self.a),self._obj(self.b)
        self.table.setRowCount(0)
        if a is None or b is None: return
        rows=[]
        # caption, formatted A/B, numeric A/B for delta formatting
        rows.append(("Verdict", str(getattr(a,"Verdict","—")), str(getattr(b,"Verdict","—")), ""))
        sa,sb=_f(a,"EstimatedSafetyFactor"),_f(b,"EstimatedSafetyFactor")
        rows.append(("Safety factor",_num(sa),_num(sb),_num(None if sa is None or sb is None else sb-sa, digits=2)))
        ma,mb=_f(a,"MaxDisplacementMM"),_f(b,"MaxDisplacementMM")
        rows.append(("Max movement",_num(ma," mm",3),_num(mb," mm",3),_num(None if ma is None or mb is None else mb-ma," mm",3)))
        ua,ub=_f(a,"FailureIndexP99"),_f(b,"FailureIndexP99")
        rows.append(("Representative utilization",_pct(ua),_pct(ub),_pct(None if ua is None or ub is None else ub-ua)))
        pa,pb=_f(a,"FailureIndexPeak"),_f(b,"FailureIndexPeak")
        rows.append(("Peak utilization",_pct(pa),_pct(pb),_pct(None if pa is None or pb is None else pb-pa)))
        ea,eb=_f(a,"EffectiveMaterialFraction"),_f(b,"EffectiveMaterialFraction")
        rows.append(("Effective material",_pct(ea),_pct(eb),_pct(None if ea is None or eb is None else eb-ea)))
        rows.append(("Print settings",
                     f"{int(getattr(a,'Walls',0) or 0)} walls / {int(getattr(a,'InfillPercent',0) or 0)}% infill",
                     f"{int(getattr(b,'Walls',0) or 0)} walls / {int(getattr(b,'InfillPercent',0) or 0)}% infill",""))
        rows.append(("Material",str(getattr(a,"MaterialProfile","—")),str(getattr(b,"MaterialProfile","—")),""))
        rows.append(("Failure mode",str(getattr(a,"GoverningFailureMode","—")),str(getattr(b,"GoverningFailureMode","—")),""))
        for rowdata in rows:
            r=self.table.rowCount(); self.table.insertRow(r)
            for c,val in enumerate(rowdata):
                item=QtWidgets.QTableWidgetItem(str(val)); self.table.setItem(r,c,item)


_active_compare=None

def show_compare_dialog(doc=None):
    global _active_compare
    if _active_compare is not None:
        try:
            if _active_compare.isVisible():
                _active_compare.doc=doc or App.ActiveDocument; _active_compare.refresh(); _active_compare.raise_(); _active_compare.activateWindow(); return _active_compare
        except RuntimeError:
            _active_compare=None
    _active_compare=CompareRunsDialog(doc or App.ActiveDocument, Gui.getMainWindow())
    try: flag=QtCore.Qt.WidgetAttribute.WA_DeleteOnClose
    except AttributeError: flag=QtCore.Qt.WA_DeleteOnClose
    _active_compare.setAttribute(flag,True); _active_compare.destroyed.connect(_destroyed)
    _active_compare.show(); _active_compare.raise_(); return _active_compare

def _destroyed(*_args):
    global _active_compare; _active_compare=None
