"""3D preview helpers for PrintFEA.

Preview arrows and captured-face highlights are inserted directly into
FreeCAD's Coin scene graph. They do not create document objects, so the setup
stays visually informative without cluttering the model tree.
"""

import FreeCAD as App
import FreeCADGui as Gui
from pivy import coin


class PreviewArrows:
    def __init__(self):
        self._nodes = {}

    def clear(self, key=None):
        keys = [key] if key is not None else list(self._nodes.keys())
        for item in keys:
            node = self._nodes.pop(item, None)
            if node is None:
                continue
            try:
                root = Gui.ActiveDocument.ActiveView.getSceneGraph()
                root.removeChild(node)
            except Exception:
                pass

    def show_arrow(self, key, start, direction, length, label, rgb):
        """Show or replace a scene-graph arrow."""
        self.clear(key)
        if Gui.ActiveDocument is None:
            return

        d = App.Vector(direction.x, direction.y, direction.z)
        if d.Length <= 1e-12:
            return
        d = d / d.Length
        start = App.Vector(start.x, start.y, start.z)
        length = max(float(length), 1.0)

        end = start + d * length
        cone_h = max(length * 0.20, 0.8)
        cone_r = max(length * 0.055, 0.25)
        shaft_end = end - d * (cone_h * 0.78)

        sep = coin.SoSeparator()

        # Preview geometry is visual-only. Do not let arrows steal mouse picks
        # from the underlying CAD model.
        try:
            pick_style = coin.SoPickStyle()
            pick_style.style = coin.SoPickStyle.UNPICKABLE
            sep.addChild(pick_style)
        except Exception:
            pass

        material = coin.SoMaterial()
        material.diffuseColor.setValue(*rgb)
        material.emissiveColor.setValue(*(min(1.0, c * 0.28) for c in rgb))
        sep.addChild(material)

        style = coin.SoDrawStyle()
        style.lineWidth = 4.0
        sep.addChild(style)

        coords = coin.SoCoordinate3()
        coords.point.setValues(
            0,
            2,
            [
                coin.SbVec3f(start.x, start.y, start.z),
                coin.SbVec3f(shaft_end.x, shaft_end.y, shaft_end.z),
            ],
        )
        sep.addChild(coords)

        line = coin.SoLineSet()
        line.numVertices.setValues(0, 1, [2])
        sep.addChild(line)

        cone_sep = coin.SoSeparator()
        cone_xf = coin.SoTransform()
        cone_center = end - d * (cone_h * 0.5)
        cone_xf.translation.setValue(cone_center.x, cone_center.y, cone_center.z)
        cone_xf.rotation.setValue(
            coin.SbRotation(
                coin.SbVec3f(0.0, 1.0, 0.0),
                coin.SbVec3f(d.x, d.y, d.z),
            )
        )
        cone_sep.addChild(cone_xf)
        cone_node = coin.SoCone()
        cone_node.bottomRadius = cone_r
        cone_node.height = cone_h
        cone_sep.addChild(cone_node)
        sep.addChild(cone_sep)

        text_sep = coin.SoSeparator()
        text_xf = coin.SoTranslation()
        text_pos = end + d * (cone_h * 0.25)
        text_xf.translation.setValue(text_pos.x, text_pos.y, text_pos.z)
        text_sep.addChild(text_xf)
        font = coin.SoFont()
        font.size = 14
        text_sep.addChild(font)
        text = coin.SoText2()
        text.string.setValue(label)
        text_sep.addChild(text)
        sep.addChild(text_sep)

        root = Gui.ActiveDocument.ActiveView.getSceneGraph()
        root.addChild(sep)
        self._nodes[key] = sep


    def show_contact_disk(self, key, position, normal, diameter, label="", rgb=(1.0, 0.25, 0.12), transparency=0.55):
        """Show a thin translucent disk representing a finite contact footprint.

        The disk is oriented perpendicular to *normal*. For clicked loads we use
        the load direction as a clear visual approximation of the contact plane;
        it is a preview only and does not alter the FEM mapping.
        """
        self.clear(key)
        if Gui.ActiveDocument is None:
            return
        p = App.Vector(position.x, position.y, position.z)
        n = App.Vector(normal.x, normal.y, normal.z)
        if n.Length <= 1e-12:
            n = App.Vector(0, 0, 1)
        n = n / n.Length
        d = max(float(diameter), 0.2)
        radius = d * 0.5
        thickness = max(min(d * 0.035, 0.6), 0.12)

        try:
            root_sep = coin.SoAnnotation()
        except Exception:
            root_sep = coin.SoSeparator()
        sep = coin.SoSeparator()
        try:
            pick = coin.SoPickStyle(); pick.style = coin.SoPickStyle.UNPICKABLE; sep.addChild(pick)
        except Exception:
            pass
        mat = coin.SoMaterial()
        mat.diffuseColor.setValue(*rgb)
        mat.emissiveColor.setValue(*(min(1.0, c * 0.28) for c in rgb))
        mat.transparency.setValue(max(0.0, min(float(transparency), 0.80)))
        sep.addChild(mat)
        xf = coin.SoTransform()
        xf.translation.setValue(p.x, p.y, p.z)
        xf.rotation.setValue(coin.SbRotation(coin.SbVec3f(0.0, 1.0, 0.0), coin.SbVec3f(n.x, n.y, n.z)))
        sep.addChild(xf)
        cyl = coin.SoCylinder(); cyl.radius = radius; cyl.height = thickness; sep.addChild(cyl)
        if label:
            txtsep = coin.SoSeparator()
            tr = coin.SoTranslation(); tr.translation.setValue(radius * 1.10, thickness, radius * 0.25); txtsep.addChild(tr)
            font = coin.SoFont(); font.size = 14; txtsep.addChild(font)
            text = coin.SoText2(); text.string.setValue(str(label)); txtsep.addChild(text)
            sep.addChild(txtsep)
        root_sep.addChild(sep)
        Gui.ActiveDocument.ActiveView.getSceneGraph().addChild(root_sep)
        self._nodes[key] = root_sep

    def show_region(self, key, positions, radius, label, rgb=(1.0, 0.15, 0.08), transparency=0.28, center=None):
        """Highlight a small cloud of FEM nodes as a likely failure region."""
        self.clear(key)
        pts = [App.Vector(p.x, p.y, p.z) for p in (positions or [])]
        if Gui.ActiveDocument is None or not pts:
            return
        r = max(float(radius), 0.18)
        try:
            root_sep = coin.SoAnnotation()
        except Exception:
            root_sep = coin.SoSeparator()
        sep = coin.SoSeparator()
        try:
            pick = coin.SoPickStyle(); pick.style = coin.SoPickStyle.UNPICKABLE; sep.addChild(pick)
        except Exception:
            pass
        mat = coin.SoMaterial()
        mat.diffuseColor.setValue(*rgb)
        mat.emissiveColor.setValue(*(min(1.0, c * 0.48) for c in rgb))
        mat.transparency.setValue(max(0.0, min(float(transparency), 0.78)))
        sep.addChild(mat)
        for p in pts:
            one = coin.SoSeparator()
            tr = coin.SoTranslation(); tr.translation.setValue(p.x, p.y, p.z); one.addChild(tr)
            sphere = coin.SoSphere(); sphere.radius = r; one.addChild(sphere)
            sep.addChild(one)
        c = center if center is not None else App.Vector(
            sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts), sum(p.z for p in pts) / len(pts)
        )
        c = App.Vector(c.x, c.y, c.z)
        # A larger crosshair anchors the region and carries the plain-language label.
        anchor = coin.SoSeparator()
        tr = coin.SoTranslation(); tr.translation.setValue(c.x, c.y, c.z); anchor.addChild(tr)
        style = coin.SoDrawStyle(); style.lineWidth = 4.0; anchor.addChild(style)
        rr = r * 3.0
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, 6, [
            coin.SbVec3f(-rr,0,0), coin.SbVec3f(rr,0,0), coin.SbVec3f(0,-rr,0),
            coin.SbVec3f(0,rr,0), coin.SbVec3f(0,0,-rr), coin.SbVec3f(0,0,rr),
        ])
        anchor.addChild(coords)
        lines = coin.SoLineSet(); lines.numVertices.setValues(0,3,[2,2,2]); anchor.addChild(lines)
        if label:
            ts = coin.SoSeparator(); tt = coin.SoTranslation(); tt.translation.setValue(rr*0.8, rr*0.8, rr*0.8); ts.addChild(tt)
            font=coin.SoFont(); font.size=15; ts.addChild(font)
            text=coin.SoText2(); text.string.setValue(str(label)); ts.addChild(text); anchor.addChild(ts)
        sep.addChild(anchor)
        root_sep.addChild(sep)
        Gui.ActiveDocument.ActiveView.getSceneGraph().addChild(root_sep)
        self._nodes[key] = root_sep

    def show_faces(self, key, faces, rgb, transparency=0.22):
        """Overlay captured faces with a persistent translucent highlight.

        FreeCAD's normal selection highlight disappears as soon as the user
        starts selecting the next set of faces. This overlay keeps FIXED and
        LOADED faces visually distinct without creating helper document
        objects.
        """
        self.clear(key)
        if Gui.ActiveDocument is None or not faces:
            return

        root_sep = coin.SoSeparator()

        # Persistent face overlays must remain click-through so the user can
        # re-select the actual CAD face beneath the highlight and remove it
        # from FIXED/LOADED assignments.
        try:
            pick_style = coin.SoPickStyle()
            pick_style.style = coin.SoPickStyle.UNPICKABLE
            root_sep.addChild(pick_style)
        except Exception:
            pass

        material = coin.SoMaterial()
        material.diffuseColor.setValue(*rgb)
        material.emissiveColor.setValue(*(min(1.0, c * 0.18) for c in rgb))
        material.transparency.setValue(max(0.0, min(float(transparency), 0.85)))
        root_sep.addChild(material)

        # Nudge the overlay slightly toward the camera in the depth buffer so
        # it remains visible on top of the model without noticeably changing
        # the geometry.
        try:
            polygon_offset = coin.SoPolygonOffset()
            polygon_offset.factor.setValue(-1.0)
            polygon_offset.units.setValue(-1.0)
            root_sep.addChild(polygon_offset)
        except Exception:
            pass

        for face in faces:
            try:
                # A small deflection gives a smooth-enough overlay even on
                # cylindrical mounting holes while staying cheap to redraw.
                diag = max(face.BoundBox.DiagonalLength, 1.0)
                vertices, triangles = face.tessellate(max(diag * 0.01, 0.05))
                if not vertices or not triangles:
                    continue

                face_sep = coin.SoSeparator()
                coords = coin.SoCoordinate3()
                coords.point.setValues(
                    0,
                    len(vertices),
                    [coin.SbVec3f(v.x, v.y, v.z) for v in vertices],
                )
                face_sep.addChild(coords)

                indices = []
                for tri in triangles:
                    indices.extend([int(tri[0]), int(tri[1]), int(tri[2]), -1])
                indexed = coin.SoIndexedFaceSet()
                indexed.coordIndex.setValues(0, len(indices), indices)
                face_sep.addChild(indexed)
                root_sep.addChild(face_sep)
            except Exception:
                continue

        root = Gui.ActiveDocument.ActiveView.getSceneGraph()
        root.addChild(root_sep)
        self._nodes[key] = root_sep

    def show_marker(self, key, position, radius, label, rgb=(1.0, 0.15, 0.10), transparency=0.10):
        """Show a high-visibility, click-through 3D hotspot marker.

        The marker is inserted as a Coin annotation so it remains visible over
        the post-processing surface.  It is visual only and does not create a
        document object or participate in the FEM solve.
        """
        self.clear(key)
        if Gui.ActiveDocument is None:
            return

        p = App.Vector(position.x, position.y, position.z)
        r = max(float(radius), 0.25)

        # SoAnnotation renders after normal scene geometry, making the marker
        # visible even when the governing node sits just inside the displayed
        # post-processing surface.
        try:
            annotation = coin.SoAnnotation()
            root_sep = annotation
        except Exception:
            annotation = None
            root_sep = coin.SoSeparator()

        sep = coin.SoSeparator()
        try:
            pick_style = coin.SoPickStyle()
            pick_style.style = coin.SoPickStyle.UNPICKABLE
            sep.addChild(pick_style)
        except Exception:
            pass

        mat = coin.SoMaterial()
        mat.diffuseColor.setValue(*rgb)
        mat.emissiveColor.setValue(*(min(1.0, c * 0.55) for c in rgb))
        mat.transparency.setValue(max(0.0, min(float(transparency), 0.75)))
        sep.addChild(mat)

        xf = coin.SoTranslation()
        xf.translation.setValue(p.x, p.y, p.z)
        sep.addChild(xf)

        sphere = coin.SoSphere()
        sphere.radius = r
        sep.addChild(sphere)

        # Crosshair makes the location legible even when the translucent sphere
        # is partially obscured by a dense result mesh.
        style = coin.SoDrawStyle()
        style.lineWidth = 4.0
        sep.addChild(style)
        coords = coin.SoCoordinate3()
        s = r * 1.8
        coords.point.setValues(0, 6, [
            coin.SbVec3f(-s, 0, 0), coin.SbVec3f(s, 0, 0),
            coin.SbVec3f(0, -s, 0), coin.SbVec3f(0, s, 0),
            coin.SbVec3f(0, 0, -s), coin.SbVec3f(0, 0, s),
        ])
        sep.addChild(coords)
        lines = coin.SoLineSet()
        lines.numVertices.setValues(0, 3, [2, 2, 2])
        sep.addChild(lines)

        if label:
            text_sep = coin.SoSeparator()
            text_xf = coin.SoTranslation()
            text_xf.translation.setValue(r * 1.6, r * 1.6, r * 1.6)
            text_sep.addChild(text_xf)
            font = coin.SoFont()
            font.size = 15
            text_sep.addChild(font)
            text = coin.SoText2()
            text.string.setValue(str(label))
            text_sep.addChild(text)
            sep.addChild(text_sep)

        root_sep.addChild(sep)
        root = Gui.ActiveDocument.ActiveView.getSceneGraph()
        root.addChild(root_sep)
        self._nodes[key] = root_sep
