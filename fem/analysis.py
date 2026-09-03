import os
import re
import math
import FreeCAD as App
import ObjectsFem

from materials.library import (
    layer_aware_properties,
    estimate_print_structure,
    apply_print_structure_to_material,
)


def validate_model(obj):
    if obj is None or not hasattr(obj, "Shape"):
        raise ValueError("Select a Part/PartDesign solid before starting the analysis.")
    shape = obj.Shape
    if shape.isNull():
        raise ValueError("Selected object has no usable shape.")
    if len(shape.Solids) != 1:
        raise ValueError("PrintFEA v0.2 currently requires exactly one solid body.")
    if not shape.isValid():
        raise ValueError("Selected solid is not valid. Repair the geometry before analysis.")


def _make_ccx_solver(doc, name):
    """Create the CalculiX CcxTools solver across FreeCAD API spellings."""
    maker = getattr(ObjectsFem, "makeSolverCalculiXCcxTools", None)
    if maker is None:
        maker = getattr(ObjectsFem, "makeSolverCalculixCcxTools", None)
    if maker is None:
        raise RuntimeError(
            "This FreeCAD build does not expose a CalculiX CcxTools solver creator. "
            "Open the FEM workbench once and verify CalculiX is installed."
        )
    return maker(doc, name)


def _set_if_present(obj, prop, value):
    if hasattr(obj, prop):
        setattr(obj, prop, value)


def _normalized_vector(values, label="vector"):
    v = App.Vector(*values)
    if v.Length <= 1e-12:
        raise ValueError(f"{label} cannot be a zero vector.")
    v.normalize()
    return v


def _material_axes(build_direction):
    """Return a right-handed local material basis.

    Axis 3 is the layer-stack/build direction. Axes 1 and 2 lie in the print
    plane. Generic PrintFEA profiles are in-plane isotropic, so the exact
    heading of axes 1/2 does not affect their constitutive response.
    """
    e3 = _normalized_vector(build_direction, "Build direction")
    base = App.Vector(1, 0, 0)
    if abs(e3.dot(base)) > 0.90:
        base = App.Vector(0, 1, 0)
    e1 = base - e3 * e3.dot(base)
    if e1.Length <= 1e-12:
        base = App.Vector(0, 0, 1)
        e1 = base - e3 * e3.dot(base)
    e1.normalize()
    e2 = e3.cross(e1)
    e2.normalize()
    return e1, e2, e3


def create_analysis(
    doc,
    model_obj,
    material,
    fixed_refs,
    load_refs,
    force_n,
    direction,
    mesh_quality,
    material_model="layer_aware",
    build_direction=None,
    print_settings=None,
    structure_estimate=None,
    point_loads=None,
):
    validate_model(model_obj)
    if not fixed_refs:
        raise ValueError("No fixed/mounting faces selected.")
    point_loads = list(point_loads or [])
    if not load_refs and not point_loads:
        raise ValueError("No loads defined. Capture a loaded face or add at least one point force.")
    if material_model == "layer_aware" and not build_direction:
        raise ValueError(
            "Layer-aware analysis requires a build-plate face so PrintFEA knows the layer-stack direction."
        )

    settings = dict(print_settings or {})
    structure = dict(structure_estimate) if structure_estimate is not None else estimate_print_structure(
        model_obj,
        build_direction,
        settings.get("walls", 0),
        settings.get("line_width_mm", 0.42),
        settings.get("infill_percent", 100),
        enabled=bool(settings.get("structure_model_enabled", True)),
        layer_height_mm=settings.get("layer_height_mm", 0.20),
    )
    effective_material = apply_print_structure_to_material(material, structure)

    created = []
    try:
        analysis = ObjectsFem.makeAnalysis(doc, "PrintFEA_Analysis")
        created.append(analysis)

        solver = _make_ccx_solver(doc, "PrintFEA_CalculiX")
        created.append(solver)
        _set_if_present(solver, "GeometricalNonlinearity", "linear")
        _set_if_present(solver, "ThermoMechSteadyState", True)
        _set_if_present(solver, "MatrixSolverType", "default")
        _set_if_present(solver, "IterationsControlParameterTimeUse", False)
        analysis.addObject(solver)

        mat_obj = ObjectsFem.makeMaterialSolid(doc, "PrintFEA_Material")
        created.append(mat_obj)
        mat = mat_obj.Material
        mat["Name"] = effective_material["name"]
        # FreeCAD's stock CalculiX writer is isotropic. In layer-aware mode this
        # block is intentionally used as a placeholder and is upgraded to
        # ENGINEERING CONSTANTS immediately after the .inp is written.
        mat["YoungsModulus"] = f'{effective_material["youngs_modulus_mpa"]} MPa'
        mat["PoissonRatio"] = str(effective_material["poisson_ratio"])
        mat["Density"] = f'{effective_material["density_kg_m3"]} kg/m^3'
        mat_obj.Material = mat
        analysis.addObject(mat_obj)

        fixed = ObjectsFem.makeConstraintFixed(doc, "PrintFEA_Fixed")
        created.append(fixed)
        fixed.References = fixed_refs
        analysis.addObject(fixed)

        force = None
        direction_helper = None
        if load_refs:
            force = ObjectsFem.makeConstraintForce(doc, "PrintFEA_Force")
            created.append(force)
            force.References = load_refs
            force.Force = f"{float(force_n)} N"

            import Part

            direction_helper = doc.addObject("Part::Feature", "PrintFEA_ForceDirection")
            created.append(direction_helper)
            direction_helper.Label = "PrintFEA Force Direction (helper)"
            d = _normalized_vector(direction, "Force direction")
            direction_helper.Shape = Part.makeLine(App.Vector(0, 0, 0), d)
            direction_helper.ViewObject.Visibility = False
            force.Direction = (direction_helper, ["Edge1"])
            force.Reversed = False
            analysis.addObject(force)

        mesh = ObjectsFem.makeMeshGmsh(doc, "PrintFEA_Mesh")
        created.append(mesh)
        mesh.Shape = model_obj
        mesh.ElementOrder = "2nd"
        mesh.SecondOrderLinear = False
        if hasattr(mesh, "HighOrderOptimize"):
            try:
                mesh.HighOrderOptimize = "Optimization"
            except Exception:
                pass
        mesh.CharacteristicLengthMax = _mesh_size(model_obj, mesh_quality)
        mesh.CharacteristicLengthMin = max(float(mesh.CharacteristicLengthMax) / 4.0, 0.15)
        analysis.addObject(mesh)

        orthotropic = None
        material_axes = None
        if material_model == "layer_aware":
            orthotropic = layer_aware_properties(material, structure=structure)
            e1, e2, e3 = _material_axes(build_direction)
            material_axes = {
                "e1": (e1.x, e1.y, e1.z),
                "e2": (e2.x, e2.y, e2.z),
                "e3": (e3.x, e3.y, e3.z),
            }

        doc.recompute()
        return {
            "analysis": analysis,
            "solver": solver,
            "material": mat_obj,
            "fixed": fixed,
            "force": force,
            "direction_helper": direction_helper,
            "mesh": mesh,
            "model_obj": model_obj,
            "point_loads": point_loads,
            "mapped_point_loads": [],
            "material_profile": material,
            "effective_material_profile": effective_material,
            "material_model": material_model,
            "print_structure": structure,
            "orthotropic": orthotropic,
            "material_axes": material_axes,
            "print_settings": dict(print_settings or {}),
            "solve_info": {},
        }
    except Exception:
        for obj in reversed(created):
            try:
                if doc.getObject(obj.Name) is not None:
                    doc.removeObject(obj.Name)
            except Exception:
                pass
        try:
            doc.recompute()
        except Exception:
            pass
        raise


def _mesh_size(model_obj, quality):
    bb = model_obj.Shape.BoundBox
    diagonal = max(bb.DiagonalLength, 1.0)
    divisor = {"Fast": 18.0, "Normal": 28.0, "Fine": 42.0}.get(quality, 28.0)
    return max(diagonal / divisor, 0.35)


def _clear_mesh(mesh):
    import Fem

    mesh.FemMesh = Fem.FemMesh()
    try:
        mesh.ViewObject.HighlightedNodes = []
    except Exception:
        pass
    mesh.Document.recompute()


def _make_mesh(mesh):
    from femmesh.gmshtools import GmshTools

    gmsh = GmshTools(mesh)
    error = gmsh.create_mesh()
    if error:
        App.Console.PrintWarning(f"PrintFEA Gmsh message: {error}\n")
    if mesh.FemMesh.NodeCount == 0:
        raise RuntimeError("Gmsh did not produce a mesh. Check Gmsh installation and model geometry.")


def _fmt_axis(v):
    return ",".join(f"{float(x):.12g}" for x in v)


def _candidate_nodes_for_subelement(mesh, model_obj, subelement):
    """Return mesh-node IDs constrained to the clicked CAD subelement when possible."""
    femmesh = mesh.FemMesh
    name = str(subelement or "")
    if not name:
        return []
    try:
        shape = model_obj.Shape.getElement(name)
    except Exception:
        return []
    try:
        if name.startswith("Face"):
            return list(femmesh.getNodesByFace(shape))
        if name.startswith("Edge"):
            return list(femmesh.getNodesByEdge(shape))
        if name.startswith("Vertex"):
            return list(femmesh.getNodesByVertex(shape))
    except Exception:
        return []
    return []


def _mesh_node_vector(femmesh, node_id):
    nodes = femmesh.Nodes
    try:
        p = nodes[int(node_id)]
    except Exception:
        try:
            p = dict(nodes.items()).get(int(node_id))
        except Exception:
            p = None
    if p is None:
        return None
    return App.Vector(float(p.x), float(p.y), float(p.z))


def _map_point_loads_to_mesh(objects):
    """Map clicked loads to FEM nodes.

    Contact-patch loads distribute the user-entered TOTAL force over surface
    nodes inside the selected diameter. Ideal point loads remain available for
    advanced/global-load-path studies but intentionally map to one node and
    therefore create a local stress singularity.
    """
    point_loads = list(objects.get("point_loads") or [])
    if not point_loads:
        objects["mapped_point_loads"] = []
        return []

    mesh = objects["mesh"]
    model_obj = objects.get("model_obj")
    femmesh = mesh.FemMesh
    all_ids = list(femmesh.Nodes.keys()) if hasattr(femmesh.Nodes, "keys") else [k for k, _ in femmesh.Nodes.items()]
    if not all_ids:
        raise RuntimeError("Clicked-load mapping failed because the FEM mesh has no nodes.")

    mapped = []
    for idx, load in enumerate(point_loads, start=1):
        clicked = App.Vector(*load.get("point", (0.0, 0.0, 0.0)))
        sub = str(load.get("subelement") or "")
        candidates = _candidate_nodes_for_subelement(mesh, model_obj, sub) if model_obj is not None else []
        used_fallback = False
        if not candidates:
            candidates = all_ids
            used_fallback = True

        candidate_positions = []
        best_id = None
        best_pos = None
        best_d2 = None
        for node_id in candidates:
            pos = _mesh_node_vector(femmesh, node_id)
            if pos is None:
                continue
            d = pos - clicked
            d2 = d.dot(d)
            candidate_positions.append((int(node_id), pos, d2))
            if best_d2 is None or d2 < best_d2:
                best_d2 = d2
                best_id = int(node_id)
                best_pos = pos
        if best_id is None:
            raise RuntimeError(f"Clicked load #{idx} could not be mapped to a FEM node.")

        direction = _normalized_vector(load.get("direction", (0.0, 0.0, 1.0)), f"Clicked load #{idx} direction")
        application = str(load.get("application") or "ideal_point")
        diameter = max(0.0, float(load.get("contact_diameter_mm", 0.0) or 0.0))
        radius = diameter * 0.5
        mesh_nodes = []
        node_weights = []
        patch_underresolved = False

        if application == "contact_patch" and radius > 1e-9:
            inside = [(nid, pos, d2) for nid, pos, d2 in candidate_positions if d2 <= radius * radius + 1e-9]
            if not inside:
                inside = [(best_id, best_pos, best_d2 or 0.0)]
                patch_underresolved = True
            # Smooth radial pressure approximation. The weights are normalized
            # so the nodal forces sum to exactly the force entered by the user.
            raw_weights = []
            for nid, pos, d2 in inside:
                r = math.sqrt(max(0.0, d2))
                if radius <= 1e-12:
                    w = 1.0
                else:
                    q = min(1.0, r / radius)
                    w = max(0.05, 1.0 - q * q)
                mesh_nodes.append(int(nid))
                raw_weights.append(float(w))
            total_w = sum(raw_weights) or 1.0
            node_weights = [w / total_w for w in raw_weights]
            if len(mesh_nodes) < 3:
                patch_underresolved = True
        else:
            application = "ideal_point"
            mesh_nodes = [best_id]
            node_weights = [1.0]

        mapped.append({
            "index": idx,
            "mesh_node": best_id,  # nearest/central node retained for compatibility
            "mesh_nodes": mesh_nodes,
            "node_weights": node_weights,
            "node_count": len(mesh_nodes),
            "clicked_point": (clicked.x, clicked.y, clicked.z),
            "mapped_point": (best_pos.x, best_pos.y, best_pos.z),
            "distance_mm": math.sqrt(best_d2 or 0.0),
            "subelement": sub,
            "force_n": float(load.get("force_n", 0.0)),
            "direction": (direction.x, direction.y, direction.z),
            "application": application,
            "contact_diameter_mm": diameter if application == "contact_patch" else 0.0,
            "surface_lookup_fallback": used_fallback,
            "patch_underresolved": patch_underresolved,
        })
    objects["mapped_point_loads"] = mapped
    return mapped


def _patch_point_loads_input(inp_path, objects):
    """Append clicked loads to the active CalculiX step using nodal *CLOAD.

    A contact patch is represented by several normalized nodal CLOAD entries;
    their vector sum is exactly the requested total force.
    """
    mapped = _map_point_loads_to_mesh(objects)
    if not mapped:
        return None

    nodal = {}
    for load in mapped:
        force = float(load["force_n"])
        direction = load["direction"]
        nodes = list(load.get("mesh_nodes") or [load["mesh_node"]])
        weights = list(load.get("node_weights") or [1.0])
        if len(weights) != len(nodes):
            weights = [1.0 / max(1, len(nodes))] * len(nodes)
        for node, weight in zip(nodes, weights):
            for dof, component in enumerate(direction, start=1):
                value = force * float(weight) * float(component)
                if abs(value) <= 1e-12:
                    continue
                nodal[(int(node), dof)] = nodal.get((int(node), dof), 0.0) + value

    with open(inp_path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    step_index = None
    for i, line in enumerate(lines):
        if line.strip().upper().startswith("*STEP"):
            step_index = i
            break
    if step_index is None:
        raise RuntimeError("PrintFEA could not find *STEP while adding clicked loads to the CalculiX input file.")

    insert_at = None
    output_prefixes = ("*NODE FILE", "*EL FILE", "*NODE PRINT", "*EL PRINT", "*END STEP")
    for i in range(step_index + 1, len(lines)):
        upper = lines[i].strip().upper()
        if upper.startswith(output_prefixes):
            insert_at = i
            break
    if insert_at is None:
        raise RuntimeError("PrintFEA could not locate a safe point inside the CalculiX step for clicked-load insertion.")

    block = [
        "** PrintFEA clicked contact/point loads mapped to FEM nodes\n",
        "*CLOAD\n",
    ]
    for (node, dof), value in sorted(nodal.items()):
        block.append(f"{node},{dof},{value:.12g}\n")
    lines[insert_at:insert_at] = block

    with open(inp_path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)

    for load in mapped:
        if load.get("application") == "contact_patch":
            detail = "distributed over {count} node(s) in a Ø{diam:.3f} mm patch".format(
                count=load.get("node_count", 0), diam=load.get("contact_diameter_mm", 0.0)
            )
            if load.get("patch_underresolved"):
                detail += " [mesh under-resolves patch]"
        else:
            detail = f"mapped to ideal point node {load['mesh_node']}"
        App.Console.PrintMessage(
            "PrintFEA: clicked load #{idx} {detail}; nearest node is {node} ({dist:.3f} mm from click){fb}.\n".format(
                idx=load["index"], detail=detail, node=load["mesh_node"], dist=load["distance_mm"],
                fb=" using full-mesh fallback" if load["surface_lookup_fallback"] else "",
            )
        )
    return {"mapped": mapped, "nodal_components": nodal}


def _patch_layer_aware_input(inp_path, objects):
    """Upgrade FreeCAD's isotropic material block to an orthotropic FDM model.

    FreeCAD 1.1's stock CalculiX writer currently exposes isotropic solid
    materials. CalculiX itself supports ENGINEERING CONSTANTS and *ORIENTATION,
    so PrintFEA performs a small deterministic post-write patch before ccx is
    launched. The current MVP supports one solid / one material, matching the
    rest of PrintFEA's model validation.
    """
    if objects.get("material_model") != "layer_aware":
        return None
    props = objects.get("orthotropic") or {}
    axes = objects.get("material_axes") or {}
    if not props or not axes:
        raise RuntimeError("Layer-aware material properties/orientation were not prepared.")

    with open(inp_path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    # Replace the first isotropic *ELASTIC block. PrintFEA currently supports
    # exactly one solid material, so this is intentionally strict.
    elastic_index = None
    for i, line in enumerate(lines):
        if line.strip().upper().startswith("*ELASTIC"):
            elastic_index = i
            break
    if elastic_index is None:
        raise RuntimeError("PrintFEA could not find FreeCAD's *ELASTIC material block in the CalculiX input file.")

    end = elastic_index + 1
    while end < len(lines) and not lines[end].lstrip().startswith("*"):
        end += 1
    replacement = [
        "*ELASTIC,TYPE=ENGINEERING CONSTANTS\n",
        (
            f"{props['E1']:.9g},{props['E2']:.9g},{props['E3']:.9g},"
            f"{props['nu12']:.9g},{props['nu13']:.9g},{props['nu23']:.9g},"
            f"{props['G12']:.9g},{props['G13']:.9g},\n"
        ),
        f"{props['G23']:.9g}\n",
    ]
    lines[elastic_index:end] = replacement

    # Insert the local print/material axes before the first solid section and
    # attach that orientation to every solid section in this one-solid model.
    solid_indices = [
        i for i, line in enumerate(lines) if line.strip().upper().startswith("*SOLID SECTION")
    ]
    if not solid_indices:
        raise RuntimeError("PrintFEA could not find a *SOLID SECTION in the CalculiX input file.")

    e1 = axes["e1"]
    e2 = axes["e2"]
    orientation_lines = [
        "** PrintFEA v0.2 layer-aware FDM material orientation\n",
        "*ORIENTATION,NAME=PRINTFEA_FDM,SYSTEM=RECTANGULAR\n",
        f"{_fmt_axis(e1)},{_fmt_axis(e2)}\n",
    ]
    first_solid = solid_indices[0]
    lines[first_solid:first_solid] = orientation_lines

    # The insertion shifts all original solid indices, so match by content now.
    for i, line in enumerate(lines):
        upper = line.strip().upper()
        if not upper.startswith("*SOLID SECTION"):
            continue
        if "ORIENTATION=" not in upper:
            lines[i] = line.rstrip("\r\n") + ",ORIENTATION=PRINTFEA_FDM\n"

    # Ask CalculiX to write stresses to the FRD file in the local material axes.
    # FreeCAD will then import NodeStressXX/YY/ZZ/XY/XZ/YZ as layer-relative
    # stress components, enabling a directional FDM failure screen.
    patched_el_file = False
    for i, line in enumerate(lines):
        upper = line.strip().upper()
        if not upper.startswith("*EL FILE"):
            continue
        if "GLOBAL=" in upper:
            lines[i] = re.sub(r"GLOBAL\s*=\s*YES", "GLOBAL=NO", line, flags=re.IGNORECASE)
            if "GLOBAL=NO" not in lines[i].upper():
                lines[i] = re.sub(r"GLOBAL\s*=\s*[^,\s]+", "GLOBAL=NO", line, flags=re.IGNORECASE)
        else:
            lines[i] = line.rstrip("\r\n") + ",GLOBAL=NO\n"
        patched_el_file = True

    if not patched_el_file:
        App.Console.PrintWarning(
            "PrintFEA did not find an *EL FILE card to request local stress output; "
            "directional failure screening may be unavailable for this run.\n"
        )

    with open(inp_path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)

    return {
        "input_file": inp_path,
        "local_stress_output": patched_el_file,
        "axes": axes,
        "engineering_constants": props,
    }


def _run_ccx_without_generic_popup(objects):
    """Run CcxTools while retaining stdout/stderr for useful diagnostics."""
    from femtools import ccxtools

    fea = ccxtools.FemToolsCcx()
    fea.purge_results()
    fea.update_objects()
    fea.setup_working_dir()
    message = fea.check_prerequisites()
    if message:
        raise RuntimeError("CalculiX cannot start because a prerequisite is missing:\n" + str(message))
    fea.write_inp_file()
    if not getattr(fea, "inp_file_name", ""):
        raise RuntimeError("FreeCAD could not write the CalculiX input file.")

    patch_info = _patch_layer_aware_input(fea.inp_file_name, objects)
    if patch_info:
        objects["input_patch"] = patch_info
        App.Console.PrintMessage(
            "PrintFEA: CalculiX input upgraded to layer-aware ENGINEERING CONSTANTS "
            "aligned with the captured build direction.\n"
        )

    point_patch = _patch_point_loads_input(fea.inp_file_name, objects)
    if point_patch:
        objects["point_load_patch"] = point_patch

    ret_code = fea.ccx_run()
    if ret_code == 0:
        fea.load_results()
    return fea, ret_code


def _diagnose_ccx(fea, ret_code):
    stdout = getattr(fea, "ccx_stdout", "") or ""
    stderr = getattr(fea, "ccx_stderr", "") or ""
    joined = (stdout + "\n" + stderr).lower()

    if "nonpositive jacobian" in joined:
        reason = "nonpositive Jacobian elements (distorted/inverted high-order mesh elements)"
        kind = "nonpositive_jacobian"
    elif "no material was assigned" in joined:
        reason = "one or more elements do not have a material assigned"
        kind = "missing_material"
    elif "orientation" in joined and ("error" in joined or "not defined" in joined):
        reason = "a CalculiX material-orientation input error"
        kind = "orientation"
    elif "engineering constants" in joined and "error" in joined:
        reason = "an invalid orthotropic engineering-constants definition"
        kind = "orthotropic_material"
    elif "singular" in joined and "matrix" in joined:
        reason = "a singular stiffness matrix, usually caused by insufficient constraints or disconnected geometry"
        kind = "singular_matrix"
    elif "zero pivot" in joined:
        reason = "a zero-pivot/singular system, usually caused by insufficient constraints"
        kind = "singular_matrix"
    else:
        reason = "an unclassified CalculiX solver failure"
        kind = "unknown"

    tail_source = stdout.strip() or stderr.strip()
    tail_lines = tail_source.splitlines()[-14:]
    tail = "\n".join(tail_lines)
    return {"kind": kind, "reason": reason, "ret_code": ret_code, "tail": tail}


def mesh_and_solve(objects):
    import FemGui

    mesh = objects["mesh"]
    FemGui.setActiveAnalysis(objects["analysis"])

    _make_mesh(mesh)
    fea, ret_code = _run_ccx_without_generic_popup(objects)
    if ret_code == 0:
        model_desc = (
            "layer-aware orthotropic FDM"
            if objects.get("material_model") == "layer_aware"
            else "isotropic conservative"
        )
        objects["solve_info"] = {
            "mesh_mode": "2nd-order curved",
            "fallback_used": False,
            "material_model": model_desc,
            "message": f"Solved with optimized 2nd-order curved tetrahedral elements and a {model_desc} material model.",
        }
        return objects["analysis"]

    diag = _diagnose_ccx(fea, ret_code)

    if diag["kind"] == "nonpositive_jacobian":
        # FreeCAD 1.1.x currently has a confirmed issue in which face loads can
        # be omitted when SecondOrderLinear is enabled. Do NOT use that tempting
        # fallback for a structural screening tool: a solver that converges with
        # the requested load missing is worse than an explicit failure. Instead
        # remesh with a somewhat finer 1st-order tetrahedral mesh, for which the
        # standard face-load writer remains on the normal path.
        App.Console.PrintWarning(
            "PrintFEA detected nonpositive Jacobian elements. Retrying with a refined 1st-order tetrahedral mesh.\n"
        )
        _clear_mesh(mesh)
        mesh.ElementOrder = "1st"
        mesh.SecondOrderLinear = False
        try:
            mesh.CharacteristicLengthMax = max(float(mesh.CharacteristicLengthMax) * 0.75, 0.30)
            mesh.CharacteristicLengthMin = max(float(mesh.CharacteristicLengthMax) / 4.0, 0.12)
        except Exception:
            pass
        if hasattr(mesh, "HighOrderOptimize"):
            try:
                mesh.HighOrderOptimize = "None"
            except Exception:
                pass
        mesh.Document.recompute()
        _make_mesh(mesh)
        fea2, ret_code2 = _run_ccx_without_generic_popup(objects)
        if ret_code2 == 0:
            model_desc = (
                "layer-aware orthotropic FDM"
                if objects.get("material_model") == "layer_aware"
                else "isotropic conservative"
            )
            objects["solve_info"] = {
                "mesh_mode": "refined 1st-order tetrahedral fallback",
                "fallback_used": True,
                "material_model": model_desc,
                "message": (
                    "The original curved 2nd-order mesh contained nonpositive Jacobian elements. "
                    "PrintFEA remeshed with a finer 1st-order tetrahedral mesh rather than using FreeCAD's "
                    "SecondOrderLinear path, which has a known face-load writing issue in current 1.1.x builds. "
                    f"The {model_desc} solve then succeeded. Compare against a normal/fine curved mesh when possible."
                ),
            }
            return objects["analysis"]
        diag = _diagnose_ccx(fea2, ret_code2)

    detail = f"CalculiX failed with exit code {diag['ret_code']}: {diag['reason']}."
    if diag["kind"] == "nonpositive_jacobian":
        detail += " PrintFEA already retried with a refined 1st-order mesh. Try Fine quality or repair/simplify the geometry near the reported distorted elements."
    elif diag["kind"] == "singular_matrix":
        detail += " Verify the fixed faces prevent all rigid-body motion and that the loaded region is connected to the part."
    elif diag["kind"] in ("orientation", "orthotropic_material"):
        detail += " Try the legacy isotropic material mode to isolate whether the failure is specific to the layer-aware input patch."
    if diag.get("tail"):
        detail += "\n\nLast CalculiX output:\n" + diag["tail"]
    raise RuntimeError(detail)
