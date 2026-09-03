"""PrintFEA in-app help.

The main wizard intentionally stays concise. Detailed explanations live here so
users can learn concepts without carrying paragraphs of text through every run.
"""

import FreeCADGui as Gui
from diagnostics import diagnostics_dir, log_path
from PySide import QtCore, QtGui

try:
    from PySide import QtWidgets
except ImportError:
    QtWidgets = QtGui


HELP_SECTIONS = [
    (
        "Quick Start",
        """
        <h2>Quick Start</h2>
        <ol>
          <li>Select a solid STEP/FreeCAD model.</li>
          <li>Choose filament and enter the slicer settings you intend to use.</li>
          <li>Select the face that sits on the printer bed and capture it as <b>BUILD PLATE / BOTTOM</b>.</li>
          <li>Select mounting faces and capture them as <b>FIXED</b>.</li>
          <li>Apply a distributed face load, add one or more clicked point forces, or use both.</li>
          <li>Choose mesh quality and a target safety factor, then click <b>RUN ANALYSIS</b>.</li>
        </ol>
        <p>Green = build plate/orientation, blue = fixed faces, orange = loaded faces.</p>
        <p>Click any numbered section header to collapse or expand it. Hover over a field or its label for a quick explanation; use Help when you want the longer version.</p>
        """,
    ),
    (
        "Print Settings",
        """
        <h2>Print Settings</h2>
        <p><b>Filament</b> selects a conservative generic FDM material profile.</p>
        <p><b>Layer height</b> is used by the layer-sliced shell/core estimator and is stored with the run.</p>
        <p><b>Walls</b> is the number of perimeter extrusion lines. Thin structural parts can become mostly perimeter even at modest infill.</p>
        <p><b>Wall line width</b> is the slicer's extrusion width for those perimeter lines.</p>
        <p><b>Infill</b> applies to the remaining internal core after perimeters are accounted for.</p>
        <p>The shell/core model is a screening approximation. It does not reproduce every extrusion path, infill cell, top/bottom skin, seam, void, or defect.</p>
        """,
    ),
    (
        "Print Orientation",
        """
        <h2>Print Orientation</h2>
        <p>Select the flat face that physically touches the printer bed. PrintFEA uses the outward normal of that face to establish the layer-stack direction.</p>
        <p>The green <b>BUILD UP</b> arrow should point in the direction the printer builds the part. Use <b>Flip build direction</b> if it points the wrong way.</p>
        <p>In layer-aware mode, material axes 1/2 lie in the printed layer plane and axis 3 follows BUILD UP. This changes stiffness and failure screening.</p>
        """,
    ),
    (
        "Fixed Faces",
        """
        <h2>Fixed / Mounting Faces</h2>
        <p>Fixed faces represent surfaces that cannot move in the simulated load case: clamped faces, firmly captured mounting faces, or an intentionally simplified support.</p>
        <p>Captured fixed faces stay highlighted <b>blue</b>. Select a blue face and use <b>Remove selected</b> to correct a mistake.</p>
        <p>Over-constraining a part can make the model unrealistically stiff and can create local stress peaks near the fixed boundary.</p>
        """,
    ),
    (
        "Loads",
        """
        <h2>Loads</h2>
        <p>Loaded faces are the surfaces over which the entered force is distributed. They stay highlighted <b>orange</b>.</p>
        <p>Force direction uses FreeCAD's global X/Y/Z axes. The red arrow is the easiest way to verify the direction before solving.</p>
        <p>A face load is a simplification. Real pins, bolts, bearings, remote masses, moments, impacts, and contact can require more advanced boundary conditions.</p>
        <p><b>Clicked loads:</b> set the magnitude/direction and contact diameter, click <b>Click model to add point force</b>, then click anywhere on the CAD surface. Each finite contact patch is previewed as a translucent diameter disk and the entered value remains the TOTAL force distributed across nearby surface mesh nodes. Select an existing load in the list to edit its force, direction, or diameter and click <b>Update selected</b>. Advanced users can still choose an ideal one-node point load.</p>
        <p>Contact-patch loads are recommended for local strength screening because the entered force is distributed over a finite surface footprint. If the patch contains too few mesh nodes, refine the FEM mesh. The optional ideal mathematical point load puts the full force on one node and creates a local stress singularity; use it mainly for global displacement/load-path studies.</p>
        """,
    ),
    (
        "Mesh Quality",
        """
        <h2>Mesh Quality</h2>
        <p><b>Fast</b> is useful for quick iteration. <b>Normal</b> is the recommended default. <b>Fine</b> is useful for checking whether representative results are converging.</p>
        <p>Peak stress at a single node can increase as the mesh gets finer near sharp corners or constraints. PrintFEA therefore emphasizes a representative 99th-percentile utilization while still reporting the worst local hotspot.</p>
        """,
    ),
    (
        "Safety Factor",
        """
        <h2>Safety Factor</h2>
        <p>A target safety factor of <b>2.0</b> means the representative utilization should remain at or below 50% of the selected directional allowable.</p>
        <p>Utilization and safety factor are approximately inverse: utilization 0.50 corresponds to SF 2.0; utilization 1.00 corresponds to SF 1.0.</p>
        <p>PASS means the representative screening result meets the selected target. CAUTION means it is close to the limit. FAIL means the representative result does not meet the selected margin.</p>
        """,
    ),
    (
        "Understanding Results",
        """
        <h2>Understanding Results</h2>
        <p><b>FDM SAFETY</b> is the recommended result view. It colors the model by the governing directional utilization relative to the FDM allowables.</p>
        <p><b>STRESS</b> shows conventional von Mises stress. It is useful for locating highly stressed regions but is not the layer-aware PASS/FAIL criterion.</p>
        <p><b>MOVEMENT</b> shows displacement magnitude.</p>
        <p>The native FreeCAD color scale auto-ranges. Red means the highest value in that particular run; it does <b>not</b> automatically mean failure.</p>
        <p>For <b>CAUTION</b> or <b>FAIL</b>, PrintFEA also highlights a small cloud of nearby high-utilization FEM nodes as the <b>likely failure region</b> and labels the governing mode in plain language (for example Layer separation or Inter-layer shear). Treat this as an approximate hotspot region, not an exact crack path.</p>
        <p>For <b>CAUTION</b> and <b>FAIL</b> runs, PrintFEA automatically places a 3D <b>LIKELY FAILURE</b> marker at the representative high-utilization region used by the screening result. The marker identifies a FEM hotspot, not an exact crack path or fracture surface. Use mesh refinement to verify that the hotspot remains in the same area.</p>
        """,
    ),
    (
        "Walls & Infill Model",
        """
        <h2>Walls & Infill Model</h2>
        <p>PrintFEA samples cross-sections perpendicular to BUILD UP and offsets each slice inward one wall line at a time. The area outside the remaining core is treated as dense perimeter shell.</p>
        <p>The remaining core is homogenized using the entered infill percentage. This lets thin ribs, holes, and opposing walls naturally become shell-dominated.</p>
        <p>If a complex slice cannot complete every requested inward offset, PrintFEA conservatively stops crediting additional walls on that slice instead of pretending it became solid.</p>
        """,
    ),
    (
        "Saved Setups & Run Comparison",
        """
        <h2>Saved Setups & Run Comparison</h2>
        <p><b>Save Setup</b> stores the current model reference, print settings, build face, fixed/loaded faces, clicked loads, mesh quality, and target safety factor inside the active FreeCAD document.</p>
        <p><b>Load Setup</b> restores one of those saved configurations. Saved face references depend on the original model topology; if the CAD model changes and face numbers are regenerated, capture those faces again and save a new setup.</p>
        <p>The Results window includes <b>Compare</b> for side-by-side safety factor, movement, utilization, effective material, print settings, and failure-mode comparisons between saved runs.</p>
        """,
    ),
    (
        "Known Limitations",
        """
        <h2>Known Limitations</h2>
        <p>PrintFEA is a design-screening tool, not engineering certification.</p>
        <ul>
          <li>Generic materials are conservative approximations, not coupon data for your exact printer/filament/process.</li>
          <li>Explicit infill pattern/cells, exact raster paths, top/bottom skin stacks, seams, voids, supports, and manufacturing defects are not explicitly meshed.</li>
          <li>Creep, fatigue, impact, temperature, moisture, and aging are not modeled.</li>
          <li>Simple fixed/load boundary conditions may not represent real bolts, pins, bearings, contact, or remote moments.</li>
          <li>Local nodal peaks can be mesh-sensitive near constraints and sharp geometry.</li>
        </ul>
        """,
    ),
    (
        "Troubleshooting",
        """
        <h2>Troubleshooting</h2>
        <p>If CalculiX fails, try Normal/Fine mesh, inspect the model for invalid/sliver geometry, and confirm at least one fixed and loaded face is captured.</p>
        <p>If the print-structure calculation is slow, edit all print settings first and calculate once. PrintFEA intentionally does not run the layer-sliced estimator live. In v0.4.1 and later this calculation runs in a separate <code>FreeCADCmd</code> worker process, so difficult OCCT slices should not freeze the main FreeCAD window. You can cancel the worker from the Print Settings section.</p>
        <p>If a BUILD UP or FORCE arrow is hard to see, enable Advanced options and increase its arrow-size percentage.</p>
        <p>PrintFEA writes runtime diagnostics to <code>printfea.log</code> under the FreeCAD user-data <code>PrintFEA</code> folder. Use the <b>Open Diagnostics Folder</b> button at the bottom of this Help window when reporting a reproducible failure.</p>
        """,
    ),
]

_active_help_dialog = None


class HelpDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, section=None):
        super().__init__(parent)
        self.setWindowTitle("PrintFEA — Help")
        self.setModal(False)
        self.resize(820, 650)
        self._build_ui()
        self.select_section(section or "Quick Start")

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QLabel("<h2>PrintFEA Help</h2><p>Quick explanations for the guided FDM analysis workflow.</p>")
        root.addWidget(header)

        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search help…")
        self.search.textChanged.connect(self._filter_sections)
        root.addWidget(self.search)

        split = QtWidgets.QSplitter()
        try:
            split.setOrientation(QtCore.Qt.Orientation.Horizontal)
        except AttributeError:
            split.setOrientation(QtCore.Qt.Horizontal)
        root.addWidget(split, 1)

        self.sections = QtWidgets.QListWidget()
        self.sections.setMinimumWidth(210)
        for title, _html in HELP_SECTIONS:
            self.sections.addItem(title)
        self.sections.currentTextChanged.connect(self._show_section)
        split.addWidget(self.sections)

        self.browser = QtWidgets.QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        split.addWidget(self.browser)
        split.setStretchFactor(1, 1)

        actions = QtWidgets.QHBoxLayout()
        diag_btn = QtWidgets.QPushButton("Open Diagnostics Folder")
        diag_btn.setToolTip("Open the folder containing PrintFEA's runtime log file.")
        diag_btn.clicked.connect(self._open_diagnostics)
        actions.addWidget(diag_btn)
        actions.addStretch(1)
        close_btn = QtWidgets.QPushButton("Close Help")
        close_btn.clicked.connect(self.close)
        actions.addWidget(close_btn)
        root.addLayout(actions)

    def _open_diagnostics(self):
        path = diagnostics_dir()
        url = QtCore.QUrl.fromLocalFile(path)
        QtGui.QDesktopServices.openUrl(url)

    def _filter_sections(self, text):
        needle = (text or "").strip().lower()
        for i in range(self.sections.count()):
            item = self.sections.item(i)
            title = item.text()
            html = next((body for name, body in HELP_SECTIONS if name == title), "")
            item.setHidden(bool(needle and needle not in (title + " " + html).lower()))

    def _show_section(self, title):
        for name, html in HELP_SECTIONS:
            if name == title:
                self.browser.setHtml(html)
                return

    def select_section(self, title):
        for i in range(self.sections.count()):
            item = self.sections.item(i)
            if item.text() == title:
                item.setHidden(False)
                self.sections.setCurrentItem(item)
                return
        if self.sections.count():
            self.sections.setCurrentRow(0)


def show_help_dialog(section=None):
    global _active_help_dialog
    if _active_help_dialog is not None:
        try:
            if _active_help_dialog.isVisible():
                if section:
                    _active_help_dialog.select_section(section)
                _active_help_dialog.raise_()
                _active_help_dialog.activateWindow()
                return _active_help_dialog
        except RuntimeError:
            _active_help_dialog = None

    _active_help_dialog = HelpDialog(Gui.getMainWindow(), section=section)
    _active_help_dialog.setModal(False)
    try:
        delete_on_close = QtCore.Qt.WidgetAttribute.WA_DeleteOnClose
    except AttributeError:
        delete_on_close = QtCore.Qt.WA_DeleteOnClose
    _active_help_dialog.setAttribute(delete_on_close, True)
    _active_help_dialog.destroyed.connect(_help_destroyed)
    _active_help_dialog.show()
    _active_help_dialog.raise_()
    _active_help_dialog.activateWindow()
    return _active_help_dialog


def _help_destroyed(*_args):
    global _active_help_dialog
    _active_help_dialog = None
