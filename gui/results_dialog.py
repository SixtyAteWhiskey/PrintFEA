"""Independent result browser/dialog for PrintFEA.

The setup wizard is intentionally disposable. Analysis results are persisted in
FreeCAD document objects and this module owns a separate non-modal result window
that can outlive the wizard and be reopened from the PrintFEA toolbar.

v0.3.0 keeps the default results UI decision-first. Engineering
internals remain available behind an Advanced Details expander, but the first
screen answers the questions most users actually have: did it pass, what safety
factor did it achieve, how much did it move, and what failure mode is closest?
"""

import math
import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:
    QtWidgets = QtGui

from gui.colorbar_units import ColorBarUnitOverlay
from gui.preview import PreviewArrows
from post.results import (
    configure_pipeline,
    isolate_and_focus_pipeline,
    find_pipeline,
    utilization_object_has_field,
)

_active_results_dialog = None


def _is_summary_object(obj):
    try:
        return obj.Name.startswith("PrintFEA_Summary") and hasattr(obj, "Verdict")
    except Exception:
        return False


def recent_summary_objects(doc=None):
    doc = doc or App.ActiveDocument
    if doc is None:
        return []
    return [obj for obj in reversed(list(doc.Objects)) if _is_summary_object(obj)]


def _find_analysis_for_summary(summary_obj):
    analysis = getattr(summary_obj, "AnalysisObject", None)
    if analysis is not None:
        return analysis
    doc = getattr(summary_obj, "Document", None)
    if doc is None:
        return None
    for obj in reversed(list(doc.Objects)):
        try:
            if not obj.Name.startswith("PrintFEA_Analysis"):
                continue
            if summary_obj in list(getattr(obj, "Group", [])):
                return obj
        except Exception:
            continue
    return None


def _find_pipeline_for_summary(summary_obj):
    pipeline = getattr(summary_obj, "PipelineObject", None)
    if pipeline is not None:
        return pipeline
    analysis = _find_analysis_for_summary(summary_obj)
    if analysis is not None:
        return find_pipeline(analysis)
    return None


def _find_utilization_for_summary(summary_obj):
    obj = getattr(summary_obj, "UtilizationObject", None)
    if obj is not None and utilization_object_has_field(obj):
        return obj
    return None


def _to_float(value):
    try:
        f = float(value)
    except Exception:
        return None
    return f if math.isfinite(f) else None


def _finite_text(value, decimals=2):
    try:
        f = float(value)
    except Exception:
        return "—"
    if math.isinf(f):
        return "∞" if f > 0 else "-∞"
    if not math.isfinite(f):
        return "—"
    return f"{f:.{decimals}f}"


def _percent_text(value, decimals=0):
    f = _to_float(value)
    if f is None:
        return "—"
    return f"{f * 100.0:.{decimals}f}%"


def _friendly_failure_mode(mode):
    raw = str(mode or "").strip()
    low = raw.lower()
    if "layer separation" in low or "inter-layer normal tension" in low:
        return "Layer separation"
    if "through-layer compression" in low:
        return "Through-layer compression"
    if "inter-layer shear" in low:
        return "Inter-layer shear"
    if "in-layer shear" in low:
        return "In-layer shear"
    if "in-layer normal" in low:
        return "Along-layer normal stress"
    if raw and raw != "—" and raw.lower() != "not available":
        return raw
    return "Not available"


def _run_label(obj, index):
    stamp = str(getattr(obj, "RunTimestamp", "") or "").strip()
    verdict = str(getattr(obj, "Verdict", "INCOMPLETE") or "INCOMPLETE")
    material = str(getattr(obj, "MaterialProfile", "") or "")
    analysis = _find_analysis_for_summary(obj)
    analysis_label = getattr(analysis, "Label", "") if analysis is not None else ""
    parts = [stamp if stamp else f"Run {index + 1}", verdict]
    if material:
        parts.append(material)
    if analysis_label:
        parts.append(analysis_label)
    return " — ".join(parts)


class PrintFEAResultsDialog(QtWidgets.QDialog):
    def __init__(self, doc=None, parent=None):
        super().__init__(parent or Gui.getMainWindow())
        self.setWindowTitle("PrintFEA — Results")
        self.setModal(False)
        self.resize(650, 720)
        self.doc = doc or App.ActiveDocument
        self.summary_obj = None
        self.pipeline = None
        self.utilization_obj = None
        self.colorbar_units = ColorBarUnitOverlay(self)
        self.failure_marker = PreviewArrows()
        self._build_ui()
        self.refresh_runs()

    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)

        nav = QtWidgets.QGroupBox("Recent PrintFEA runs")
        nav_layout = QtWidgets.QHBoxLayout(nav)
        self.run_combo = QtWidgets.QComboBox()
        try:
            policy = QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents
        except AttributeError:
            policy = QtWidgets.QComboBox.AdjustToContents
        self.run_combo.setSizeAdjustPolicy(policy)
        self.run_combo.currentIndexChanged.connect(self._run_changed)
        self.run_combo.setToolTip("Choose any saved PrintFEA result in the active FreeCAD document.")
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.refresh_button.setToolTip("Refresh the list of saved PrintFEA runs.")
        self.refresh_button.clicked.connect(self.refresh_runs)
        compare_button = QtWidgets.QPushButton("Compare")
        compare_button.setToolTip("Compare two saved PrintFEA runs side by side.")
        compare_button.clicked.connect(self._open_compare)
        help_button = QtWidgets.QPushButton("Help")
        help_button.setToolTip("Open help for interpreting PrintFEA results.")
        help_button.clicked.connect(self._open_results_help)
        nav_layout.addWidget(self.run_combo, 1)
        nav_layout.addWidget(self.refresh_button)
        nav_layout.addWidget(compare_button)
        nav_layout.addWidget(help_button)
        outer.addWidget(nav)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        try:
            scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        except AttributeError:
            scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        try:
            scroll.viewport().setStyleSheet("background: transparent;")
        except Exception:
            pass
        outer.addWidget(scroll, 1)

        content = QtWidgets.QWidget()
        content.setObjectName("PrintFEAResultsContent")
        content.setStyleSheet("QWidget#PrintFEAResultsContent { background: transparent; }")
        content.setAutoFillBackground(False)
        layout = QtWidgets.QVBoxLayout(content)
        scroll.setWidget(content)

        title = QtWidgets.QLabel("<h2>Analysis Results</h2>")
        layout.addWidget(title)

        # ---- At-a-glance decision card ----
        glance = QtWidgets.QGroupBox("At a glance")
        glance_layout = QtWidgets.QVBoxLayout(glance)

        self.verdict = QtWidgets.QLabel("No result selected")
        vf = self.verdict.font()
        vf.setPointSize(max(vf.pointSize() + 8, 18))
        vf.setBold(True)
        self.verdict.setFont(vf)
        glance_layout.addWidget(self.verdict)

        self.detail = QtWidgets.QLabel("")
        self.detail.setWordWrap(True)
        glance_layout.addWidget(self.detail)

        basic_grid = QtWidgets.QGridLayout()
        self.basic_values = {}
        basic_rows = [
            ("Safety factor", "safety_factor"),
            ("Maximum movement", "max_displacement"),
            ("Print structure", "print_structure"),
            ("Closest failure mode", "failure_mode"),
            ("Representative load", "representative_util"),
            ("Worst local hotspot", "peak_util"),
        ]
        for row, (caption, key) in enumerate(basic_rows):
            label = QtWidgets.QLabel(caption + ":")
            label.setAlignment(QtCore.Qt.AlignTop)
            value = QtWidgets.QLabel("—")
            value.setWordWrap(True)
            value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            if key == "safety_factor":
                f = value.font()
                f.setBold(True)
                f.setPointSize(max(f.pointSize() + 2, 11))
                value.setFont(f)
            basic_grid.addWidget(label, row, 0)
            basic_grid.addWidget(value, row, 1)
            self.basic_values[key] = value
        basic_grid.setColumnStretch(1, 1)
        glance_layout.addLayout(basic_grid)

        self.util_bar = QtWidgets.QProgressBar()
        self.util_bar.setRange(0, 100)
        self.util_bar.setTextVisible(True)
        glance_layout.addWidget(self.util_bar)

        self.hotspot_note = QtWidgets.QLabel("")
        self.hotspot_note.setWordWrap(True)
        glance_layout.addWidget(self.hotspot_note)

        self.failure_location_btn = QtWidgets.QPushButton("Show likely failure region")
        self.failure_location_btn.setCheckable(True)
        self.failure_location_btn.setVisible(False)
        self.failure_location_btn.setToolTip(
            "Show a 3D marker at the representative high-utilization region used for a CAUTION/FAIL result. "
            "This is an approximate FEM hotspot, not an exact predicted crack path."
        )
        self.failure_location_btn.toggled.connect(self._toggle_failure_marker)
        glance_layout.addWidget(self.failure_location_btn)
        layout.addWidget(glance)

        # ---- Result visualization ----
        map_box = QtWidgets.QGroupBox("What do you want to see on the model?")
        map_layout = QtWidgets.QVBoxLayout(map_box)
        buttons = QtWidgets.QHBoxLayout()
        self.btn_util = QtWidgets.QPushButton("FDM SAFETY")
        self.btn_stress = QtWidgets.QPushButton("STRESS")
        self.btn_disp = QtWidgets.QPushButton("MOVEMENT")
        self.btn_util.setToolTip("Recommended view: directional FDM utilization relative to the selected material allowables.")
        self.btn_stress.setToolTip("Conventional von Mises stress map. Useful for hotspots, but not the layer-aware PASS/FAIL criterion.")
        self.btn_disp.setToolTip("Displacement magnitude: how far the part moves under the applied load.")
        self.btn_util.clicked.connect(lambda: self.show_quantity("utilization"))
        self.btn_stress.clicked.connect(lambda: self.show_quantity("stress"))
        self.btn_disp.clicked.connect(lambda: self.show_quantity("displacement"))
        buttons.addWidget(self.btn_util)
        buttons.addWidget(self.btn_stress)
        buttons.addWidget(self.btn_disp)
        map_layout.addLayout(buttons)
        self.color_guide = QtWidgets.QLabel("")
        self.color_guide.setWordWrap(True)
        map_layout.addWidget(self.color_guide)
        layout.addWidget(map_box)

        # ---- Advanced engineering details, collapsed by default ----
        self.advanced_toggle = QtWidgets.QPushButton("Show advanced engineering details")
        self.advanced_toggle.setToolTip("Show raw stress components, directional allowables, solver details, mesh diagnostics, and print-structure internals.")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setChecked(False)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_toggle)

        self.advanced_widget = QtWidgets.QWidget()
        advanced_layout = QtWidgets.QVBoxLayout(self.advanced_widget)
        advanced_layout.setContentsMargins(0, 0, 0, 0)

        values_box = QtWidgets.QGroupBox("Engineering details")
        grid = QtWidgets.QGridLayout(values_box)
        self.values = {}
        rows = [
            ("Peak von Mises stress", "peak_stress"),
            ("99th-percentile von Mises", "p99_stress"),
            ("Analysis material model", "material_model"),
            ("Failure-screening method", "failure_method"),
            ("Raw governing FDM mode", "failure_mode"),
            ("99th-percentile utilization", "failure_p99"),
            ("Peak utilization", "failure_peak"),
            ("Governing stress / allowable", "governing_pair"),
            ("Directional stiffness", "directional_stiffness"),
            ("Directional allowables", "directional_allowables"),
            ("Wall / infill scaling", "structure_scaling"),
            ("Peak-node safety factor", "peak_safety_factor"),
            ("Governing hotspot", "hotspot"),
            ("Highlighted failure region", "failure_location"),
            ("Clicked loads", "point_loads"),
            ("Mesh / solver mode", "mesh_mode"),
            ("Material profile", "material"),
            ("Recorded print settings", "print_settings"),
        ]
        for row, (caption, key) in enumerate(rows):
            label = QtWidgets.QLabel(caption + ":")
            label.setAlignment(QtCore.Qt.AlignTop)
            value = QtWidgets.QLabel("—")
            value.setWordWrap(True)
            value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1)
            self.values[key] = value
        grid.setColumnStretch(1, 1)
        advanced_layout.addWidget(values_box)

        self.advanced_map_guide = QtWidgets.QLabel("")
        self.advanced_map_guide.setWordWrap(True)
        self.advanced_map_guide.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        advanced_layout.addWidget(self.advanced_map_guide)

        caveat = QtWidgets.QLabel(
            "<b>Screening result only.</b> PrintFEA uses a homogenized layer-aware material model. "
            "Generic presets are conservative screening assumptions, not coupon data for your exact printer and filament. "
            "Wall count and infill density can be represented by an approximate shell/core homogenization, but explicit infill pattern/cells, top/bottom skin, raster direction, seams/voids, defects, creep, fatigue, impact and temperature are not yet modeled. "
            "Peak nodal values can also be mesh-sensitive near fixed edges, sharp corners and load boundaries."
        )
        caveat.setWordWrap(True)
        advanced_layout.addWidget(caveat)

        self.advanced_widget.setVisible(False)
        layout.addWidget(self.advanced_widget)
        layout.addStretch(1)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        close_btn = QtWidgets.QPushButton("Close Results")
        close_btn.clicked.connect(self.close)
        outer.addWidget(close_btn)

    def _open_compare(self):
        from gui.compare_dialog import show_compare_dialog
        show_compare_dialog(self.doc or App.ActiveDocument)

    def _open_results_help(self):
        """Open the in-app Help window directly to result interpretation."""
        from gui.help_dialog import show_help_dialog
        show_help_dialog(section="Understanding Results")

    def _toggle_advanced(self, checked):
        self.advanced_widget.setVisible(bool(checked))
        self.advanced_toggle.setText(
            "Hide advanced engineering details" if checked else "Show advanced engineering details"
        )

    def refresh_runs(self, select_obj=None):
        self.doc = self.doc or App.ActiveDocument
        current_obj = select_obj or self.summary_obj
        summaries = recent_summary_objects(self.doc)
        self.run_combo.blockSignals(True)
        self.run_combo.clear()
        for i, obj in enumerate(summaries):
            self.run_combo.addItem(_run_label(obj, i), obj.Name)
        self.run_combo.blockSignals(False)

        if not summaries:
            self.summary_obj = None
            self.pipeline = None
            self.verdict.setText("No PrintFEA results found")
            self.detail.setText("Run an analysis first, then this window can reopen its saved result summary.")
            self.btn_util.setEnabled(False)
            self.btn_stress.setEnabled(False)
            self.btn_disp.setEnabled(False)
            self.status.setText("No saved PrintFEA result summaries exist in the active document.")
            return

        index = 0
        if current_obj is not None:
            for i, obj in enumerate(summaries):
                if obj is current_obj or obj.Name == getattr(current_obj, "Name", None):
                    index = i
                    break
        self.run_combo.setCurrentIndex(index)
        self._load_summary(summaries[index])

    def select_summary(self, summary_obj):
        if summary_obj is None:
            self.refresh_runs()
            return
        self.doc = getattr(summary_obj, "Document", None) or self.doc or App.ActiveDocument
        self.refresh_runs(select_obj=summary_obj)

    def _run_changed(self, index):
        if index < 0 or self.doc is None:
            return
        name = self.run_combo.itemData(index)
        try:
            obj = self.doc.getObject(str(name))
        except Exception:
            obj = None
        if obj is not None:
            self._load_summary(obj)

    def _load_summary(self, obj):
        self.summary_obj = obj
        self.pipeline = _find_pipeline_for_summary(obj)
        self.utilization_obj = _find_utilization_for_summary(obj)

        verdict = str(getattr(obj, "Verdict", "INCOMPLETE") or "INCOMPLETE")
        symbol = {"PASS": "✓", "CAUTION": "⚠", "FAIL": "✕"}.get(verdict, "?")
        self.verdict.setText(f"{symbol} {verdict}")

        sf_v = _to_float(getattr(obj, "EstimatedSafetyFactor", None))
        target_v = _to_float(getattr(obj, "TargetSafetyFactor", None))
        peak_sf_v = _to_float(getattr(obj, "PeakSafetyFactor", None))
        model_text = str(getattr(obj, "MaterialModel", "") or "")
        is_layer = "layer-aware" in model_text.lower()
        p99_u = _to_float(getattr(obj, "FailureIndexP99", None)) if is_layer and hasattr(obj, "FailureIndexP99") else None
        peak_u = _to_float(getattr(obj, "FailureIndexPeak", None)) if is_layer and hasattr(obj, "FailureIndexPeak") else None
        mode_raw = str(getattr(obj, "GoverningFailureMode", "—") or "—")
        mode_friendly = _friendly_failure_mode(mode_raw)

        if verdict == "PASS":
            self.detail.setText(
                f"This run meets the selected safety-factor target{f' of {target_v:.2f}' if target_v else ''}."
            )
        elif verdict == "CAUTION":
            self.detail.setText("This run is close to the selected safety margin. Inspect the highlighted regions before relying on the part.")
        elif verdict == "FAIL":
            self.detail.setText("This run does not meet the selected safety margin. The highlighted regions need redesign, a different orientation, material, or load case.")
        else:
            self.detail.setText(str(getattr(obj, "VerdictDetail", "") or "The result is incomplete."))

        sf_text = _finite_text(sf_v, 2)
        target_text = _finite_text(target_v, 2)
        self.basic_values["safety_factor"].setText(f"{sf_text}  (target {target_text})")
        self.basic_values["max_displacement"].setText(
            f"{_finite_text(getattr(obj, 'MaxDisplacementMM', None), 3)} mm"
        )
        if hasattr(obj, "StructureModelEnabled") and bool(getattr(obj, "StructureModelEnabled", False)):
            eff = _to_float(getattr(obj, "EffectiveMaterialFraction", None))
            walls = int(getattr(obj, "Walls", 0) or 0)
            infill = int(getattr(obj, "InfillPercent", 0) or 0)
            self.basic_values["print_structure"].setText(
                f"{walls} walls + {infill}% infill → ~{_percent_text(eff, 0)} effective material"
            )
        else:
            self.basic_values["print_structure"].setText("Fully dense / wall-infill effect disabled or not recorded")
        self.basic_values["failure_mode"].setText(mode_friendly)
        self.basic_values["representative_util"].setText(
            f"{_percent_text(p99_u, 1)} of directional allowable" if p99_u is not None else "Not available"
        )
        self.basic_values["peak_util"].setText(
            f"{_percent_text(peak_u, 1)} of directional allowable" if peak_u is not None else "Not available"
        )

        # One visual number is easier to parse than another table row. The bar
        # shows the representative utilization; the text retains values >100%.
        if p99_u is not None:
            self.util_bar.setValue(max(0, min(100, int(round(p99_u * 100.0)))))
            self.util_bar.setFormat(f"Representative load: {p99_u * 100.0:.1f}% of allowable")
        else:
            self.util_bar.setValue(0)
            self.util_bar.setFormat("Representative utilization unavailable")

        if peak_sf_v is not None and target_v is not None and peak_sf_v < target_v:
            if peak_sf_v < 1.0:
                self.hotspot_note.setText(
                    f"⚠ <b>Local hotspot:</b> the single worst node exceeds the selected allowable (local SF {peak_sf_v:.2f}). "
                    "Inspect the FDM Safety map and verify whether the hotspot persists with a finer mesh."
                )
            else:
                self.hotspot_note.setText(
                    f"⚠ <b>Local hotspot:</b> the worst single node has SF {peak_sf_v:.2f}, below your target of {target_v:.2f}. "
                    "The overall verdict uses the representative 99th-percentile region because isolated FEM peaks can be mesh-sensitive."
                )
        elif peak_sf_v is not None and target_v is not None:
            self.hotspot_note.setText(
                f"✓ The worst local node also meets the target margin (local SF {peak_sf_v:.2f})."
            )
        else:
            self.hotspot_note.setText("")

        # Advanced engineering values.
        self.values["peak_stress"].setText(f"{_finite_text(getattr(obj, 'PeakVonMisesMPa', None), 3)} MPa")
        self.values["p99_stress"].setText(f"{_finite_text(getattr(obj, 'P99VonMisesMPa', None), 3)} MPa")
        self.values["material_model"].setText(str(getattr(obj, "MaterialModel", "Isotropic conservative (older run)") or "—"))
        self.values["failure_method"].setText(str(getattr(obj, "FailureMethod", "von Mises / allowable") or "—"))
        self.values["failure_mode"].setText(mode_raw)
        self.values["failure_p99"].setText(_finite_text(p99_u, 3) + ("  (1.0 = directional allowable)" if p99_u is not None else ""))
        self.values["failure_peak"].setText(_finite_text(peak_u, 3) + ("  (1.0 = directional allowable)" if peak_u is not None else ""))

        gs = getattr(obj, "GoverningStressMPa", None) if is_layer and hasattr(obj, "GoverningStressMPa") else None
        ga = getattr(obj, "GoverningAllowableMPa", None) if is_layer and hasattr(obj, "GoverningAllowableMPa") else None
        self.values["governing_pair"].setText(f"{_finite_text(gs, 3)} / {_finite_text(ga, 3)} MPa" if is_layer else "—")
        xy = getattr(obj, "AllowableStressMPa", None)
        z = getattr(obj, "LayerAllowableMPa", None) if is_layer and hasattr(obj, "LayerAllowableMPa") else None
        sxy = getattr(obj, "InPlaneShearAllowableMPa", None) if is_layer and hasattr(obj, "InPlaneShearAllowableMPa") else None
        sz = getattr(obj, "InterlayerShearAllowableMPa", None) if is_layer and hasattr(obj, "InterlayerShearAllowableMPa") else None
        if is_layer:
            exy = getattr(obj, "InPlaneModulusMPa", None) if hasattr(obj, "InPlaneModulusMPa") else None
            ez = getattr(obj, "BuildModulusMPa", None) if hasattr(obj, "BuildModulusMPa") else None
            gxy = getattr(obj, "InPlaneShearModulusMPa", None) if hasattr(obj, "InPlaneShearModulusMPa") else None
            gz = getattr(obj, "InterlayerShearModulusMPa", None) if hasattr(obj, "InterlayerShearModulusMPa") else None
            self.values["directional_stiffness"].setText(
                f"E(XY) {_finite_text(exy, 1)} MPa / E(Z) {_finite_text(ez, 1)} MPa / "
                f"G(XY) {_finite_text(gxy, 1)} MPa / G(inter-layer) {_finite_text(gz, 1)} MPa"
            )
            self.values["directional_allowables"].setText(
                f"normal XY {_finite_text(xy, 2)} MPa / normal Z {_finite_text(z, 2)} MPa / "
                f"shear XY {_finite_text(sxy, 2)} MPa / inter-layer shear {_finite_text(sz, 2)} MPa"
            )
        else:
            self.values["directional_stiffness"].setText("Isotropic legacy model")
            self.values["directional_allowables"].setText(f"Isotropic {_finite_text(xy, 2)} MPa")
        if hasattr(obj, "StructureModelEnabled") and bool(getattr(obj, "StructureModelEnabled", False)):
            shell = _to_float(getattr(obj, "EstimatedShellFraction", None))
            eff = _to_float(getattr(obj, "EffectiveMaterialFraction", None))
            ks = _to_float(getattr(obj, "StructureStiffnessScale", None))
            ss = _to_float(getattr(obj, "StructureStrengthScale", None))
            lw = _to_float(getattr(obj, "WallLineWidthMM", None))
            method = str(getattr(obj, "StructureMethod", "not recorded") or "not recorded")
            core_v = _to_float(getattr(obj, "EstimatedCoreVolumeMM3", None)) if hasattr(obj, "EstimatedCoreVolumeMM3") else None
            fallback = bool(getattr(obj, "StructureFallbackUsed", False)) if hasattr(obj, "StructureFallbackUsed") else False
            fallback_text = "; ⚠ conservative slice fallback used" if fallback else ""
            core_text = f"; core volume {_finite_text(core_v, 1)} mm³" if core_v is not None else ""
            slice_text = ""
            if hasattr(obj, "SampledSlices"):
                sampled = int(getattr(obj, "SampledSlices", 0) or 0)
                layers = int(getattr(obj, "EstimatedPrintLayers", 0) or 0)
                partial = int(getattr(obj, "PartialWallSlices", 0) or 0)
                failed = int(getattr(obj, "SectionFailureSlices", 0) or 0)
                err = _to_float(getattr(obj, "SliceIntegrationErrorPercent", None))
                workers = int(getattr(obj, "ParallelStructureWorkers", 1) or 1) if hasattr(obj, "ParallelStructureWorkers") else 1
                timed_out = int(getattr(obj, "TimedOutStructureSlices", 0) or 0) if hasattr(obj, "TimedOutStructureSlices") else 0
                timeout_s = _to_float(getattr(obj, "SlowSliceLimitSeconds", None)) if hasattr(obj, "SlowSliceLimitSeconds") else None
                timeout_text = f"; timed-out slices {timed_out} @ {_finite_text(timeout_s, 1)} s limit" if timed_out or timeout_s else ""
                slice_text = (
                    f"; slices {sampled}/~{layers} layers on {workers} worker(s); partial wall slices {partial}; "
                    f"section failures {failed}; raw integration error {_finite_text(err, 2)}%" + timeout_text
                )
            self.values["structure_scaling"].setText(
                f"{method}; shell {_percent_text(shell, 1)}; effective material {_percent_text(eff, 1)}; "
                f"stiffness ×{_finite_text(ks, 3)}; strength ×{_finite_text(ss, 3)}; wall line width {_finite_text(lw, 2)} mm"
                + core_text + slice_text + fallback_text
            )
        else:
            self.values["structure_scaling"].setText("Disabled / fully dense assumption")
        self.values["peak_safety_factor"].setText(_finite_text(peak_sf_v, 2))

        node = int(getattr(obj, "PeakNode", 0) or 0)
        pos = getattr(obj, "PeakPosition", None)
        if pos is not None and node:
            hotspot = f"node {node} at ({pos.x:.2f}, {pos.y:.2f}, {pos.z:.2f}) mm"
        elif node:
            hotspot = f"node {node}"
        else:
            hotspot = "not available"
        self.values["hotspot"].setText(hotspot)

        fnode = int(getattr(obj, "FailureLocationNode", 0) or 0) if hasattr(obj, "FailureLocationNode") else 0
        fpos = getattr(obj, "FailureLocationPosition", None) if hasattr(obj, "FailureLocationPosition") else None
        fkind = str(getattr(obj, "FailureLocationKind", "") or "") if hasattr(obj, "FailureLocationKind") else ""
        if fnode and fpos is not None:
            frcount = int(getattr(obj, "FailureRegionNodeCount", 0) or 0) if hasattr(obj, "FailureRegionNodeCount") else 0
            frmode = str(getattr(obj, "FailureLocationMode", "") or "") if hasattr(obj, "FailureLocationMode") else ""
            mode_text = _friendly_failure_mode(frmode) if frmode else ""
            suffix = f"; {frcount} highlighted node(s)" if frcount else ""
            if mode_text and mode_text != "Not available":
                suffix += f"; predicted mode: {mode_text}"
            self.values["failure_location"].setText(
                f"{fkind or 'high-utilization region'}: node {fnode} at ({fpos.x:.2f}, {fpos.y:.2f}, {fpos.z:.2f}) mm{suffix}"
            )
        else:
            self.values["failure_location"].setText("not highlighted for this run")

        point_count = int(getattr(obj, "PointLoadCount", 0) or 0) if hasattr(obj, "PointLoadCount") else 0
        point_summary = str(getattr(obj, "PointLoadSummary", "") or "") if hasattr(obj, "PointLoadSummary") else ""
        self.values["point_loads"].setText(
            f"{point_count} point force(s)" + (f": {point_summary}" if point_summary else "")
        )
        self.values["mesh_mode"].setText(str(getattr(obj, "MeshSolverMode", "not reported") or "not reported"))
        self.values["material"].setText(str(getattr(obj, "MaterialProfile", "") or "—"))
        if hasattr(obj, "LayerHeightMM"):
            bd = getattr(obj, "BuildDirection", None)
            bd_text = ""
            if bd is not None:
                try:
                    bd_text = f"; build dir ({bd.x:+.3f}, {bd.y:+.3f}, {bd.z:+.3f})"
                except Exception:
                    pass
            self.values["print_settings"].setText(
                f"{_finite_text(getattr(obj, 'LayerHeightMM', None), 2)} mm layers; "
                f"{int(getattr(obj, 'Walls', 0) or 0)} walls @ {_finite_text(getattr(obj, 'WallLineWidthMM', None), 2)} mm; "
                f"{int(getattr(obj, 'InfillPercent', 0) or 0)}% infill{bd_text}"
            )
        else:
            self.values["print_settings"].setText("not recorded in this older run")

        has_pipeline = self.pipeline is not None
        has_util = self.utilization_obj is not None
        self.btn_util.setEnabled(has_util)
        self.btn_util.setToolTip(
            "Color by layer-aware directional utilization; 100% means the selected directional allowable."
            if has_util else
            "This saved run does not contain a layer-aware FDM safety heat map."
        )
        self.btn_stress.setEnabled(has_pipeline)
        self.btn_disp.setEnabled(has_pipeline)
        if has_pipeline:
            self.show_quantity("utilization" if has_util else "stress")
            self.status.setText("Showing this saved run. Use the selector above to view another PrintFEA result.")
        else:
            self.colorbar_units.hide()
            self.color_guide.setText("The saved numerical summary is available, but the associated result pipeline could not be found.")
            self.advanced_map_guide.setText("")
            self.status.setText("Saved summary loaded; associated Pipeline_CCX_Results was not found.")

        self._refresh_failure_marker_for_summary()

    def _result_extent(self):
        obj = self.summary_obj
        result_obj = getattr(obj, "ResultObject", None) if obj is not None else None
        try:
            nodes = result_obj.Mesh.FemMesh.Nodes
            xs, ys, zs = [], [], []
            for _nid, p in nodes.items():
                xs.append(float(p.x)); ys.append(float(p.y)); zs.append(float(p.z))
            if not xs:
                return 20.0
            dx = max(xs) - min(xs); dy = max(ys) - min(ys); dz = max(zs) - min(zs)
            return max(math.sqrt(dx * dx + dy * dy + dz * dz), 1.0)
        except Exception:
            return 20.0

    def _toggle_failure_marker(self, checked):
        self.failure_marker.clear("failure_hotspot")
        if not checked or self.summary_obj is None:
            if hasattr(self, "failure_location_btn"):
                self.failure_location_btn.setText("Show likely failure region")
            return
        node = int(getattr(self.summary_obj, "FailureLocationNode", 0) or 0)
        pos = getattr(self.summary_obj, "FailureLocationPosition", None)
        if not node or pos is None:
            self.failure_location_btn.blockSignals(True)
            self.failure_location_btn.setChecked(False)
            self.failure_location_btn.blockSignals(False)
            self.failure_location_btn.setText("Show likely failure region")
            return
        verdict = str(getattr(self.summary_obj, "Verdict", "") or "")
        rgb = (1.0, 0.08, 0.04) if verdict == "FAIL" else (1.0, 0.55, 0.05)
        mode_raw = str(getattr(self.summary_obj, "FailureLocationMode", "") or getattr(self.summary_obj, "GoverningFailureMode", "") or "")
        mode = _friendly_failure_mode(mode_raw)
        label = f"LIKELY: {mode}" if mode and mode != "Not available" else "LIKELY FAILURE REGION"
        region = list(getattr(self.summary_obj, "FailureRegionPositions", []) or []) if hasattr(self.summary_obj, "FailureRegionPositions") else []
        if region:
            radius = max(self._result_extent() * 0.007, 0.30)
            self.failure_marker.show_region(
                "failure_hotspot", region, radius, label, rgb, 0.30 if verdict == "FAIL" else 0.38, center=pos
            )
        else:
            radius = max(self._result_extent() * 0.018, 0.65)
            self.failure_marker.show_marker(
                "failure_hotspot", pos, radius, label, rgb, 0.08
            )
        self.failure_location_btn.setText("Hide likely failure region")

    def _refresh_failure_marker_for_summary(self):
        self.failure_marker.clear("failure_hotspot")
        if self.summary_obj is None:
            self.failure_location_btn.setVisible(False)
            return
        verdict = str(getattr(self.summary_obj, "Verdict", "") or "")
        node = int(getattr(self.summary_obj, "FailureLocationNode", 0) or 0)
        available = verdict in ("CAUTION", "FAIL") and node > 0
        self.failure_location_btn.setVisible(available)
        self.failure_location_btn.blockSignals(True)
        self.failure_location_btn.setChecked(available)
        self.failure_location_btn.blockSignals(False)
        if available:
            kind = str(getattr(self.summary_obj, "FailureLocationKind", "") or "representative high-utilization region")
            self.failure_location_btn.setToolTip(
                f"Highlight the {kind}. PrintFEA shows a small cloud of nearby high-utilization FEM nodes and labels the predicted directional failure mode. "
                "It is an approximate likely failure region, not an exact crack path or fracture surface."
            )
            self._toggle_failure_marker(True)
        else:
            self.failure_location_btn.setText("Show likely failure region")

    def show_quantity(self, quantity):
        if self.summary_obj is None:
            return

        target = self.utilization_obj if quantity == "utilization" else self.pipeline
        if target is None:
            self.status.setText("That result view is not available for this saved run.")
            return

        isolate_and_focus_pipeline(target, open_task_panel=True)
        selected = configure_pipeline(target, quantity)
        self.colorbar_units.show_quantity(quantity)

        if quantity == "utilization":
            p99 = _to_float(getattr(self.summary_obj, "FailureIndexP99", None))
            peak = _to_float(getattr(self.summary_obj, "FailureIndexPeak", None))
            target_sf = _to_float(getattr(self.summary_obj, "TargetSafetyFactor", None))
            target_u = (1.0 / target_sf) if target_sf and target_sf > 0 else None
            self.color_guide.setText(
                "<b>FDM Safety map</b><br>"
                f"Representative region: <b>{_percent_text(p99, 1)}</b> of allowable &nbsp; • &nbsp; "
                f"Worst point: <b>{_percent_text(peak, 1)}</b>.<br>"
                + (f"Your SF {_finite_text(target_sf, 2)} target corresponds to <b>{_percent_text(target_u, 1)}</b> utilization. " if target_u is not None else "")
                + "<b>100% means the selected directional allowable.</b><br>"
                "The color scale auto-ranges: red means the highest utilization in this run, not automatically failure."
            )
            self.advanced_map_guide.setText(
                "<b>How FDM utilization is calculated:</b> at every displayed point, PrintFEA evaluates "
                "|S11|/allowable, |S22|/allowable, |S33|/allowable, |S12|/allowable, |S13|/allowable and |S23|/allowable, "
                "then displays the largest value. Utilization is dimensionless. A value of 1.000 means that directional allowable has been reached."
            )
        elif quantity == "stress":
            result_obj = getattr(self.summary_obj, "ResultObject", None)
            lo = None
            try:
                values = [float(v) for v in getattr(result_obj, "vonMises", []) if math.isfinite(float(v))]
                lo = min(values) if values else None
            except Exception:
                lo = None
            hi = getattr(self.summary_obj, "PeakVonMisesMPa", None)
            self.color_guide.setText(
                "<b>Stress map</b><br>Higher colors mean higher von Mises stress. "
                f"This run ranges from about {_finite_text(lo, 2)} to {_finite_text(hi, 2)} MPa.<br>"
                "Use <b>FDM Safety</b> for the actual layer-aware PASS/FAIL picture."
            )
            self.advanced_map_guide.setText(
                "Von Mises stress is useful for conventional stress visualization, but layer-aware PASS/CAUTION/FAIL is based on the individual local print-axis stress components and directional allowables. "
                "FreeCAD's native color-bar stress ticks are in Pa; PrintFEA reports MPa."
            )
        else:
            result_obj = getattr(self.summary_obj, "ResultObject", None)
            lo = None
            try:
                values = [float(v) for v in getattr(result_obj, "DisplacementLengths", []) if math.isfinite(float(v))]
                lo = min(values) if values else None
            except Exception:
                lo = None
            hi = getattr(self.summary_obj, "MaxDisplacementMM", None)
            self.color_guide.setText(
                "<b>Movement map</b><br>Blue moves least; red moves most. "
                f"Maximum movement in this run is <b>{_finite_text(hi, 3)} mm</b>. "
                "This view shows deflection, not material failure."
            )
            self.advanced_map_guide.setText(
                f"Minimum reported displacement: {_finite_text(lo, 4)} mm. FreeCAD's native displacement color-bar ticks are in metres; PrintFEA reports millimetres."
            )

        if selected is None:
            self.status.setText("Could not switch the requested post-processing field automatically on this FreeCAD build.")
        else:
            friendly = {"utilization": "FDM Safety", "stress": "Stress", "displacement": "Movement"}.get(quantity, selected)
            self.status.setText(f"Showing {friendly} map.")

    def closeEvent(self, event):
        self.failure_marker.clear()
        self.colorbar_units.hide()
        super().closeEvent(event)


def _dialog_destroyed(*_args):
    global _active_results_dialog
    _active_results_dialog = None


def show_results_dialog(doc=None, summary_obj=None):
    """Show/reuse the independent recent-results dialog."""
    global _active_results_dialog
    doc = doc or getattr(summary_obj, "Document", None) or App.ActiveDocument

    if _active_results_dialog is not None:
        try:
            if _active_results_dialog.isVisible():
                if summary_obj is not None:
                    _active_results_dialog.select_summary(summary_obj)
                else:
                    _active_results_dialog.refresh_runs()
                _active_results_dialog.raise_()
                _active_results_dialog.activateWindow()
                return _active_results_dialog
        except RuntimeError:
            _active_results_dialog = None

    dlg = PrintFEAResultsDialog(doc, Gui.getMainWindow())
    try:
        delete_on_close = QtCore.Qt.WidgetAttribute.WA_DeleteOnClose
    except AttributeError:
        delete_on_close = QtCore.Qt.WA_DeleteOnClose
    dlg.setAttribute(delete_on_close, True)
    dlg.destroyed.connect(_dialog_destroyed)
    _active_results_dialog = dlg
    if summary_obj is not None:
        dlg.select_summary(summary_obj)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
