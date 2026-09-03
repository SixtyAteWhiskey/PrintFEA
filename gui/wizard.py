import math
import json
import os
import shutil
import tempfile
import traceback
import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:
    QtWidgets = QtGui

from materials.library import MATERIALS, layer_aware_properties, estimate_print_structure
from fem.analysis import create_analysis, mesh_and_solve
from gui.preview import PreviewArrows
from gui.point_picker import SurfacePointPicker
from gui.colorbar_units import ColorBarUnitOverlay
from gui.results_dialog import show_results_dialog
from gui.setup_store import saved_setup_objects, save_setup, load_payload
from diagnostics import log_exception, log_info
from post.results import (
    summarize_results,
    create_summary_object,
    create_fdm_utilization_filter,
    configure_pipeline,
    isolate_and_focus_pipeline,
)


class CollapsibleSection(QtWidgets.QWidget):
    """Clean, frameless collapsible wizard section.

    The v0.3.2 card styling used a broad QFrame stylesheet which could cascade
    into QLabel/QFrame descendants on some FreeCAD themes, producing boxes
    around ordinary labels.  This version deliberately uses whitespace and a
    subtle divider instead of nested borders.
    """

    def __init__(self, title, parent=None, expanded=True):
        super().__init__(parent)
        self._title = title
        self._expanded = bool(expanded)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        self.header = QtWidgets.QToolButton()
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(self._expanded)
        try:
            self.header.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.header.setArrowType(QtCore.Qt.ArrowType.DownArrow if self._expanded else QtCore.Qt.ArrowType.RightArrow)
        except AttributeError:
            self.header.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            self.header.setArrowType(QtCore.Qt.DownArrow if self._expanded else QtCore.Qt.RightArrow)
        self.header.setStyleSheet(
            "QToolButton { text-align: left; font-weight: 650; padding: 8px 3px; "
            "border: none; background: transparent; }"
            "QToolButton:hover { background: palette(alternate-base); border-radius: 4px; }"
        )
        self.header.toggled.connect(self._set_expanded)
        outer.addWidget(self.header)

        self.body = QtWidgets.QFrame()
        self.body.setObjectName("PrintFEASectionBody")
        try:
            self.body.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        except AttributeError:
            self.body.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.body.setStyleSheet(
            "QFrame#PrintFEASectionBody { border: none; background: transparent; }"
        )
        outer.addWidget(self.body)
        self.body.setVisible(self._expanded)

        self.divider = QtWidgets.QFrame()
        self.divider.setObjectName("PrintFEASectionDivider")
        try:
            self.divider.setFrameShape(QtWidgets.QFrame.Shape.HLine)
            self.divider.setFrameShadow(QtWidgets.QFrame.Shadow.Plain)
        except AttributeError:
            self.divider.setFrameShape(QtWidgets.QFrame.HLine)
            self.divider.setFrameShadow(QtWidgets.QFrame.Plain)
        self.divider.setStyleSheet(
            "QFrame#PrintFEASectionDivider { color: palette(mid); background: palette(mid); max-height: 1px; border: none; }"
        )
        outer.addWidget(self.divider)

    def setContentLayout(self, layout):
        # Slight indent makes the hierarchy obvious without putting a box around
        # every field.  Keep enough vertical whitespace to visually group rows.
        layout.setContentsMargins(18, 3, 4, 9)
        layout.setSpacing(6)
        self.body.setLayout(layout)

    def setTitle(self, title):
        self._title = title
        self.header.setText(title)

    def title(self):
        return self._title

    def setExpanded(self, expanded):
        self.header.setChecked(bool(expanded))

    def isExpanded(self):
        return self.header.isChecked()

    def _set_expanded(self, expanded):
        self._expanded = bool(expanded)
        self.body.setVisible(self._expanded)
        try:
            arrow = QtCore.Qt.ArrowType.DownArrow if self._expanded else QtCore.Qt.ArrowType.RightArrow
        except AttributeError:
            arrow = QtCore.Qt.DownArrow if self._expanded else QtCore.Qt.RightArrow
        self.header.setArrowType(arrow)


class PrintFEAWizard(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PrintFEA — FDM Stress Analysis")
        self.setModal(False)
        self.resize(600, 820)
        self.fixed_refs = []
        self.load_refs = []
        self.point_loads = []
        self.point_picker = None
        self.build_ref = None
        self.build_direction = None
        self.model_obj = None
        self.previews = PreviewArrows()
        self.colorbar_units = ColorBarUnitOverlay(self)
        self.last_analysis = None
        self.last_result = None
        self.last_pipeline = None
        self.last_summary = None
        self.results_dialog = None
        self._setup_markers_visible = True
        # The slicer-style shell/core estimator is intentionally NOT live.
        # Geometry slicing/offsetting can take many seconds on detailed B-Reps,
        # so edits merely invalidate the cached estimate until the user asks for
        # a refresh (or starts a solve).
        self._structure_estimate = None
        self._structure_stale = True
        self._structure_signature_cached = None
        # v0.4.1 runs the expensive layer-sliced shell/core estimator in a
        # separate FreeCADCmd process. A single pathological OCCT offset can
        # therefore take as long as it needs without freezing the GUI thread.
        self._structure_process = None
        self._structure_process_stdout = ""
        self._structure_process_stderr = ""
        self._structure_tempdir = None
        self._structure_worker_signature = None
        self._structure_worker_for_run = False
        self._structure_worker_cancelled = False
        self._pending_run_after_structure = False
        self._build_ui()
        self._update_material_summary()
        self._auto_pick_model()

    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 8)
        outer.setSpacing(8)

        # Header: keep the normal workflow concise and move explanations into
        # tooltips / the dedicated Help window.
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("<h2 style='margin-bottom:2px'>PrintFEA</h2><span>Guided FDM structural analysis</span><br><small>Hover any field for a quick explanation.</small>")
        header.addWidget(title, 1)
        self.advanced_options = QtWidgets.QCheckBox("Advanced")
        self.advanced_options.setToolTip(
            "Show technical controls such as the material model, line width, force/build vectors, and preview-arrow sizing."
        )
        self.advanced_options.toggled.connect(self._toggle_advanced_options)
        header.addWidget(self.advanced_options)
        save_setup_button = QtWidgets.QPushButton("Save Setup")
        save_setup_button.setToolTip("Save the current PrintFEA setup inside this FreeCAD document so it can be restored later.")
        save_setup_button.clicked.connect(self._save_setup)
        header.addWidget(save_setup_button)
        load_setup_button = QtWidgets.QPushButton("Load Setup")
        load_setup_button.setToolTip("Restore a previously saved PrintFEA setup from this FreeCAD document.")
        load_setup_button.clicked.connect(self._load_setup)
        header.addWidget(load_setup_button)
        help_button = QtWidgets.QPushButton("Help")
        help_button.setToolTip("Open PrintFEA Help and Quick Start.")
        help_button.clicked.connect(lambda: self._open_help("Quick Start"))
        header.addWidget(help_button)
        outer.addLayout(header)

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
        content.setObjectName("PrintFEAContent")
        content.setStyleSheet("QWidget#PrintFEAContent { background: transparent; }")
        content.setAutoFillBackground(False)
        root = QtWidgets.QVBoxLayout(content)
        root.setContentsMargins(2, 2, 2, 2)
        root.setSpacing(8)
        scroll.setWidget(content)

        self._advanced_widgets = []
        self._section_titles = {}

        # 1. Model ---------------------------------------------------------
        self.model_box = CollapsibleSection("1. Model")
        self._section_titles['model'] = "1. Model"
        ml = QtWidgets.QVBoxLayout()
        self.model_box.setContentLayout(ml)
        self.model_label = QtWidgets.QLabel("No solid selected")
        self.model_label.setToolTip("The solid CAD body that PrintFEA will mesh and analyze.")
        choose_model = QtWidgets.QPushButton("Use Selection")
        choose_model.setToolTip("Select one solid in the model tree or 3D view, then click here.")
        choose_model.clicked.connect(self._pick_model)
        choose_model.setMaximumWidth(160)
        ml.addWidget(self.model_label)
        model_action_row = QtWidgets.QHBoxLayout()
        model_action_row.addStretch(1)
        model_action_row.addWidget(choose_model)
        ml.addLayout(model_action_row)
        root.addWidget(self.model_box)

        # 2. Print settings ------------------------------------------------
        self.print_box = CollapsibleSection("2. Print Settings")
        self._section_titles['print'] = "2. Print Settings"
        pl = QtWidgets.QVBoxLayout()
        self.print_box.setContentLayout(pl)
        form = QtWidgets.QFormLayout()

        self.material_combo = QtWidgets.QComboBox()
        self.material_combo.addItems(sorted(MATERIALS.keys()))
        if "ASA (generic conservative)" in MATERIALS:
            self.material_combo.setCurrentText("ASA (generic conservative)")
        self.material_combo.setToolTip(
            "Generic conservative FDM material profile. Built-in profiles are screening assumptions, not coupon data for your exact printer and filament."
        )
        self.material_combo.currentIndexChanged.connect(self._update_material_summary)

        self.material_model = QtWidgets.QComboBox()
        self.material_model.addItems([
            "Layer-aware orthotropic (recommended)",
            "Isotropic conservative (legacy)",
        ])
        self.material_model.setToolTip(
            "Layer-aware mode uses different stiffness/allowables in the printed layer plane and through the layer stack."
        )
        self.material_model.currentIndexChanged.connect(self._update_material_summary)
        self.material_model.currentIndexChanged.connect(self._update_readiness)

        self.layer_height = QtWidgets.QDoubleSpinBox()
        self.layer_height.setRange(0.05, 1.0)
        self.layer_height.setValue(0.20)
        self.layer_height.setSuffix(" mm")
        self.layer_height.setToolTip(
            "Slicer layer height. Used to estimate physical layer count for the shell/core sampler and saved with the analysis."
        )

        self.walls = QtWidgets.QSpinBox()
        self.walls.setRange(0, 20)
        self.walls.setValue(4)
        self.walls.setToolTip(
            "Number of perimeter extrusion lines. Thin structural parts can become mostly perimeter even at modest infill."
        )

        self.line_width = QtWidgets.QDoubleSpinBox()
        self.line_width.setRange(0.20, 2.00)
        self.line_width.setDecimals(2)
        self.line_width.setSingleStep(0.01)
        self.line_width.setValue(0.42)
        self.line_width.setSuffix(" mm")
        self.line_width.setToolTip(
            "Slicer wall/perimeter line width. PrintFEA offsets each sampled layer inward by this amount once per requested wall."
        )

        self.infill = QtWidgets.QSpinBox()
        self.infill.setRange(0, 100)
        self.infill.setValue(40)
        self.infill.setSuffix(" %")
        self.infill.setToolTip(
            "Infill density applied to the remaining internal core after perimeter walls are accounted for."
        )

        self.structure_sampling = QtWidgets.QComboBox()
        self.structure_sampling.addItems([
            "Balanced (48 slices)",
            "Fast (24 slices)",
            "High accuracy (96 slices)",
        ])
        self.structure_sampling.setCurrentIndex(0)
        self.structure_sampling.setToolTip(
            "Maximum representative cross-sections used by the wall/infill estimator. Balanced is recommended. Fast is useful for difficult geometry; High accuracy may take substantially longer."
        )

        self.structure_workers = QtWidgets.QComboBox()
        self.structure_workers.addItems(["Auto (up to 4)", "1", "2", "4", "8"])
        self.structure_workers.setCurrentIndex(0)
        self.structure_workers.setToolTip(
            "CPU processes used for independent layer slices. GPU acceleration is not useful for these OCCT B-Rep operations; multiple CPU workers are. Auto uses up to four processes."
        )
        self.structure_timeout = QtWidgets.QDoubleSpinBox()
        self.structure_timeout.setRange(3.0, 120.0)
        self.structure_timeout.setValue(12.0)
        self.structure_timeout.setSingleStep(2.0)
        self.structure_timeout.setSuffix(" s")
        self.structure_timeout.setToolTip(
            "Maximum time allowed for one pathological slice before PrintFEA terminates that slice worker and uses a conservative no-extra-shell fallback for that slice."
        )

        form.addRow("Filament", self.material_combo)
        form.addRow("Layer height", self.layer_height)
        form.addRow("Walls", self.walls)
        form.addRow("Infill", self.infill)
        form.addRow("Material model", self.material_model)
        form.addRow("Wall line width", self.line_width)
        form.addRow("Structure sampling", self.structure_sampling)
        form.addRow("Structure workers", self.structure_workers)
        form.addRow("Slow-slice limit", self.structure_timeout)
        for field_widget in (self.material_combo, self.layer_height, self.walls, self.infill, self.material_model, self.line_width, self.structure_sampling, self.structure_workers, self.structure_timeout):
            field_label = form.labelForField(field_widget)
            if field_label is not None:
                field_label.setToolTip(field_widget.toolTip())
        pl.addLayout(form)
        self._advanced_widgets.extend([
            form.labelForField(self.material_model), self.material_model,
            form.labelForField(self.line_width), self.line_width,
            form.labelForField(self.structure_sampling), self.structure_sampling,
            form.labelForField(self.structure_workers), self.structure_workers,
            form.labelForField(self.structure_timeout), self.structure_timeout,
        ])

        self.structure_model = QtWidgets.QCheckBox("Account for wall count + infill")
        self.structure_model.setChecked(True)
        self.structure_model.setToolTip(
            "Use the layer-sliced perimeter/core model. Each sampled print layer is offset inward once per wall line; only the remaining core receives the entered infill density."
        )
        pl.addWidget(self.structure_model)

        self.structure_summary = QtWidgets.QLabel("")
        self.structure_summary.setWordWrap(True)
        self.structure_summary.setToolTip(
            "Estimated shell/core structure. This calculation is intentionally on-demand because detailed CAD slicing can take time."
        )
        pl.addWidget(self.structure_summary)

        self.calculate_structure_button = QtWidgets.QPushButton("Calculate Structure")
        self.calculate_structure_button.setToolTip(
            "Run the layer-sliced shell/core estimator now. Editing print settings only marks the estimate as needing refresh."
        )
        self.calculate_structure_button.clicked.connect(self._calculate_structure)
        self.calculate_structure_button.setMaximumWidth(180)
        self.cancel_structure_button = QtWidgets.QPushButton("Cancel")
        self.cancel_structure_button.setToolTip(
            "Stop the background print-structure calculation. The FreeCAD window remains usable while the worker is running."
        )
        self.cancel_structure_button.clicked.connect(self._cancel_structure_worker)
        self.cancel_structure_button.setVisible(False)
        self.cancel_structure_button.setMaximumWidth(100)
        structure_action_row = QtWidgets.QHBoxLayout()
        structure_action_row.addStretch(1)
        structure_action_row.addWidget(self.cancel_structure_button)
        structure_action_row.addWidget(self.calculate_structure_button)
        pl.addLayout(structure_action_row)

        self.material_summary = QtWidgets.QLabel("")
        self.material_summary.setWordWrap(True)
        self.material_summary.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.material_summary.setToolTip(
            "Effective layer-aware stiffness/strength after the selected filament and print-structure scaling are applied."
        )
        pl.addWidget(self.material_summary)
        self._advanced_widgets.append(self.material_summary)

        for widget in (self.layer_height, self.walls, self.line_width, self.infill):
            widget.valueChanged.connect(self._mark_structure_stale)
        self.structure_sampling.currentIndexChanged.connect(self._mark_structure_stale)
        self.structure_workers.currentIndexChanged.connect(self._mark_structure_stale)
        self.structure_timeout.valueChanged.connect(self._mark_structure_stale)
        self.structure_model.stateChanged.connect(self._mark_structure_stale)
        self.structure_model.stateChanged.connect(self._update_readiness)
        root.addWidget(self.print_box)

        # 3. Print orientation --------------------------------------------
        self.orientation_box = CollapsibleSection("3. Print Orientation")
        self._section_titles['orientation'] = "3. Print Orientation"
        ol = QtWidgets.QVBoxLayout()
        self.orientation_box.setContentLayout(ol)
        self.build_label = QtWidgets.QLabel("Build plate face: not selected")
        self.build_label.setToolTip(
            "Choose the flat CAD face that physically touches the printer bed. Green highlights indicate the captured build face."
        )
        ol.addWidget(self.build_label)

        build_buttons = QtWidgets.QHBoxLayout()
        btn_build = QtWidgets.QPushButton("Capture Selected Bottom Face")
        btn_build.setToolTip(
            "Select one flat face in the 3D view, then capture it. The green BUILD UP arrow should point in the printer's build direction."
        )
        btn_build.clicked.connect(self._capture_build_face)
        btn_clear_build = QtWidgets.QPushButton("Clear")
        btn_clear_build.setToolTip("Remove the captured build-plate face and BUILD UP preview.")
        btn_clear_build.clicked.connect(self._clear_build_face)
        build_buttons.addWidget(btn_build, 2)
        build_buttons.addWidget(btn_clear_build, 1)
        ol.addLayout(build_buttons)

        self.flip_build = QtWidgets.QCheckBox("Flip build direction")
        self.flip_build.setToolTip("Use this if the green BUILD UP arrow points into the build plate instead of away from it.")
        self.flip_build.stateChanged.connect(self._refresh_build_preview)
        self.flip_build.stateChanged.connect(self._mark_structure_stale)
        ol.addWidget(self.flip_build)

        self.build_vector_label = QtWidgets.QLabel("Build direction: not defined")
        self.build_vector_label.setToolTip("Global XYZ unit vector used as the layer-stack / material axis 3 direction.")
        ol.addWidget(self.build_vector_label)
        self._advanced_widgets.append(self.build_vector_label)

        build_size_row = QtWidgets.QHBoxLayout()
        build_size_label = QtWidgets.QLabel("BUILD UP arrow size")
        self.build_arrow_size = QtWidgets.QSpinBox()
        self.build_arrow_size.setRange(25, 500)
        self.build_arrow_size.setSingleStep(25)
        self.build_arrow_size.setValue(100)
        self.build_arrow_size.setSuffix(" %")
        self.build_arrow_size.setToolTip("Scale only the green preview arrow; this does not affect the analysis.")
        self.build_arrow_size.valueChanged.connect(self._refresh_build_preview)
        build_size_row.addWidget(build_size_label)
        build_size_row.addWidget(self.build_arrow_size, 1)
        build_size_widget = QtWidgets.QWidget()
        build_size_widget.setLayout(build_size_row)
        ol.addWidget(build_size_widget)
        self._advanced_widgets.append(build_size_widget)
        root.addWidget(self.orientation_box)

        # 4. Supports -----------------------------------------------------
        self.supports_box = CollapsibleSection("4. Mounting / Fixed Faces")
        self._section_titles['supports'] = "4. Mounting / Fixed Faces"
        sl = QtWidgets.QVBoxLayout()
        self.supports_box.setContentLayout(sl)
        self.fixed_label = QtWidgets.QLabel("0 faces captured")
        self.fixed_label.setToolTip("Blue highlights are treated as fully fixed supports in this simplified load case.")
        sl.addWidget(self.fixed_label)
        fixed_edit_row = QtWidgets.QHBoxLayout()
        fixed_edit_row.setContentsMargins(0, 0, 0, 0)
        btn_fixed = QtWidgets.QPushButton("Add Selected")
        btn_fixed.setToolTip("Select one or more mounting/support faces. Captured faces remain highlighted blue.")
        btn_fixed.clicked.connect(self._capture_fixed)
        btn_remove_fixed = QtWidgets.QPushButton("Remove Selected")
        btn_remove_fixed.setToolTip("Select one or more blue FIXED faces and remove only those faces from the captured set.")
        btn_remove_fixed.clicked.connect(self._remove_selected_fixed)
        btn_clear_fixed = QtWidgets.QPushButton("Clear all")
        btn_clear_fixed.setToolTip("Clear every captured FIXED face.")
        btn_clear_fixed.clicked.connect(self._clear_fixed)
        fixed_edit_row.addWidget(btn_fixed, 2)
        fixed_edit_row.addWidget(btn_remove_fixed, 2)
        fixed_edit_row.addWidget(btn_clear_fixed, 1)
        sl.addLayout(fixed_edit_row)
        root.addWidget(self.supports_box)

        # 5. Load ---------------------------------------------------------
        self.loads_box = CollapsibleSection("5. Load")
        self._section_titles['load'] = "5. Load"
        lf = QtWidgets.QFormLayout()
        lf.setHorizontalSpacing(10)
        lf.setVerticalSpacing(6)
        self.loads_box.setContentLayout(lf)
        self.load_label = QtWidgets.QLabel("0 faces captured")
        self.load_label.setToolTip("Orange highlights are the faces over which the entered force is distributed.")
        btn_load = QtWidgets.QPushButton("Add Selected")
        btn_load.setToolTip("Select one or more faces where the force acts. Captured faces remain highlighted orange/red.")
        btn_load.clicked.connect(self._capture_load)
        load_edit_widget = QtWidgets.QWidget()
        load_edit_row = QtWidgets.QHBoxLayout(load_edit_widget)
        load_edit_row.setContentsMargins(0, 0, 0, 0)
        btn_remove_load = QtWidgets.QPushButton("Remove selected")
        btn_remove_load.setToolTip("Select one or more orange LOADED faces and remove only those faces from the captured set.")
        btn_remove_load.clicked.connect(self._remove_selected_load)
        btn_clear_load = QtWidgets.QPushButton("Clear all")
        btn_clear_load.setToolTip("Clear every captured LOADED face and the force preview.")
        btn_clear_load.clicked.connect(self._clear_load)
        load_edit_row.addWidget(btn_load, 2)
        load_edit_row.addWidget(btn_remove_load, 2)
        load_edit_row.addWidget(btn_clear_load, 1)

        self.force = QtWidgets.QDoubleSpinBox()
        self.force.setRange(0.001, 1000000)
        self.force.setValue(10)
        self.force.setSuffix(" N")
        self.force.setDecimals(3)
        self.force.setToolTip("Total static force distributed across the captured LOADED face(s), in newtons.")

        self.direction = QtWidgets.QComboBox()
        self.direction.addItems([
            "+X  → global X positive", "-X  → global X negative",
            "+Y  → global Y positive", "-Y  → global Y negative",
            "+Z  → global Z positive", "-Z  → global Z negative",
        ])
        self.direction.setCurrentIndex(2)
        self.direction.setToolTip("Force direction in FreeCAD's global coordinate system. Verify it visually with the red FORCE arrow.")
        self.direction.currentIndexChanged.connect(self._refresh_force_preview)
        self.direction_vector_label = QtWidgets.QLabel("Force vector: (0, +1, 0)")
        self.direction_vector_label.setToolTip("Numeric global XYZ direction vector for the applied force.")
        self._advanced_widgets.append(self.direction_vector_label)

        self.force_arrow_size = QtWidgets.QSpinBox()
        self.force_arrow_size.setRange(25, 500)
        self.force_arrow_size.setSingleStep(25)
        self.force_arrow_size.setValue(100)
        self.force_arrow_size.setSuffix(" %")
        self.force_arrow_size.setToolTip("Scale only the red preview arrow; this does not change the force magnitude.")
        self.force_arrow_size.valueChanged.connect(self._refresh_force_preview)
        self.force_arrow_size.valueChanged.connect(self._refresh_point_load_previews)

        btn_preview_force = QtWidgets.QPushButton("Refresh Arrow")
        btn_preview_force.setToolTip("Redraw the force-direction preview on the captured loaded faces.")
        btn_preview_force.clicked.connect(self._refresh_force_preview)

        lf.addRow(self.load_label)
        lf.addRow(load_edit_widget)
        lf.addRow("Force", self.force)
        lf.addRow("Direction", self.direction)
        lf.addRow(self.direction_vector_label)
        lf.addRow("Force arrow size", self.force_arrow_size)
        lf.addRow(btn_preview_force)

        point_heading = QtWidgets.QLabel("<b>Point forces</b>")
        point_heading.setToolTip(
            "Point forces let you click an exact location on the model. After meshing, PrintFEA maps each click to the nearest mesh node on that surface. "
            "Local stress immediately at a point load is singular/mesh-sensitive, so use a distributed face load when a real contact area is known."
        )
        lf.addRow(point_heading)

        self.point_load_label = QtWidgets.QLabel("0 point forces")
        self.point_load_label.setToolTip("Each saved point force has its own magnitude, direction, and clicked surface location.")
        lf.addRow(self.point_load_label)

        self.point_force = QtWidgets.QDoubleSpinBox()
        self.point_force.setRange(0.001, 1000000)
        self.point_force.setValue(10.0)
        self.point_force.setSuffix(" N")
        self.point_force.setDecimals(3)
        self.point_force.setToolTip("Magnitude for the next point force you place.")

        self.point_direction = QtWidgets.QComboBox()
        self.point_direction.addItems([
            "+X  → global X positive", "-X  → global X negative",
            "+Y  → global Y positive", "-Y  → global Y negative",
            "+Z  → global Z positive", "-Z  → global Z negative",
        ])
        self.point_direction.setCurrentIndex(5)
        self.point_direction.setToolTip("Direction for the next clicked load. The placed load gets its own red arrow preview.")

        self.point_contact_diameter = QtWidgets.QDoubleSpinBox()
        self.point_contact_diameter.setRange(0.2, 1000.0)
        self.point_contact_diameter.setValue(5.0)
        self.point_contact_diameter.setSingleStep(0.5)
        self.point_contact_diameter.setSuffix(" mm")
        self.point_contact_diameter.setDecimals(2)
        self.point_contact_diameter.setToolTip(
            "Diameter of the surface contact patch centered on your click. The entered force is the TOTAL force and is distributed across nearby mesh nodes inside this patch."
        )
        self.point_ideal_load = QtWidgets.QCheckBox("Ideal mathematical point load")
        self.point_ideal_load.setChecked(False)
        self.point_ideal_load.setToolTip(
            "Advanced: put the full force on one FEM node. This creates a stress singularity and is not recommended for local strength/failure assessment."
        )
        self.point_ideal_load.toggled.connect(lambda checked: self.point_contact_diameter.setEnabled(not checked))

        self.point_load_list = QtWidgets.QListWidget()
        self.point_load_list.setMaximumHeight(96)
        self.point_load_list.setToolTip("Saved clicked loads. Select one row to edit it with the controls above, or select rows to remove them.")
        self.point_load_list.currentRowChanged.connect(self._point_load_selection_changed)

        point_buttons = QtWidgets.QWidget()
        point_button_row = QtWidgets.QHBoxLayout(point_buttons)
        point_button_row.setContentsMargins(0, 0, 0, 0)
        self.btn_pick_point_load = QtWidgets.QPushButton("Click model to add point force")
        self.btn_pick_point_load.setToolTip(
            "Arm point-pick mode. Then click anywhere on the selected CAD model surface to place a force using the magnitude and direction above."
        )
        self.btn_pick_point_load.clicked.connect(self._start_point_load_pick)
        self.btn_update_point_load = QtWidgets.QPushButton("Update selected")
        self.btn_update_point_load.setToolTip("Apply the current force, direction, and contact-diameter controls to the selected clicked load without moving its location.")
        self.btn_update_point_load.setEnabled(False)
        self.btn_update_point_load.clicked.connect(self._update_selected_point_load)
        btn_remove_point = QtWidgets.QPushButton("Remove selected")
        btn_remove_point.setToolTip("Remove the selected saved point-force rows.")
        btn_remove_point.clicked.connect(self._remove_selected_point_loads)
        btn_clear_points = QtWidgets.QPushButton("Clear all")
        btn_clear_points.setToolTip("Remove all saved point forces.")
        btn_clear_points.clicked.connect(self._clear_point_loads)
        point_button_row.addWidget(self.btn_pick_point_load, 2)
        point_button_row.addWidget(self.btn_update_point_load, 1)
        point_button_row.addWidget(btn_remove_point, 1)
        point_button_row.addWidget(btn_clear_points, 1)

        lf.addRow("Clicked load", self.point_force)
        lf.addRow("Point direction", self.point_direction)
        lf.addRow("Contact diameter", self.point_contact_diameter)
        lf.addRow(self.point_ideal_load)
        lf.addRow(self.point_load_list)
        lf.addRow(point_buttons)

        for field_widget in (self.force, self.direction, self.force_arrow_size, self.point_force, self.point_direction, self.point_contact_diameter):
            field_label = lf.labelForField(field_widget)
            if field_label is not None:
                field_label.setToolTip(field_widget.toolTip())
        self._advanced_widgets.extend([
            lf.labelForField(self.force_arrow_size), self.force_arrow_size,
            btn_preview_force, self.point_ideal_load,
        ])
        root.addWidget(self.loads_box)

        # 6. Analysis -----------------------------------------------------
        self.mesh_box = CollapsibleSection("6. Analysis")
        self._section_titles['analysis'] = "6. Analysis"
        mf = QtWidgets.QFormLayout()
        mf.setHorizontalSpacing(10)
        mf.setVerticalSpacing(6)
        self.mesh_box.setContentLayout(mf)
        self.quality = QtWidgets.QComboBox()
        self.quality.addItems(["Fast", "Normal", "Fine"])
        self.quality.setCurrentText("Normal")
        self.quality.setToolTip(
            "Fast = quick iteration; Normal = recommended default; Fine = useful for checking convergence of representative results."
        )
        self.target_sf = QtWidgets.QDoubleSpinBox()
        self.target_sf.setRange(1.0, 10.0)
        self.target_sf.setSingleStep(0.25)
        self.target_sf.setDecimals(2)
        self.target_sf.setValue(2.0)
        self.target_sf.setToolTip(
            "Desired screening margin. SF 2.0 corresponds to representative utilization at or below 50% of the selected directional allowable."
        )
        self.run_button = QtWidgets.QPushButton("RUN ANALYSIS")
        self.run_button.setMinimumHeight(44)
        self.run_button.setToolTip("Create the FEM model, mesh with Gmsh, solve with CalculiX, and open the PrintFEA result summary.")
        self.run_button.clicked.connect(self._run)
        mf.addRow("Mesh quality", self.quality)
        mf.addRow("Target safety factor", self.target_sf)
        mf.addRow(self.run_button)
        for field_widget in (self.quality, self.target_sf):
            field_label = mf.labelForField(field_widget)
            if field_label is not None:
                field_label.setToolTip(field_widget.toolTip())
        self.readiness_label = QtWidgets.QLabel("")
        self.readiness_label.setWordWrap(True)
        mf.addRow(self.readiness_label)
        root.addWidget(self.mesh_box)

        # Legacy in-wizard result widgets are retained off-screen for method
        # compatibility; v0.3 results are intentionally shown in the dedicated
        # independent Results window.
        self.results_box = QtWidgets.QGroupBox(self)
        self.results_box.setVisible(False)
        self.verdict_label = QtWidgets.QLabel("RESULT", self.results_box)
        self.result_detail = QtWidgets.QLabel("", self.results_box)
        self.result_values = {}
        for key in ("max_displacement", "peak_stress", "p99_stress", "allowable", "safety_factor", "peak_safety_factor", "hotspot", "mesh_mode"):
            self.result_values[key] = QtWidgets.QLabel("—", self.results_box)
        self.btn_show_stress = QtWidgets.QPushButton("Show STRESS", self.results_box)
        self.btn_show_displacement = QtWidgets.QPushButton("Show DISPLACEMENT", self.results_box)
        self.color_guide = QtWidgets.QLabel("", self.results_box)
        self.show_setup_arrows = QtWidgets.QCheckBox("Show setup markers", self.results_box)
        self.show_setup_arrows.setChecked(False)
        self.show_setup_arrows.stateChanged.connect(self._toggle_setup_arrows)

        root.addStretch(1)

        self.status = QtWidgets.QLabel("Ready")
        self.status.setWordWrap(True)
        self.status.setToolTip("Current PrintFEA activity/status.")
        self.status.setStyleSheet("padding: 5px 7px; border-top: 1px solid palette(mid); font-size: 90%;")
        outer.addWidget(self.status)

        self._toggle_advanced_options(False)
        self._update_readiness()

    def _open_help(self, section=None):
        from gui.help_dialog import show_help_dialog
        show_help_dialog(section=section)

    def _toggle_advanced_options(self, checked):
        visible = bool(checked)
        for widget in getattr(self, "_advanced_widgets", []):
            if widget is not None:
                try:
                    widget.setVisible(visible)
                except RuntimeError:
                    pass

    def _update_readiness(self, *_args):
        if not hasattr(self, "run_button"):
            return
        build_required = self._material_model_key() == "layer_aware" or bool(self.structure_model.isChecked())
        ready = {
            "model": self.model_obj is not None,
            "print": True,
            "orientation": (self._effective_build_direction() is not None) if build_required else True,
            "supports": bool(self.fixed_refs),
            "load": bool(self.load_refs or self.point_loads),
        }
        boxes = {
            "model": getattr(self, "model_box", None),
            "print": getattr(self, "print_box", None),
            "orientation": getattr(self, "orientation_box", None),
            "supports": getattr(self, "supports_box", None),
            "load": getattr(self, "loads_box", None),
        }
        for key, box in boxes.items():
            if box is not None:
                base = self._section_titles.get(key, box.title())
                box.setTitle(base + ("   ✓" if ready[key] else "   ○"))

        invalid_structure = bool(self.structure_model.isChecked() and self.walls.value() <= 0 and self.infill.value() <= 0)
        worker_running = self._structure_process is not None
        all_ready = all(ready.values()) and not invalid_structure and not worker_running
        self.run_button.setEnabled(all_ready)
        if worker_running:
            self.readiness_label.setText("Calculating print structure in background…")
        elif all_ready:
            self.readiness_label.setText("<b>✓ Ready to analyze</b>")
        else:
            missing = []
            if not ready["model"]:
                missing.append("model")
            if not ready["orientation"]:
                missing.append("print orientation")
            if not ready["supports"]:
                missing.append("fixed faces")
            if not ready["load"]:
                missing.append("loaded faces")
            if invalid_structure:
                missing.append("valid wall/infill settings")
            self.readiness_label.setText("Still needed: " + ", ".join(missing))

    def _material_model_key(self):
        return "layer_aware" if self.material_model.currentIndex() == 0 else "isotropic"

    def _structure_sample_budget(self):
        if not hasattr(self, "structure_sampling"):
            return 48
        return {0: 48, 1: 24, 2: 96}.get(int(self.structure_sampling.currentIndex()), 48)

    def _structure_worker_count(self):
        if not hasattr(self, "structure_workers"):
            return min(4, os.cpu_count() or 1)
        idx = int(self.structure_workers.currentIndex())
        return {0: min(4, os.cpu_count() or 1), 1: 1, 2: 2, 3: 4, 4: 8}.get(idx, min(4, os.cpu_count() or 1))

    def _structure_signature(self):
        build_dir = self._effective_build_direction() if hasattr(self, "flip_build") else None
        build_tuple = None if build_dir is None else tuple(round(float(v), 8) for v in (build_dir.x, build_dir.y, build_dir.z))
        model_name = getattr(self.model_obj, "Name", None)
        return (
            model_name, build_tuple, int(self.walls.value()),
            round(float(self.line_width.value()), 6), int(self.infill.value()),
            round(float(self.layer_height.value()), 6), bool(self.structure_model.isChecked()),
            int(self._structure_sample_budget()), int(self._structure_worker_count()),
            round(float(self.structure_timeout.value()), 3),
        )

    def _current_structure_estimate(self, progress_callback=None, yield_callback=None):
        build_dir = self._effective_build_direction() if hasattr(self, "flip_build") else None
        build_tuple = None if build_dir is None else (build_dir.x, build_dir.y, build_dir.z)
        return estimate_print_structure(
            self.model_obj,
            build_tuple,
            int(self.walls.value()),
            float(self.line_width.value()),
            int(self.infill.value()),
            enabled=bool(self.structure_model.isChecked()),
            layer_height_mm=float(self.layer_height.value()),
            max_slice_samples=int(self._structure_sample_budget()),
            progress_callback=progress_callback,
            yield_callback=yield_callback,
        )

    def _mark_structure_stale(self, *_args):
        # Cheap UI-only invalidation. Never launch B-Rep slicing from a spinbox,
        # checkbox, build-face capture, or arrow-preview event. If the user
        # edits settings while a background calculation is running, stop that
        # now-obsolete worker rather than burning CPU on a result we cannot use.
        if self._structure_process is not None:
            self._cancel_structure_worker(silent=True)
        self._structure_stale = True
        self._structure_signature_cached = None
        if not self.structure_model.isChecked():
            # Disabled mode is cheap and deterministic: fully dense.
            self._structure_estimate = self._current_structure_estimate()
            self._structure_stale = False
            self._structure_signature_cached = self._structure_signature()
        self._render_structure_summary()
        self._update_material_summary_only()
        self._update_readiness()

    def _render_structure_summary(self):
        try:
            enabled = bool(self.structure_model.isChecked())
            build_dir = self._effective_build_direction() if hasattr(self, "flip_build") else None
            if self._structure_process is not None:
                self.calculate_structure_button.setEnabled(False)
                self.cancel_structure_button.setVisible(True)
                return
            if not enabled:
                self.structure_summary.setText(
                    "<b>Wall/infill effect disabled:</b> treating the CAD solid as fully dense for stiffness and strength."
                )
                self.calculate_structure_button.setEnabled(False)
                return
            if self.model_obj is None:
                self.structure_summary.setText(
                    "<b>Print structure:</b> choose a solid model first."
                )
                self.calculate_structure_button.setEnabled(False)
                return
            if build_dir is None:
                self.structure_summary.setText(
                    "<b>Print structure:</b> capture the BUILD PLATE / BOTTOM face first. No layer slicing is running yet."
                )
                self.calculate_structure_button.setEnabled(False)
                return
            self.calculate_structure_button.setEnabled(True)
            valid_cache = (
                not self._structure_stale
                and self._structure_estimate is not None
                and self._structure_signature_cached == self._structure_signature()
            )
            if not valid_cache:
                self.structure_summary.setText(
                    "<b>Print structure: needs refresh.</b><br>Settings changed. Click <b>Calculate print structure</b> when ready, or simply run the analysis and PrintFEA will calculate it once before meshing."
                )
                return
            st = self._structure_estimate
            fallback = (
                "<br><b>⚠ Conservative slice fallback:</b> " + st.get("fallback_reason", "slice calculation partially failed")
                if st.get("fallback_used") else ""
            )
            self.structure_summary.setText(
                "<b>Layer-sliced print structure</b><br>"
                f"Shell ≈ {st['shell_fraction'] * 100.0:.1f}% of CAD volume; "
                f"core ≈ {(1.0 - st['shell_fraction']) * 100.0:.1f}%; "
                f"effective material ≈ {st['effective_material_fraction'] * 100.0:.1f}%.<br>"
                f"Solver stiffness ×{st['stiffness_scale']:.2f}; screening strength ×{st['strength_scale']:.2f}.<br>"
                f"Slices: {int(st.get('sampled_slice_count', 0))} sampled / ~{int(st.get('estimated_layer_count', 0))} print layers; method: {st.get('method', 'not reported')}." + fallback
            )
        except Exception as exc:
            self.structure_summary.setText(f"<b>Print structure:</b> status unavailable ({exc})")

    def _plugin_root(self):
        try:
            return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        except Exception:
            return os.path.join(App.getUserAppDataDir(), "Mod", "PrintFEA")

    def _find_freecadcmd(self):
        """Locate the headless executable that matches the running FreeCAD."""
        candidates = []
        env_override = os.environ.get("PRINTFEA_FREECADCMD")
        if env_override:
            candidates.append(env_override)
        for name in ("FreeCADCmd", "freecadcmd", "FreeCADCmdLink"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
        try:
            current_exe = os.path.realpath("/proc/self/exe")
            bindir = os.path.dirname(current_exe)
            candidates.extend([
                os.path.join(bindir, "FreeCADCmd"),
                os.path.join(bindir, "freecadcmd"),
            ])
        except Exception:
            pass
        try:
            home = App.getHomePath()
            candidates.extend([
                os.path.join(home, "bin", "FreeCADCmd"),
                os.path.join(home, "bin", "freecadcmd"),
                os.path.join(home, "FreeCADCmd"),
            ])
        except Exception:
            pass
        seen = set()
        for path in candidates:
            if not path:
                continue
            path = os.path.abspath(os.path.expanduser(str(path)))
            if path in seen:
                continue
            seen.add(path)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return None

    def _cleanup_structure_worker_files(self):
        td = self._structure_tempdir
        self._structure_tempdir = None
        if td:
            try:
                shutil.rmtree(td, ignore_errors=True)
            except Exception:
                pass

    def _cancel_structure_worker(self, *_args, silent=False):
        proc = self._structure_process
        if proc is None:
            return
        self._structure_worker_cancelled = True
        self._pending_run_after_structure = False
        try:
            proc.kill()
        except Exception:
            pass
        if not silent:
            self.status.setText("Cancelling print-structure calculation…")

    def _structure_worker_stdout_ready(self):
        proc = self._structure_process
        if proc is None:
            return
        try:
            data = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
        except Exception:
            return
        self._structure_process_stdout += data
        # QProcess can split output at arbitrary byte boundaries. Process only
        # complete lines and retain the final partial line for the next signal.
        lines = self._structure_process_stdout.split("\n")
        self._structure_process_stdout = lines.pop() if lines else ""
        for line in lines:
            line = line.strip()
            if not line.startswith("PRINTFEA_PROGRESS:"):
                continue
            try:
                _, done, total = line.split(":", 2)
                done, total = int(done), int(total)
                pct = int(round(100.0 * done / max(1, total)))
            except Exception:
                continue
            self.structure_summary.setText(
                f"<b>Calculating print structure in background… {pct}%</b><br>"
                f"Sampled layer {done} of {total}. You can keep rotating/inspecting the model while this runs."
            )
            self.status.setText(f"Calculating print structure in background… {pct}%")

    def _structure_worker_stderr_ready(self):
        proc = self._structure_process
        if proc is None:
            return
        try:
            self._structure_process_stderr += bytes(proc.readAllStandardError()).decode("utf-8", "replace")
        except Exception:
            pass

    def _structure_worker_error(self, _error):
        proc = self._structure_process
        if proc is None:
            return
        try:
            msg = proc.errorString()
        except Exception:
            msg = "Could not start the background FreeCADCmd worker."
        self._structure_process_stderr += ("\n" + msg)

    def _structure_worker_finished(self, exit_code=0, _exit_status=None):
        proc = self._structure_process
        tempdir = self._structure_tempdir
        worker_signature = self._structure_worker_signature
        for_run = bool(self._structure_worker_for_run)
        cancelled = bool(self._structure_worker_cancelled)
        pending_run = bool(self._pending_run_after_structure)
        self._structure_process = None
        self._structure_worker_signature = None
        self._structure_worker_for_run = False
        self._structure_worker_cancelled = False
        self._pending_run_after_structure = False
        self.calculate_structure_button.setEnabled(True)
        self.calculate_structure_button.setText("Calculate Structure")
        self.cancel_structure_button.setVisible(False)

        result_payload = None
        result_path = os.path.join(tempdir, "result.json") if tempdir else ""
        if result_path and os.path.exists(result_path):
            try:
                with open(result_path, "r", encoding="utf-8") as fh:
                    result_payload = json.load(fh)
            except Exception as exc:
                result_payload = {"ok": False, "error": f"Could not read worker result: {exc}"}
        self._cleanup_structure_worker_files()

        if cancelled:
            self.status.setText("Print-structure calculation cancelled.")
            self._render_structure_summary()
            self._update_readiness()
            return

        if not result_payload or not result_payload.get("ok"):
            detail = (result_payload or {}).get("error") or self._structure_process_stderr.strip() or f"FreeCADCmd exited with code {exit_code}."
            log_exception("Background structure worker failed", RuntimeError(str(detail)))
            self._structure_stale = True
            self._render_structure_summary()
            self.status.setText("Print-structure calculation failed.")
            self._warn(
                "The background print-structure calculation failed. The FEM solve was not started.\n\n"
                + str(detail)
                + "\n\nIf FreeCADCmd is installed under an unusual name, set PRINTFEA_FREECADCMD to its full path."
            )
            self._update_readiness()
            return

        st = result_payload.get("structure") or {}
        # If settings changed while the worker was running, don't accidentally
        # apply an estimate from the old geometry/settings to a new solve.
        if worker_signature != self._structure_signature():
            self._structure_stale = True
            self.status.setText("Print structure finished, but settings changed during calculation; refresh required.")
            self._render_structure_summary()
            self._update_material_summary_only()
            self._update_readiness()
            return

        self._structure_estimate = st
        self._structure_stale = False
        self._structure_signature_cached = worker_signature
        self._render_structure_summary()
        self._update_material_summary_only()
        self._update_readiness()
        self.status.setText("Print structure calculated in background.")

        if for_run and pending_run:
            QtCore.QTimer.singleShot(0, self._run_with_current_structure)

    def _start_structure_worker(self, signature, for_run=False):
        if self._structure_process is not None:
            if for_run:
                self._pending_run_after_structure = True
            return None

        freecadcmd = self._find_freecadcmd()
        if not freecadcmd:
            raise RuntimeError(
                "Could not find FreeCADCmd/freecadcmd. PrintFEA uses a separate headless FreeCAD process for wall/infill slicing so difficult geometry cannot freeze the GUI."
            )

        tempdir = tempfile.mkdtemp(prefix="printfea_structure_")
        brep_path = os.path.join(tempdir, "model.brep")
        input_path = os.path.join(tempdir, "input.json")
        result_path = os.path.join(tempdir, "result.json")
        try:
            # BREP is OCCT-native, compact, and preserves exact CAD topology.
            # Exporting it is normally fast; all expensive slicing happens in
            # the child FreeCADCmd process.
            self.model_obj.Shape.exportBrep(brep_path)
            build_dir = self._effective_build_direction()
            cfg = {
                "plugin_root": self._plugin_root(),
                "brep_path": brep_path,
                "result_path": result_path,
                "build_direction": [float(build_dir.x), float(build_dir.y), float(build_dir.z)],
                "walls": int(self.walls.value()),
                "line_width_mm": float(self.line_width.value()),
                "infill_percent": int(self.infill.value()),
                "layer_height_mm": float(self.layer_height.value()),
                "max_slice_samples": int(self._structure_sample_budget()),
                "parallel_workers": int(self._structure_worker_count()),
                "slice_timeout_seconds": float(self.structure_timeout.value()),
                "enabled": True,
            }
            with open(input_path, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh)
        except Exception:
            shutil.rmtree(tempdir, ignore_errors=True)
            raise

        proc = QtCore.QProcess(self)
        # Do not pass PrintFEA's JSON config as a second positional argument to
        # FreeCADCmd: FreeCAD treats positional paths as files to open/import.
        # Environment transport avoids accidental FEM JSON-mesh importer errors.
        try:
            env = QtCore.QProcessEnvironment.systemEnvironment()
            env.insert("PRINTFEA_STRUCTURE_CONFIG", input_path)
            proc.setProcessEnvironment(env)
        except Exception:
            pass
        try:
            proc.setProcessChannelMode(QtCore.QProcess.SeparateChannels)
        except Exception:
            pass
        proc.readyReadStandardOutput.connect(self._structure_worker_stdout_ready)
        proc.readyReadStandardError.connect(self._structure_worker_stderr_ready)
        try:
            proc.errorOccurred.connect(self._structure_worker_error)
        except Exception:
            pass
        proc.finished.connect(self._structure_worker_finished)
        self._structure_process = proc
        self._structure_tempdir = tempdir
        self._structure_worker_signature = signature
        self._structure_worker_for_run = bool(for_run)
        self._structure_worker_cancelled = False
        self._pending_run_after_structure = bool(for_run)
        self._structure_process_stdout = ""
        self._structure_process_stderr = ""

        self.calculate_structure_button.setEnabled(False)
        self.calculate_structure_button.setText("Calculating…")
        self.cancel_structure_button.setVisible(True)
        self.structure_summary.setText(
            f"<b>Calculating print structure in background…</b><br>"
            f"Up to {self._structure_sample_budget()} representative slices across {self._structure_worker_count()} CPU worker(s). "
            f"Slices taking longer than {float(self.structure_timeout.value()):g}s are conservatively skipped instead of hanging indefinitely."
        )
        self.status.setText("Starting background print-structure worker…")
        worker_script = os.path.join(self._plugin_root(), "workers", "structure_worker.py")
        proc.setWorkingDirectory(tempdir)
        proc.start(freecadcmd, [worker_script])
        self.status.setText("Calculating print structure in background…")
        return None

    def _calculate_structure(self, *_args, for_run=False):
        if not self.structure_model.isChecked():
            self._structure_estimate = self._current_structure_estimate()
            self._structure_stale = False
            self._structure_signature_cached = self._structure_signature()
            self._render_structure_summary()
            self._update_material_summary_only()
            return self._structure_estimate
        if self.model_obj is None:
            if for_run:
                raise ValueError("Choose a model before calculating print structure.")
            self._warn("Choose a model before calculating print structure.")
            return None
        if self._effective_build_direction() is None:
            if for_run:
                raise ValueError("Capture the BUILD PLATE / BOTTOM face before calculating print structure.")
            self._warn("Capture the BUILD PLATE / BOTTOM face before calculating print structure.")
            return None

        signature = self._structure_signature()
        if (
            not self._structure_stale
            and self._structure_estimate is not None
            and self._structure_signature_cached == signature
        ):
            return self._structure_estimate

        self._start_structure_worker(signature, for_run=for_run)
        return None

    def _update_structure_summary(self, *_args):
        # Compatibility helper for older code paths: render cached state only.
        self._render_structure_summary()
        self._update_material_summary_only()

    def _update_material_summary_only(self):
        try:
            material = MATERIALS[self.material_combo.currentText()]
            st = None
            structure_pending = False
            if hasattr(self, "structure_model") and self.structure_model.isChecked():
                valid_cache = (
                    not self._structure_stale
                    and self._structure_estimate is not None
                    and self._structure_signature_cached == self._structure_signature()
                )
                if valid_cache:
                    st = self._structure_estimate
                else:
                    structure_pending = True
            elif hasattr(self, "structure_model"):
                st = self._structure_estimate

            pending_note = "<br><i>Wall/infill scaling pending — calculate print structure before solving.</i>" if structure_pending else ""
            if self._material_model_key() == "layer_aware":
                p = layer_aware_properties(material, structure=st)
                heading = "Base layer-aware screening properties" if structure_pending else "Effective layer-aware screening properties"
                self.material_summary.setText(
                    f"<b>{heading}</b><br>"
                    f"Layer plane: E1 = E2 = {p['E1']:.0f} MPa, allowable = {p['allow_xy']:.1f} MPa<br>"
                    f"Build / Z: E3 = {p['E3']:.0f} MPa, allowable = {p['allow_z']:.1f} MPa<br>"
                    f"Inter-layer shear allowable = {p['allow_shear_z']:.1f} MPa" + pending_note
                )
            else:
                stiff = float((st or {}).get("stiffness_scale", 1.0))
                strength = float((st or {}).get("strength_scale", 1.0))
                heading = "Base isotropic screening" if structure_pending else "Legacy isotropic screening"
                self.material_summary.setText(
                    f"<b>{heading}</b><br>"
                    f"Effective E ≈ {float(material['youngs_modulus_mpa']) * stiff:.0f} MPa, "
                    f"effective allowable ≈ {float(material['allowable_mpa']) * strength:.1f} MPa in every direction. "
                    "Print orientation will be recorded but will not affect directional anisotropy." + pending_note
                )
        except Exception:
            pass

    def _update_material_summary(self, *_args):
        self._render_structure_summary()
        self._update_material_summary_only()

    def _auto_pick_model(self):
        sel = Gui.Selection.getSelection()
        if len(sel) == 1 and hasattr(sel[0], "Shape"):
            self.model_obj = sel[0]
            self.model_label.setText(f"{sel[0].Label} ({sel[0].Name})")
            if hasattr(self, "structure_summary"):
                self._mark_structure_stale()
            self._update_readiness()

    def _pick_model(self):
        sel = Gui.Selection.getSelection()
        if len(sel) != 1 or not hasattr(sel[0], "Shape"):
            self._warn("Select exactly one solid object in the 3D view/tree.")
            return
        if self.point_picker is not None:
            try:
                self.point_picker.stop(notify=False)
            except Exception:
                pass
        self.model_obj = sel[0]
        self.fixed_refs = []
        self.load_refs = []
        self.point_loads = []
        self.point_picker = None
        self.build_ref = None
        self.build_direction = None
        self.last_analysis = None
        self.last_result = None
        self.last_pipeline = None
        self.last_summary = None
        self.results_box.setVisible(False)
        if self.results_dialog is not None:
            try:
                self.results_dialog.close()
            except RuntimeError:
                pass
            self.results_dialog = None
        self._setup_markers_visible = True
        # The slicer-style shell/core estimator is intentionally NOT live.
        # Geometry slicing/offsetting can take many seconds on detailed B-Reps,
        # so edits merely invalidate the cached estimate until the user asks for
        # a refresh (or starts a solve).
        self._structure_estimate = None
        self._structure_stale = True
        self._structure_signature_cached = None
        self.previews.clear()
        self.model_label.setText(f"{sel[0].Label} ({sel[0].Name})")
        self.fixed_label.setText("0 faces captured")
        self.load_label.setText("0 faces captured")
        self._refresh_point_load_list()
        self.build_label.setText("Build plate face: not selected")
        self.build_vector_label.setText("Build direction: not defined")
        self._mark_structure_stale()
        self._update_readiness()

    def _capture_face_refs(self, exactly_one=False):
        if self.model_obj is None:
            raise ValueError("Choose the model before selecting faces.")
        refs = []
        for ex in Gui.Selection.getSelectionEx():
            if ex.Object != self.model_obj:
                continue
            for sub in ex.SubElementNames:
                if sub.startswith("Face"):
                    refs.append((ex.Object, sub))
        if not refs:
            raise ValueError("Select one or more faces on the chosen model in the 3D view first.")
        if exactly_one and len(refs) != 1:
            raise ValueError("Select exactly one face for the build plate / bottom face.")
        return refs

    def _face_from_ref(self, ref):
        obj, sub = ref
        try:
            return obj.Shape.getElement(sub)
        except Exception:
            index = int(sub.replace("Face", "")) - 1
            return obj.Shape.Faces[index]

    def _capture_build_face(self):
        try:
            self.build_ref = self._capture_face_refs(exactly_one=True)[0]
            self._setup_markers_visible = True
            face = self._face_from_ref(self.build_ref)
            u0, u1, v0, v1 = face.ParameterRange
            normal = face.normalAt((u0 + u1) * 0.5, (v0 + v1) * 0.5)
            if normal.Length <= 1e-12:
                raise ValueError("Could not determine a surface normal for that face. Choose a flat build-plate face.")
            normal = normal / normal.Length
            # For a solid's outside bottom face, the outward normal normally
            # points into the plate, so build direction is the opposite vector.
            self.build_direction = -normal
            self.build_label.setText(f"Build plate face: {self.build_ref[1]}")
            self.status.setText("Build-plate face captured. Check the green preview arrow.")
            self._refresh_build_preview()
            self._refresh_face_highlights()
            self._mark_structure_stale()
            self._update_readiness()
            Gui.Selection.clearSelection()
        except Exception as exc:
            self._warn(str(exc))

    def _clear_build_face(self):
        """Remove the captured build-plate face and all related previews."""
        self.build_ref = None
        self.build_direction = None
        try:
            self.flip_build.setChecked(False)
        except Exception:
            pass
        self.build_label.setText("Build plate face: not selected")
        self.build_vector_label.setText("Build direction: not defined")
        self.previews.clear("build_face")
        self.previews.clear("build")
        self._mark_structure_stale()
        Gui.Selection.clearSelection()
        self.status.setText("Build-plate face cleared. Select a new bottom face when ready.")
        self._update_readiness()

    def _effective_build_direction(self):
        if self.build_direction is None:
            return None
        d = App.Vector(self.build_direction.x, self.build_direction.y, self.build_direction.z)
        if self.flip_build.isChecked():
            d = -d
        return d

    def _refresh_build_preview(self, *_args):
        if not self._setup_markers_visible:
            self.previews.clear("build")
            return
        if self.build_ref is None or self.build_direction is None or self.model_obj is None:
            self.previews.clear("build")
            return
        try:
            d = self._effective_build_direction()
            face = self._face_from_ref(self.build_ref)
            start = face.CenterOfMass
            scale = max(self.model_obj.Shape.BoundBox.DiagonalLength * 0.28, 8.0)
            scale *= self.build_arrow_size.value() / 100.0
            self.previews.show_arrow("build", start, d, scale, "BUILD UP", (0.2, 0.95, 0.35))
            self.build_vector_label.setText(
                f"Build direction: ({d.x:+.3f}, {d.y:+.3f}, {d.z:+.3f})"
            )
        except Exception as exc:
            self.status.setText(f"Could not preview build direction: {exc}")

    def _capture_fixed(self):
        try:
            self.fixed_refs = self._capture_face_refs()
            self._setup_markers_visible = True
            self.fixed_label.setText(f"{len(self.fixed_refs)} faces captured")
            self.status.setText("Fixed faces captured. They remain highlighted in blue.")
            self._refresh_face_highlights()
            Gui.Selection.clearSelection()
            self._update_readiness()
        except Exception as exc:
            self._warn(str(exc))

    def _capture_load(self):
        try:
            self.load_refs = self._capture_face_refs()
            self._setup_markers_visible = True
            self.load_label.setText(f"{len(self.load_refs)} faces captured")
            self.status.setText("Loaded faces captured. They remain highlighted in orange/red; check the force arrow.")
            self._refresh_face_highlights()
            Gui.Selection.clearSelection()
            self._refresh_force_preview()
            self._update_readiness()
        except Exception as exc:
            self._warn(str(exc))

    @staticmethod
    def _ref_key(ref):
        obj, sub = ref
        return (getattr(obj, "Name", str(obj)), sub)

    def _remove_selected_fixed(self):
        try:
            selected = self._capture_face_refs()
            selected_keys = {self._ref_key(ref) for ref in selected}
            before = len(self.fixed_refs)
            self.fixed_refs = [ref for ref in self.fixed_refs if self._ref_key(ref) not in selected_keys]
            removed = before - len(self.fixed_refs)
            if removed == 0:
                raise ValueError("None of the selected faces are currently captured as FIXED.")
            self.fixed_label.setText(f"{len(self.fixed_refs)} faces captured")
            self._setup_markers_visible = True
            self._refresh_face_highlights()
            Gui.Selection.clearSelection()
            self.status.setText(f"Removed {removed} FIXED face(s). Remaining FIXED faces stay highlighted in blue.")
            self._update_readiness()
        except Exception as exc:
            self._warn(str(exc))

    def _clear_fixed(self):
        count = len(self.fixed_refs)
        self.fixed_refs = []
        self.fixed_label.setText("0 faces captured")
        self.previews.clear("fixed_faces")
        Gui.Selection.clearSelection()
        self.status.setText(f"Cleared {count} FIXED face(s)." if count else "No FIXED faces were captured.")
        self._update_readiness()

    def _remove_selected_load(self):
        try:
            selected = self._capture_face_refs()
            selected_keys = {self._ref_key(ref) for ref in selected}
            before = len(self.load_refs)
            self.load_refs = [ref for ref in self.load_refs if self._ref_key(ref) not in selected_keys]
            removed = before - len(self.load_refs)
            if removed == 0:
                raise ValueError("None of the selected faces are currently captured as LOADED.")
            self.load_label.setText(f"{len(self.load_refs)} faces captured")
            self._setup_markers_visible = True
            self._refresh_face_highlights()
            self._refresh_force_preview()
            Gui.Selection.clearSelection()
            self.status.setText(f"Removed {removed} LOADED face(s). Remaining LOADED faces stay highlighted in orange/red.")
            self._update_readiness()
        except Exception as exc:
            self._warn(str(exc))

    def _clear_load(self):
        count = len(self.load_refs)
        self.load_refs = []
        self.load_label.setText("0 faces captured")
        self.previews.clear("load_faces")
        self.previews.clear("force")
        Gui.Selection.clearSelection()
        self.status.setText(f"Cleared {count} LOADED face(s) and the force preview." if count else "No LOADED faces were captured.")
        self._update_readiness()

    def _point_direction_vector(self):
        return [
            (1, 0, 0),
            (-1, 0, 0),
            (0, 1, 0),
            (0, -1, 0),
            (0, 0, 1),
            (0, 0, -1),
        ][self.point_direction.currentIndex()]

    def _start_point_load_pick(self):
        if self.model_obj is None:
            self._warn("Choose the model before adding a point force.")
            return
        if self.point_picker is not None and getattr(self.point_picker, "active", False):
            try:
                self.point_picker.stop(notify=False)
            except Exception:
                pass
            self.point_picker = None
            self.btn_pick_point_load.setText("Click model to add point force")
            self.status.setText("Point-force pick mode cancelled.")
            return
        if self.point_picker is not None:
            try:
                self.point_picker.stop(notify=False)
            except Exception:
                pass
        self.point_picker = SurfacePointPicker(
            self.model_obj,
            self._point_load_picked,
            self._point_load_pick_cancelled,
        )
        self.point_picker.start()
        self.btn_pick_point_load.setText("Click a point on the model…")
        self.status.setText(
            "Point-force pick mode: click anywhere on the selected model surface. "
            "Contact-patch loads will distribute the entered TOTAL force over nearby mesh nodes on that surface after meshing."
        )

    def _point_load_pick_cancelled(self):
        if hasattr(self, "btn_pick_point_load"):
            self.btn_pick_point_load.setText("Click model to add point force")

    def _point_load_picked(self, pos, sub_name):
        try:
            dx, dy, dz = self._point_direction_vector()
            ideal = bool(self.point_ideal_load.isChecked())
            item = {
                "point": (float(pos.x), float(pos.y), float(pos.z)),
                "subelement": str(sub_name),
                "force_n": float(self.point_force.value()),
                "direction": (float(dx), float(dy), float(dz)),
                "application": "ideal_point" if ideal else "contact_patch",
                "contact_diameter_mm": 0.0 if ideal else float(self.point_contact_diameter.value()),
            }
            self.point_loads.append(item)
            self._refresh_point_load_list()
            self._refresh_point_load_previews()
            self.status.setText(
                f"Added clicked load #{len(self.point_loads)}: {item['force_n']:.3f} N total at "
                f"({pos.x:.2f}, {pos.y:.2f}, {pos.z:.2f}) mm on {sub_name}"
                + (" as an ideal point load." if ideal else f" over a Ø{item['contact_diameter_mm']:.2f} mm contact patch.")
            )
            self._update_readiness()
        finally:
            self.btn_pick_point_load.setText("Click model to add point force")
            self.point_picker = None

    def _point_load_selection_changed(self, row):
        valid = 0 <= int(row) < len(self.point_loads)
        if hasattr(self, "btn_update_point_load"):
            self.btn_update_point_load.setEnabled(valid)
        if not valid:
            return
        load = self.point_loads[int(row)]
        self.point_force.setValue(float(load.get("force_n", self.point_force.value())))
        dirs = [(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)]
        d = tuple(int(round(float(v))) for v in load.get("direction", (0,0,-1)))
        if d in dirs:
            self.point_direction.setCurrentIndex(dirs.index(d))
        ideal = str(load.get("application") or "contact_patch") == "ideal_point"
        self.point_ideal_load.setChecked(ideal)
        if not ideal:
            self.point_contact_diameter.setValue(float(load.get("contact_diameter_mm", 5.0) or 5.0))
        self.status.setText(f"Editing clicked load #{int(row)+1}. Change the controls, then click Update selected.")

    def _update_selected_point_load(self):
        row = self.point_load_list.currentRow()
        if row < 0 or row >= len(self.point_loads):
            self._warn("Select one clicked-load row to update.")
            return
        dx, dy, dz = self._point_direction_vector()
        ideal = bool(self.point_ideal_load.isChecked())
        load = self.point_loads[row]
        load["force_n"] = float(self.point_force.value())
        load["direction"] = (float(dx), float(dy), float(dz))
        load["application"] = "ideal_point" if ideal else "contact_patch"
        load["contact_diameter_mm"] = 0.0 if ideal else float(self.point_contact_diameter.value())
        self._refresh_point_load_list(select_row=row)
        self._refresh_point_load_previews()
        self.status.setText(
            f"Updated clicked load #{row+1}: {load['force_n']:.3f} N "
            + ("as an ideal point load." if ideal else f"over a Ø{load['contact_diameter_mm']:.2f} mm contact patch.")
        )
        self._update_readiness()

    def _refresh_point_load_list(self, select_row=None):
        if not hasattr(self, "point_load_list"):
            return
        self.point_load_list.clear()
        axis_names = {
            (1.0, 0.0, 0.0): "+X", (-1.0, 0.0, 0.0): "-X",
            (0.0, 1.0, 0.0): "+Y", (0.0, -1.0, 0.0): "-Y",
            (0.0, 0.0, 1.0): "+Z", (0.0, 0.0, -1.0): "-Z",
        }
        for i, load in enumerate(self.point_loads, start=1):
            p = load.get("point", (0.0, 0.0, 0.0))
            d = tuple(float(v) for v in load.get("direction", (0, 0, 1)))
            dname = axis_names.get(d, str(d))
            app = str(load.get("application") or "ideal_point")
            if app == "contact_patch":
                app_text = f"Ø{float(load.get('contact_diameter_mm', 5.0)):.2f} mm patch"
            else:
                app_text = "ideal point"
            self.point_load_list.addItem(
                f"#{i}  {float(load.get('force_n', 0.0)):.3f} N {dname} · {app_text}  @ "
                f"({p[0]:.2f}, {p[1]:.2f}, {p[2]:.2f}) mm"
            )
        self.point_load_label.setText(
            f"{len(self.point_loads)} clicked load" + ("" if len(self.point_loads) == 1 else "s")
        )
        if select_row is not None and 0 <= int(select_row) < self.point_load_list.count():
            self.point_load_list.setCurrentRow(int(select_row))

    def _refresh_point_load_previews(self, *_args):
        # Clear old indexed markers/arrows first.
        for key in list(getattr(self.previews, "_nodes", {}).keys()):
            if (str(key).startswith("point_force_") or str(key).startswith("point_marker_")
                    or str(key).startswith("point_patch_")):
                self.previews.clear(key)
        if not self._setup_markers_visible or self.model_obj is None:
            return
        diag = max(self.model_obj.Shape.BoundBox.DiagonalLength, 1.0)
        length = max(diag * 0.24, 8.0) * (self.force_arrow_size.value() / 100.0)
        marker_r = max(diag * 0.008, 0.45)
        for i, load in enumerate(self.point_loads):
            p = load.get("point", (0.0, 0.0, 0.0))
            d = load.get("direction", (0.0, 0.0, 1.0))
            pos = App.Vector(*p)
            app = str(load.get("application") or "ideal_point")
            contact_d = float(load.get("contact_diameter_mm", 0.0) or 0.0)
            if app == "contact_patch":
                self.previews.show_contact_disk(
                    f"point_patch_{i}", pos, App.Vector(*d), contact_d, f"Ø{contact_d:.1f} mm",
                    (1.0, 0.18, 0.06), 0.55
                )
                self.previews.show_marker(
                    f"point_marker_{i}", pos, marker_r * 0.60, "", (1.0, 0.18, 0.06), 0.05
                )
            else:
                self.previews.show_marker(
                    f"point_marker_{i}", pos, marker_r, "POINT", (1.0, 0.18, 0.06), 0.15
                )
            self.previews.show_arrow(
                f"point_force_{i}", pos, App.Vector(*d), length, f"F{i + 1}", (1.0, 0.25, 0.12)
            )

    def _remove_selected_point_loads(self):
        rows = sorted({idx.row() for idx in self.point_load_list.selectedIndexes()}, reverse=True)
        if not rows:
            self._warn("Select one or more point-force rows to remove.")
            return
        for row in rows:
            if 0 <= row < len(self.point_loads):
                del self.point_loads[row]
        self._refresh_point_load_list()
        self._refresh_point_load_previews()
        self.status.setText(f"Removed {len(rows)} point force(s).")
        self._update_readiness()

    def _clear_point_loads(self):
        count = len(self.point_loads)
        self.point_loads = []
        self._refresh_point_load_list()
        self._refresh_point_load_previews()
        self.status.setText(f"Cleared {count} point force(s)." if count else "No point forces were saved.")
        self._update_readiness()

    def _direction_vector(self):
        return [
            (1, 0, 0),
            (-1, 0, 0),
            (0, 1, 0),
            (0, -1, 0),
            (0, 0, 1),
            (0, 0, -1),
        ][self.direction.currentIndex()]

    def _refresh_force_preview(self, *_args):
        dx, dy, dz = self._direction_vector()
        if not self._setup_markers_visible:
            self.previews.clear("force")
            return
        self.direction_vector_label.setText(f"Force vector: ({dx:+d}, {dy:+d}, {dz:+d})")
        if not self.load_refs or self.model_obj is None:
            self.previews.clear("force")
            return
        try:
            centers = [self._face_from_ref(ref).CenterOfMass for ref in self.load_refs]
            start = App.Vector(
                sum(v.x for v in centers) / len(centers),
                sum(v.y for v in centers) / len(centers),
                sum(v.z for v in centers) / len(centers),
            )
            scale = max(self.model_obj.Shape.BoundBox.DiagonalLength * 0.32, 10.0)
            scale *= self.force_arrow_size.value() / 100.0
            self.previews.show_arrow(
                "force",
                start,
                App.Vector(dx, dy, dz),
                scale,
                "FORCE",
                (1.0, 0.25, 0.12),
            )
        except Exception as exc:
            self.status.setText(f"Could not preview force direction: {exc}")

    def _refresh_face_highlights(self):
        """Keep captured setup faces visible after FreeCAD selection changes."""
        if not self._setup_markers_visible:
            self.previews.clear("build_face")
            self.previews.clear("fixed_faces")
            self.previews.clear("load_faces")
            return
        try:
            if self.build_ref is not None:
                self.previews.show_faces(
                    "build_face",
                    [self._face_from_ref(self.build_ref)],
                    (0.20, 0.95, 0.35),
                    0.40,
                )
            else:
                self.previews.clear("build_face")
            if self.fixed_refs:
                self.previews.show_faces(
                    "fixed_faces",
                    [self._face_from_ref(ref) for ref in self.fixed_refs],
                    (0.18, 0.48, 1.00),
                    0.18,
                )
            else:
                self.previews.clear("fixed_faces")
            if self.load_refs:
                self.previews.show_faces(
                    "load_faces",
                    [self._face_from_ref(ref) for ref in self.load_refs],
                    (1.00, 0.30, 0.08),
                    0.18,
                )
            else:
                self.previews.clear("load_faces")
        except Exception as exc:
            self.status.setText(f"Could not refresh captured-face highlights: {exc}")

    def _set_setup_markers_visible(self, visible):
        self._setup_markers_visible = bool(visible)
        if self._setup_markers_visible:
            self._refresh_face_highlights()
            self._refresh_build_preview()
            self._refresh_force_preview()
            self._refresh_point_load_previews()
        else:
            self.previews.clear()

    def _toggle_setup_arrows(self, state):
        # Kept for compatibility with the hidden legacy results widgets. The
        # standalone results popup uses the same marker toggle.
        self._set_setup_markers_visible(bool(state))

    def _setup_payload(self):
        """Serialize the complete user-facing setup using stable object/subelement names."""
        model_name = getattr(self.model_obj, "Name", "") if self.model_obj is not None else ""
        return {
            "schema": 1,
            "model_name": model_name,
            "material": self.material_combo.currentText(),
            "material_model_index": int(self.material_model.currentIndex()),
            "layer_height_mm": float(self.layer_height.value()),
            "walls": int(self.walls.value()),
            "line_width_mm": float(self.line_width.value()),
            "infill_percent": int(self.infill.value()),
            "structure_model_enabled": bool(self.structure_model.isChecked()),
            "structure_sampling_index": int(self.structure_sampling.currentIndex()),
            "structure_workers_index": int(self.structure_workers.currentIndex()),
            "structure_timeout_s": float(self.structure_timeout.value()),
            "build_face": self.build_ref[1] if self.build_ref else "",
            "flip_build": bool(self.flip_build.isChecked()),
            "fixed_faces": [sub for _obj, sub in self.fixed_refs],
            "loaded_faces": [sub for _obj, sub in self.load_refs],
            "face_force_n": float(self.force.value()),
            "face_direction_index": int(self.direction.currentIndex()),
            "point_loads": json.loads(json.dumps(self.point_loads)),
            "mesh_quality": self.quality.currentText(),
            "target_sf": float(self.target_sf.value()),
            "advanced": bool(self.advanced_options.isChecked()),
        }

    def _save_setup(self):
        try:
            if self.model_obj is None:
                raise ValueError("Choose a model before saving a setup.")
            text, ok = QtWidgets.QInputDialog.getText(
                self, "Save PrintFEA Setup", "Setup name:", text=f"{self.model_obj.Label} setup"
            )
            if not ok:
                return
            obj = save_setup(App.ActiveDocument, str(text), self._setup_payload())
            self.status.setText(f"Saved setup: {obj.Label}")
            log_info(f"Saved setup {obj.Label}")
        except Exception as exc:
            log_exception("Could not save PrintFEA setup", exc)
            self._warn(str(exc))

    def _load_setup(self):
        try:
            doc = App.ActiveDocument
            setups = saved_setup_objects(doc)
            if not setups:
                self._warn("No saved PrintFEA setups exist in the active document yet.")
                return
            labels = [f"{getattr(o, 'SetupName', o.Label)} — {getattr(o, 'SavedAt', '')}" for o in setups]
            text, ok = QtWidgets.QInputDialog.getItem(self, "Load PrintFEA Setup", "Saved setup:", labels, 0, False)
            if not ok:
                return
            idx = labels.index(str(text))
            self._apply_setup_payload(load_payload(setups[idx]))
            self.status.setText(f"Loaded setup: {setups[idx].Label}")
            log_info(f"Loaded setup {setups[idx].Label}")
        except Exception as exc:
            log_exception("Could not load PrintFEA setup", exc)
            self._warn(str(exc))

    def _apply_setup_payload(self, data):
        doc = App.ActiveDocument
        model = doc.getObject(str(data.get("model_name") or "")) if doc is not None else None
        if model is None or not hasattr(model, "Shape"):
            raise ValueError(
                "The saved setup's model object is not present in this document. "
                "Load the original model or create a new setup for the current solid."
            )
        self.model_obj = model
        self.model_label.setText(f"{model.Label} ({model.Name})")

        material = str(data.get("material") or "")
        mi = self.material_combo.findText(material)
        if mi >= 0:
            self.material_combo.setCurrentIndex(mi)
        self.material_model.setCurrentIndex(max(0, min(self.material_model.count()-1, int(data.get("material_model_index", 0)))))
        self.layer_height.setValue(float(data.get("layer_height_mm", 0.20)))
        self.walls.setValue(int(data.get("walls", 4)))
        self.line_width.setValue(float(data.get("line_width_mm", 0.42)))
        self.infill.setValue(int(data.get("infill_percent", 40)))
        self.structure_model.setChecked(bool(data.get("structure_model_enabled", True)))
        self.structure_sampling.setCurrentIndex(max(0, min(self.structure_sampling.count()-1, int(data.get("structure_sampling_index", 0)))))
        self.structure_workers.setCurrentIndex(max(0, min(self.structure_workers.count()-1, int(data.get("structure_workers_index", 0)))))
        self.structure_timeout.setValue(float(data.get("structure_timeout_s", 12.0)))
        self.flip_build.setChecked(bool(data.get("flip_build", False)))
        self.force.setValue(float(data.get("face_force_n", 10.0)))
        self.direction.setCurrentIndex(max(0, min(self.direction.count()-1, int(data.get("face_direction_index", 1)))))
        q = str(data.get("mesh_quality") or "Normal")
        qi = self.quality.findText(q)
        if qi >= 0:
            self.quality.setCurrentIndex(qi)
        self.target_sf.setValue(float(data.get("target_sf", 2.0)))
        self.advanced_options.setChecked(bool(data.get("advanced", False)))

        def valid_ref(sub):
            if not sub:
                return False
            try:
                model.Shape.getElement(str(sub))
                return True
            except Exception:
                return False

        build_face = str(data.get("build_face") or "")
        self.build_ref = (model, build_face) if valid_ref(build_face) else None
        if self.build_ref:
            face = self._face_from_ref(self.build_ref)
            u0, u1, v0, v1 = face.ParameterRange
            normal = face.normalAt((u0+u1)*0.5, (v0+v1)*0.5)
            if normal.Length > 1e-12:
                normal.normalize()
                self.build_direction = -normal
            else:
                self.build_direction = None
            self.build_label.setText(f"Build plate face: {build_face}")
        else:
            self.build_direction = None
            self.build_label.setText("Build plate face: not selected")

        self.fixed_refs = [(model, str(sub)) for sub in data.get("fixed_faces", []) if valid_ref(sub)]
        self.load_refs = [(model, str(sub)) for sub in data.get("loaded_faces", []) if valid_ref(sub)]
        self.fixed_label.setText(f"{len(self.fixed_refs)} faces captured")
        self.load_label.setText(f"{len(self.load_refs)} faces captured")
        self.point_loads = list(data.get("point_loads") or [])
        self._refresh_point_load_list()
        self._mark_structure_stale()
        self._setup_markers_visible = True
        self._refresh_face_highlights()
        self._refresh_build_preview()
        self._refresh_force_preview()
        self._refresh_point_load_previews()
        self._update_material_summary()
        self._update_readiness()

    def _run(self):
        """Validate the setup, refresh structure asynchronously if needed, then solve."""
        try:
            if self.model_obj is None:
                raise ValueError("Choose a model first.")
            material_model = self._material_model_key()
            build_dir = self._effective_build_direction()
            if material_model == "layer_aware" and build_dir is None:
                raise ValueError(
                    "Layer-aware analysis needs a BUILD PLATE / BOTTOM face. Capture the face that sat on the printer bed, or choose the legacy isotropic material model."
                )
            if self.structure_model.isChecked() and self.walls.value() <= 0 and self.infill.value() <= 0:
                raise ValueError(
                    "Wall/infill modeling cannot represent a part with both 0 walls and 0% infill. Enter the actual slicer structure or disable the wall/infill model."
                )

            if self.structure_model.isChecked():
                signature = self._structure_signature()
                structure_ready = (
                    not self._structure_stale
                    and self._structure_estimate is not None
                    and self._structure_signature_cached == signature
                )
                if not structure_ready:
                    self.run_button.setEnabled(False)
                    self.status.setText("Calculating print structure before analysis…")
                    self._calculate_structure(for_run=True)
                    return
            else:
                self._calculate_structure(for_run=False)

            self._run_with_current_structure()
        except Exception as exc:
            log_exception("Analysis setup/run failed", exc)
            self._warn(str(exc))
            self._update_readiness()

    def _run_with_current_structure(self):
        try:
            if self.model_obj is None:
                raise ValueError("Choose a model first.")
            self.run_button.setEnabled(False)
            self.results_box.setVisible(False)
            self.colorbar_units.hide()
            try:
                Gui.activeDocument().resetEdit()
            except Exception:
                pass
            self.status.setText("Creating FEM analysis...")
            QtWidgets.QApplication.processEvents()
            material_name = self.material_combo.currentText()
            material = MATERIALS[material_name]
            material_model = self._material_model_key()
            build_dir = self._effective_build_direction()
            build_tuple = None if build_dir is None else (build_dir.x, build_dir.y, build_dir.z)
            structure_estimate = self._structure_estimate
            if self.structure_model.isChecked() and structure_estimate is None:
                raise RuntimeError("Print structure is not ready. Calculate it before solving.")
            print_settings = {
                "layer_height_mm": float(self.layer_height.value()),
                "walls": int(self.walls.value()),
                "line_width_mm": float(self.line_width.value()),
                "infill_percent": int(self.infill.value()),
                "structure_model_enabled": bool(self.structure_model.isChecked()),
                "build_face": self.build_ref[1] if self.build_ref else "",
                "build_direction": build_tuple,
            }
            objs = create_analysis(
                App.ActiveDocument,
                self.model_obj,
                material,
                self.fixed_refs,
                self.load_refs,
                self.force.value(),
                self._direction_vector(),
                self.quality.currentText(),
                point_loads=self.point_loads,
                material_model=material_model,
                build_direction=build_tuple,
                print_settings=print_settings,
                structure_estimate=structure_estimate,
            )
            self.status.setText("Meshing with Gmsh and running CalculiX...")
            QtWidgets.QApplication.processEvents()
            analysis = mesh_and_solve(objs)
            solve_info = objs.get("solve_info", {})
            self.status.setText("Calculating PrintFEA result summary...")
            QtWidgets.QApplication.processEvents()

            summary = summarize_results(
                analysis,
                objs.get("effective_material_profile", material),
                self.target_sf.value(),
                material_model=material_model,
                orthotropic=objs.get("orthotropic"),
                material_axes=objs.get("material_axes"),
                print_settings=print_settings,
                local_stress_output=bool((objs.get("input_patch") or {}).get("local_stress_output")),
            )
            summary["solve_info"] = solve_info
            summary["print_structure"] = objs.get("print_structure", {})
            summary["point_loads"] = list(objs.get("point_loads", []) or [])
            summary["mapped_point_loads"] = list(objs.get("mapped_point_loads", []) or [])

            ortho_props = objs.get("orthotropic")
            if summary.get("directional_available") and ortho_props:
                util_obj = create_fdm_utilization_filter(summary.get("pipeline"), ortho_props)
                summary["utilization_object"] = util_obj
                summary["utilization_heatmap_available"] = bool(util_obj)

            summary_obj = create_summary_object(
                App.ActiveDocument,
                analysis,
                summary,
                material_name,
            )
            objs["summary"] = summary_obj

            self.last_analysis = analysis
            self.last_result = summary.get("result_object")
            self.last_pipeline = summary.get("pipeline")
            self.last_summary = summary
            self.show_setup_arrows.setChecked(False)
            self._setup_markers_visible = False
            self.previews.clear()
            self._populate_results(summary)
            self.results_box.setVisible(False)

            default_quantity = "utilization" if summary.get("utilization_heatmap_available") else "stress"
            self._show_result_quantity(default_quantity)
            QtCore.QTimer.singleShot(150, lambda q=default_quantity: self.colorbar_units.show_quantity(q))
            show_results_dialog(App.ActiveDocument, summary_obj)
            self.colorbar_units.hide()
            self.status.setText(
                "Analysis complete. Showing the newest Pipeline_CCX_Results only; all other document layers were hidden. "
                "Results can be reopened anytime with View Recent PrintFEA Results."
            )
        except Exception as exc:
            log_exception("FEM solve/result processing failed", exc)
            self._warn(str(exc))
        finally:
            self._update_readiness()

    def _populate_results(self, summary):
        verdict = summary.get("verdict", "INCOMPLETE")
        symbol = {"PASS": "✓", "CAUTION": "⚠", "FAIL": "✕"}.get(verdict, "?")
        self.verdict_label.setText(f"{symbol} {verdict}")
        self.result_detail.setText(summary.get("verdict_detail", ""))

        self.result_values["max_displacement"].setText(
            self._fmt(summary.get("displacement_max_mm"), "mm", 4)
        )
        self.result_values["peak_stress"].setText(
            self._fmt(summary.get("stress_peak_mpa"), "MPa", 3)
        )
        self.result_values["p99_stress"].setText(
            self._fmt(summary.get("stress_p99_mpa"), "MPa", 3)
            + "  (used for screening safety factor)"
        )
        self.result_values["allowable"].setText(
            self._fmt(summary.get("allowable_mpa"), "MPa", 3)
        )
        self.result_values["safety_factor"].setText(
            self._fmt_sf(summary.get("estimated_safety_factor"))
            + f"  (target {summary.get('target_safety_factor', 0):.2f})"
        )
        self.result_values["peak_safety_factor"].setText(
            self._fmt_sf(summary.get("peak_safety_factor"))
        )

        pos = summary.get("peak_position")
        node = summary.get("peak_node")
        if pos is not None:
            hotspot = f"node {node or '?'} at ({pos.x:.2f}, {pos.y:.2f}, {pos.z:.2f}) mm"
        elif node:
            hotspot = f"node {node}"
        else:
            hotspot = "not available"
        self.result_values["hotspot"].setText(hotspot)

        solve_info = summary.get("solve_info", {}) or {}
        mesh_mode = solve_info.get("mesh_mode", "not reported")
        if solve_info.get("fallback_used"):
            mesh_mode += "  (automatic Jacobian fallback)"
        self.result_values["mesh_mode"].setText(mesh_mode)
        if solve_info.get("message"):
            self.result_detail.setText(self.result_detail.text() + " " + solve_info["message"])

        ratio = summary.get("concentration_ratio")
        if ratio is not None and math.isfinite(ratio) and ratio >= 1.5:
            self.result_detail.setText(
                self.result_detail.text()
                + f" The absolute peak is {ratio:.2f}× the 99th-percentile stress, which suggests a concentrated hotspot; inspect it and compare mesh qualities."
            )

    def _show_results_popup(self, summary):
        """Open a dedicated non-modal result summary instead of burying it below setup."""
        if self.results_dialog is not None:
            try:
                self.results_dialog.close()
            except RuntimeError:
                pass
            self.results_dialog = None

        dlg = QtWidgets.QDialog(Gui.getMainWindow())
        dlg.setWindowTitle("PrintFEA — Analysis Results")
        dlg.setModal(False)
        dlg.resize(610, 650)
        self.results_dialog = dlg

        outer = QtWidgets.QVBoxLayout(dlg)
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

        title = QtWidgets.QLabel("<h2>PrintFEA Analysis Results</h2>")
        layout.addWidget(title)

        verdict = QtWidgets.QLabel(self.verdict_label.text())
        vf = verdict.font()
        vf.setPointSize(max(vf.pointSize() + 7, 16))
        vf.setBold(True)
        verdict.setFont(vf)
        layout.addWidget(verdict)

        detail = QtWidgets.QLabel(self.result_detail.text())
        detail.setWordWrap(True)
        detail.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(detail)

        values_box = QtWidgets.QGroupBox("Result summary")
        grid = QtWidgets.QGridLayout(values_box)
        rows = [
            ("Maximum displacement", "max_displacement"),
            ("Peak von Mises stress", "peak_stress"),
            ("99th-percentile stress", "p99_stress"),
            ("Material allowable", "allowable"),
            ("Estimated safety factor", "safety_factor"),
            ("Peak-node safety factor", "peak_safety_factor"),
            ("Peak stress location", "hotspot"),
            ("Mesh / solver mode", "mesh_mode"),
        ]
        for row, (caption, key) in enumerate(rows):
            label = QtWidgets.QLabel(caption + ":")
            label.setAlignment(QtCore.Qt.AlignTop)
            value = QtWidgets.QLabel(self.result_values[key].text())
            value.setWordWrap(True)
            value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1)
        grid.setColumnStretch(1, 1)
        layout.addWidget(values_box)

        map_box = QtWidgets.QGroupBox("Result color map")
        map_layout = QtWidgets.QVBoxLayout(map_box)
        buttons = QtWidgets.QHBoxLayout()
        btn_stress = QtWidgets.QPushButton("Show STRESS")
        btn_disp = QtWidgets.QPushButton("Show DISPLACEMENT")
        buttons.addWidget(btn_stress)
        buttons.addWidget(btn_disp)
        map_layout.addLayout(buttons)

        popup_guide = QtWidgets.QLabel("")
        popup_guide.setWordWrap(True)
        popup_guide.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        map_layout.addWidget(popup_guide)

        setup_markers = QtWidgets.QCheckBox(
            "Show setup markers over result (build face/arrow, fixed faces, loaded faces, force arrow)"
        )
        setup_markers.setChecked(False)
        setup_markers.stateChanged.connect(
            lambda state: self._set_setup_markers_visible(bool(state))
        )
        map_layout.addWidget(setup_markers)
        layout.addWidget(map_box)

        caveat = QtWidgets.QLabel(
            "<b>Screening result only.</b> In v0.2 layer-aware mode the CalculiX solve is orthotropic and the verdict uses directional print-axis stresses. "
            "The generic profiles remain conservative screening approximations and do not explicitly model walls, sparse infill, raster roads, voids, defects, creep, fatigue, impact or temperature. "
            "Peak nodal values can also be mesh-sensitive near fixed edges, sharp corners and load boundaries."
        )
        caveat.setWordWrap(True)
        layout.addWidget(caveat)
        layout.addStretch(1)

        close_btn = QtWidgets.QPushButton("Close Results")
        close_btn.clicked.connect(dlg.close)
        outer.addWidget(close_btn)

        def switch_quantity(quantity):
            self._show_result_quantity(quantity)
            popup_guide.setText(self.color_guide.text())

        btn_stress.clicked.connect(lambda: switch_quantity("stress"))
        btn_disp.clicked.connect(lambda: switch_quantity("displacement"))

        # Stress is the default post-solve view.
        switch_quantity("stress")

        def destroyed(*_args):
            if self.results_dialog is dlg:
                self.results_dialog = None

        dlg.destroyed.connect(destroyed)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _show_result_quantity(self, quantity):
        if not self.last_summary:
            return

        target = self.last_summary.get("utilization_object") if quantity == "utilization" else self.last_pipeline
        if target is None:
            return
        isolate_and_focus_pipeline(target, open_task_panel=True)
        selected = configure_pipeline(target, quantity)
        self.colorbar_units.show_quantity(quantity)
        s = self.last_summary
        if quantity == "utilization":
            p99 = s.get("failure_index_p99")
            peak = s.get("failure_index_peak")
            target_sf = s.get("target_safety_factor")
            target_u = (1.0 / target_sf) if target_sf else None
            self.color_guide.setText(
                "<b>FDM directional failure utilization — dimensionless</b><br>"
                f"99th-percentile utilization = {self._fmt(p99, '', 3)}; peak = {self._fmt(peak, '', 3)}.<br>"
                f"1.000 = directional allowable; target SF {self._fmt(target_sf, '', 2)} corresponds to utilization {self._fmt(target_u, '', 3)}.<br><br>"
                "BLUE = lowest utilization and RED = highest utilization in this run. The native scale auto-ranges, so red alone does not mean failure."
            )
        elif quantity == "stress":
            lo = s.get("stress_min_mpa")
            hi = s.get("stress_peak_mpa")
            allowable = s.get("allowable_mpa")
            self.color_guide.setText(
                "<b>Von Mises stress — MPa</b><br>"
                f"BLUE = lowest stress ({self._fmt(lo, 'MPa', 3)})<br>"
                f"RED = highest stress ({self._fmt(hi, 'MPa', 3)})<br>"
                f"Selected material allowable = {self._fmt(allowable, 'MPa', 3)}<br><br>"
                "Important: red means <i>highest stress in this particular result</i>; it does not automatically mean failure. "
                "Use the MPa values and the safety-factor summary above to judge the load case. "
                "FreeCAD's native pipeline color bar is SI, so the numbers on the 3D scale are Pa. PrintFEA adds a unit caption next to the scale and reports stress here in MPa."
            )
        else:
            lo = s.get("displacement_min_mm")
            hi = s.get("displacement_max_mm")
            self.color_guide.setText(
                "<b>Displacement magnitude — mm</b><br>"
                f"BLUE = least movement ({self._fmt(lo, 'mm', 4)})<br>"
                f"RED = greatest movement ({self._fmt(hi, 'mm', 4)})<br><br>"
                "This map shows how far the part moves, not whether the material fails. "
                "FreeCAD's native pipeline color bar is SI, so the numbers on the 3D scale are meters (m). "
                "PrintFEA adds a unit caption next to the scale and converts the result to millimeters here (for example, 1.2e-3 m = 1.2 mm)."
            )

        if selected is None:
            self.status.setText(
                "PrintFEA could not switch the native pipeline field automatically on this FreeCAD build. "
                "Double-click Pipeline_CCX_Results and choose the matching field manually; the PrintFEA summary remains valid."
            )
        else:
            self.status.setText(f"Color map switched to {selected}.")

    @staticmethod
    def _fmt(value, unit, decimals):
        if value is None:
            return "—"
        try:
            f = float(value)
        except (TypeError, ValueError):
            return "—"
        if not math.isfinite(f):
            return f"∞ {unit}" if f > 0 else f"-∞ {unit}"
        return f"{f:.{decimals}f} {unit}"

    @staticmethod
    def _fmt_sf(value):
        if value is None:
            return "—"
        try:
            f = float(value)
        except (TypeError, ValueError):
            return "—"
        if math.isinf(f):
            return "∞"
        if not math.isfinite(f):
            return "—"
        return f"{f:.2f}"

    def closeEvent(self, event):
        # A headless worker may still be chewing on a difficult OCCT offset.
        # Kill it when the setup wizard closes; solved result dialogs remain independent.
        if self._structure_process is not None:
            self._cancel_structure_worker(silent=True)
        # Setup markers belong to the wizard, but solved results do not. The
        # independent result dialog intentionally survives closing this window.
        if self.point_picker is not None:
            try:
                self.point_picker.stop(notify=False)
            except Exception:
                pass
            self.point_picker = None
        self.previews.clear()
        self.colorbar_units.hide()
        super().closeEvent(event)

    def _warn(self, text):
        self.status.setText(text)
        QtWidgets.QMessageBox.warning(self, "PrintFEA", text)
