"""Post-processing helpers for PrintFEA.

PrintFEA keeps FreeCAD's familiar von Mises and displacement outputs, but when the
layer-aware material model is active the PASS/CAUTION/FAIL screen is driven by
directional stresses in the print-material axes rather than by isotropic von
Mises stress alone.

CalculiX is requested to write element stresses in the local *ORIENTATION axes:
  S11/S22 = within the layer plane
  S33     = through the layer stack (build direction)
  S12     = in-plane shear
  S13/S23 = inter-layer shear

A conservative maximum-stress utilization is computed at each result node. The
99th percentile controls the screening safety factor while the absolute peak is
reported separately to retain visibility of concentrated/mesh-sensitive spots.
"""

import math
from datetime import datetime
import FreeCAD as App
import FreeCADGui as Gui

UTILIZATION_FIELD = "PrintFEA FDM Utilization"


def _utilization_expression(props):
    """Build a FreeCAD 1.1 calculator-filter expression for FDM utilization.

    FreeCAD's VTK pipeline exports stresses in Pa, while PrintFEA material
    profiles store allowables in MPa. The v0.2 orthotropic input deck requests
    local material-axis stresses, so the xx/yy/zz/xy/xz/yz fields below are the
    layer-relative stress components used by the numerical screening summary.
    """
    allow_xy = max(float(props.get("allow_xy", 0.0)), 1e-12) * 1.0e6
    allow_z = max(float(props.get("allow_z", 0.0)), 1e-12) * 1.0e6
    allow_sxy = max(float(props.get("allow_shear_xy", 0.0)), 1e-12) * 1.0e6
    allow_sz = max(float(props.get("allow_shear_z", 0.0)), 1e-12) * 1.0e6
    # vtkArrayCalculator exposes scalar-array names with spaces converted to
    # underscores. max()/abs() are supported by the calculator parser.
    return (
        "max("
        "max("
        "max(abs(Stress_xx_component)/{axy:.12g},abs(Stress_yy_component)/{axy:.12g}),"
        "abs(Stress_zz_component)/{az:.12g}"
        "),"
        "max("
        "abs(Stress_xy_component)/{asxy:.12g},"
        "max(abs(Stress_xz_component)/{asz:.12g},abs(Stress_yz_component)/{asz:.12g})"
        ")"
        ")"
    ).format(axy=allow_xy, az=allow_z, asxy=allow_sxy, asz=allow_sz)


def create_fdm_utilization_filter(pipeline, props):
    """Create a FreeCAD 1.1-compatible calculator-filter utilization map.

    FreeCAD 1.1 introduced Fem::FemPostCalculatorFilter. Using that object
    keeps the utilization field in the saved document and avoids depending on
    newer addArrayFromFunction APIs. Returns the calculator filter or None.
    """
    if pipeline is None or not props:
        return None
    doc = getattr(pipeline, "Document", None)
    if doc is None:
        return None

    try:
        calc = doc.addObject("Fem::FemPostCalculatorFilter", "PrintFEA_FDM_Utilization")
    except Exception as exc:
        App.Console.PrintWarning(f"PrintFEA: could not create FDM utilization calculator filter: {exc}\n")
        return None

    try:
        calc.Label = "PrintFEA FDM Utilization Heat Map"
        # Connecting the filter first lets it discover the pipeline's fields.
        pipeline.addObject(calc)
        doc.recompute()
        calc.FieldName = UTILIZATION_FIELD
        calc.Function = _utilization_expression(props)
        if hasattr(calc, "ReplaceInvalid"):
            calc.ReplaceInvalid = True
        if hasattr(calc, "ReplacementValue"):
            calc.ReplacementValue = 0.0
        doc.recompute()

        # Set a sensible default display. The dynamic field list is populated
        # after recompute, so use configure_pipeline once the calculator exists.
        configure_pipeline(calc, "utilization")
        try:
            calc.ViewObject.Visibility = False
        except Exception:
            pass
        return calc
    except Exception as exc:
        App.Console.PrintWarning(f"PrintFEA: FDM utilization heat map unavailable: {exc}\n")
        try:
            doc.removeObject(calc.Name)
            doc.recompute()
        except Exception:
            pass
        return None


def utilization_object_has_field(obj):
    if obj is None:
        return False
    try:
        fields = enum_options(obj.ViewObject, "Field")
    except Exception:
        return False
    return _choose_option(fields, [UTILIZATION_FIELD, "FDM Utilization", "Utilization"]) is not None


def _finite_floats(values):
    out = []
    for value in values or []:
        try:
            f = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def percentile(values, q):
    vals = sorted(_finite_floats(values))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    q = max(0.0, min(1.0, float(q)))
    pos = (len(vals) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def find_result_object(analysis):
    candidates = []
    for obj in getattr(analysis, "Group", []):
        try:
            if obj.isDerivedFrom("Fem::FemResultObject"):
                candidates.append(obj)
        except Exception:
            if hasattr(obj, "vonMises") or hasattr(obj, "DisplacementLengths"):
                candidates.append(obj)
    for obj in reversed(candidates):
        if _finite_floats(getattr(obj, "vonMises", [])) or _finite_floats(
            getattr(obj, "DisplacementLengths", [])
        ):
            return obj
    return candidates[-1] if candidates else None


def find_pipeline(analysis):
    candidates = []
    for obj in getattr(analysis, "Group", []):
        try:
            if obj.isDerivedFrom("Fem::FemPostPipeline"):
                candidates.append(obj)
                continue
        except Exception:
            pass
        if getattr(obj, "TypeId", "") == "Fem::FemPostPipeline":
            candidates.append(obj)
        elif getattr(obj, "Name", "").startswith("Pipeline_"):
            candidates.append(obj)
    return candidates[-1] if candidates else None


def _node_position(result_obj, index):
    try:
        node_numbers = list(result_obj.NodeNumbers)
        if index < 0 or index >= len(node_numbers):
            return None, None
        node_number = int(node_numbers[index])
        nodes = result_obj.Mesh.FemMesh.Nodes
        try:
            pos = nodes[node_number]
        except Exception:
            pos = dict(nodes.items()).get(node_number)
        if pos is None:
            return node_number, None
        return node_number, App.Vector(pos.x, pos.y, pos.z)
    except Exception:
        return None, None


def _directional_failure_data(result, props):
    """Return layer-relative maximum-stress utilizations for every valid node."""
    names = [
        "NodeStressXX", "NodeStressYY", "NodeStressZZ",
        "NodeStressXY", "NodeStressXZ", "NodeStressYZ",
    ]
    arrays = []
    for name in names:
        try:
            arrays.append(list(getattr(result, name)))
        except Exception:
            return None
    if not arrays or any(not arr for arr in arrays):
        return None
    n = min(len(arr) for arr in arrays)
    if n <= 0:
        return None

    allow_xy = max(float(props.get("allow_xy", 0.0)), 1e-12)
    allow_z = max(float(props.get("allow_z", 0.0)), 1e-12)
    allow_sxy = max(float(props.get("allow_shear_xy", 0.0)), 1e-12)
    allow_sz = max(float(props.get("allow_shear_z", 0.0)), 1e-12)

    utilizations = []
    modes = []
    governing_stress = []
    governing_allowable = []

    for i in range(n):
        try:
            s11, s22, s33, s12, s13, s23 = [float(arr[i]) for arr in arrays]
        except Exception:
            utilizations.append(float("nan"))
            modes.append("unavailable")
            governing_stress.append(float("nan"))
            governing_allowable.append(float("nan"))
            continue
        if not all(math.isfinite(x) for x in (s11, s22, s33, s12, s13, s23)):
            utilizations.append(float("nan"))
            modes.append("unavailable")
            governing_stress.append(float("nan"))
            governing_allowable.append(float("nan"))
            continue

        candidates = [
            (abs(s11) / allow_xy, "in-layer normal S11", s11, allow_xy),
            (abs(s22) / allow_xy, "in-layer normal S22", s22, allow_xy),
            (
                abs(s33) / allow_z,
                "inter-layer normal tension / layer separation" if s33 >= 0 else "through-layer compression",
                s33,
                allow_z,
            ),
            (abs(s12) / allow_sxy, "in-layer shear S12", s12, allow_sxy),
            (abs(s13) / allow_sz, "inter-layer shear S13", s13, allow_sz),
            (abs(s23) / allow_sz, "inter-layer shear S23", s23, allow_sz),
        ]
        u, mode, stress, allowable = max(candidates, key=lambda item: item[0])
        utilizations.append(u)
        modes.append(mode)
        governing_stress.append(stress)
        governing_allowable.append(allowable)

    valid = [(i, u) for i, u in enumerate(utilizations) if math.isfinite(u)]
    if not valid:
        return None
    peak_index, peak_u = max(valid, key=lambda item: item[1])
    p99 = percentile([u for _, u in valid], 0.99)

    # Representative failure mode: use the valid node closest to the P99
    # utilization rather than allowing a singular absolute peak to define the
    # broad failure description.
    p99_index = min(valid, key=lambda item: abs(item[1] - p99))[0] if p99 is not None else peak_index
    return {
        "utilizations": utilizations,
        "peak": peak_u,
        "p99": p99,
        "peak_index": peak_index,
        "p99_index": p99_index,
        "peak_mode": modes[peak_index],
        "p99_mode": modes[p99_index],
        "peak_stress": governing_stress[peak_index],
        "p99_stress": governing_stress[p99_index],
        "peak_allowable": governing_allowable[peak_index],
        "p99_allowable": governing_allowable[p99_index],
    }


def _failure_region_nodes(result, utilizations, center_index, threshold, max_nodes=36, radius_fraction=0.10):
    """Return a spatially-localized cloud of high-utilization nodes around a governing node."""
    center_node, center = _node_position(result, center_index)
    if center is None:
        return [], []
    positions = []
    try:
        nodes = result.Mesh.FemMesh.Nodes
        pts = [App.Vector(p.x, p.y, p.z) for _nid, p in nodes.items()]
        if pts:
            xs=[p.x for p in pts]; ys=[p.y for p in pts]; zs=[p.z for p in pts]
            diag=math.sqrt((max(xs)-min(xs))**2+(max(ys)-min(ys))**2+(max(zs)-min(zs))**2)
        else:
            diag=20.0
    except Exception:
        diag=20.0
    radius=max(diag*float(radius_fraction), 1.0)
    candidates=[]
    for i,u in enumerate(utilizations or []):
        try:
            if not math.isfinite(float(u)) or float(u) < float(threshold):
                continue
        except Exception:
            continue
        nid,pos=_node_position(result,i)
        if nid is None or pos is None:
            continue
        dist=(pos-center).Length
        if dist <= radius:
            candidates.append((dist,-float(u),int(nid),pos))
    if not candidates:
        return [center_node] if center_node else [], [center]
    candidates.sort(key=lambda x:(x[0],x[1]))
    chosen=candidates[:max(1,int(max_nodes))]
    return [x[2] for x in chosen], [x[3] for x in chosen]

def summarize_results(
    analysis,
    material,
    target_safety_factor=2.0,
    material_model="isotropic",
    orthotropic=None,
    material_axes=None,
    print_settings=None,
    local_stress_output=False,
):
    result = find_result_object(analysis)
    if result is None:
        raise RuntimeError("CalculiX finished, but no mechanical result object was found.")

    stresses = _finite_floats(getattr(result, "vonMises", []))
    displacements = _finite_floats(getattr(result, "DisplacementLengths", []))
    allowable = float(material.get("allowable_mpa", 0.0) or 0.0)
    target = max(float(target_safety_factor), 1.0)

    summary = {
        "result_object": result,
        "pipeline": find_pipeline(analysis),
        "stress_min_mpa": min(stresses) if stresses else None,
        "stress_peak_mpa": max(stresses) if stresses else None,
        "stress_p99_mpa": percentile(stresses, 0.99) if stresses else None,
        "displacement_min_mm": min(displacements) if displacements else None,
        "displacement_max_mm": max(displacements) if displacements else None,
        "allowable_mpa": allowable,
        "target_safety_factor": target,
        "node_count": len(getattr(result, "NodeNumbers", [])),
        "verdict": "INCOMPLETE",
        "verdict_detail": "Stress results were not available.",
        "estimated_safety_factor": None,
        "peak_safety_factor": None,
        "concentration_ratio": None,
        "peak_node": None,
        "peak_position": None,
        "representative_node": None,
        "representative_position": None,
        "failure_location_node": None,
        "failure_location_position": None,
        "failure_location_kind": "",
        "failure_location_mode": "",
        "failure_region_nodes": [],
        "failure_region_positions": [],
        "material_model": material_model,
        "material_axes": material_axes or {},
        "print_settings": print_settings or {},
        "local_stress_output": bool(local_stress_output),
        "failure_method": "Isotropic von Mises / allowable",
        "failure_index_peak": None,
        "failure_index_p99": None,
        "governing_failure_mode": "not available",
        "governing_stress_mpa": None,
        "governing_allowable_mpa": None,
        "directional_available": False,
        "orthotropic": orthotropic or {},
        "utilization_object": None,
        "utilization_heatmap_available": False,
        "utilization_field_name": UTILIZATION_FIELD,
    }

    # Layer-aware mode: the constitutive solve is orthotropic, and CalculiX was
    # asked to output stresses in those same local material axes. Use a
    # directional maximum-stress screen instead of treating the material as
    # isotropic after the solve.
    directional = None
    if material_model == "layer_aware" and orthotropic and local_stress_output:
        directional = _directional_failure_data(result, orthotropic)

    if directional is not None:
        summary["directional_available"] = True
        summary["failure_method"] = "Layer-aware directional maximum-stress screen"
        summary["failure_index_peak"] = directional["peak"]
        summary["failure_index_p99"] = directional["p99"]
        summary["governing_failure_mode"] = directional["p99_mode"]
        summary["governing_stress_mpa"] = directional["p99_stress"]
        summary["governing_allowable_mpa"] = directional["p99_allowable"]
        if directional["p99"] is not None and directional["p99"] > 1e-12:
            summary["estimated_safety_factor"] = 1.0 / directional["p99"]
        else:
            summary["estimated_safety_factor"] = float("inf")
        if directional["peak"] > 1e-12:
            summary["peak_safety_factor"] = 1.0 / directional["peak"]
        else:
            summary["peak_safety_factor"] = float("inf")
        if directional["p99"] and directional["p99"] > 1e-12:
            summary["concentration_ratio"] = directional["peak"] / directional["p99"]
        node, pos = _node_position(result, directional["peak_index"])
        summary["peak_node"] = node
        summary["peak_position"] = pos
        rep_node, rep_pos = _node_position(result, directional["p99_index"])
        summary["representative_node"] = rep_node
        summary["representative_position"] = rep_pos

        esf = summary["estimated_safety_factor"]
        psf = summary["peak_safety_factor"]
        mode = summary["governing_failure_mode"]
        if esf < 1.0:
            summary["verdict"] = "FAIL"
            summary["verdict_detail"] = (
                f"The layer-aware 99th-percentile utilization exceeds 1.0. Governing mode: {mode}."
            )
        elif esf < target:
            summary["verdict"] = "CAUTION"
            summary["verdict_detail"] = (
                f"Layer-aware safety factor is below the target of {target:.2f}. Governing mode: {mode}."
            )
        elif psf is not None and psf < 1.0:
            summary["verdict"] = "CAUTION"
            summary["verdict_detail"] = (
                f"The broader layer-aware stress field passes, but a localized node exceeds its directional allowable. "
                f"Peak mode: {directional['peak_mode']}. Inspect the hotspot and compare mesh qualities."
            )
        else:
            summary["verdict"] = "PASS"
            summary["verdict_detail"] = (
                f"The layer-aware directional screen meets the target safety factor of {target:.2f}. "
                f"Governing mode: {mode}."
            )

        if summary["verdict"] in ("FAIL", "CAUTION"):
            # For a broad safety-margin failure, mark a representative P99 node
            # instead of the absolute numerical peak. If only the isolated peak
            # caused CAUTION, mark that peak explicitly.
            if esf is not None and esf < target:
                summary["failure_location_node"] = summary.get("representative_node")
                summary["failure_location_position"] = summary.get("representative_position")
                summary["failure_location_kind"] = "representative high-utilization region"
            else:
                summary["failure_location_node"] = summary.get("peak_node")
                summary["failure_location_position"] = summary.get("peak_position")
                summary["failure_location_kind"] = "localized peak hotspot"
            broad = esf is not None and esf < target
            region_index = directional["p99_index"] if broad else directional["peak_index"]
            region_mode = directional["p99_mode"] if broad else directional["peak_mode"]
            region_threshold = (float(directional["p99"]) * 0.96) if broad else (float(directional["peak"]) * 0.82)
            rnodes, rpositions = _failure_region_nodes(
                result, directional.get("utilizations"), region_index, region_threshold,
                max_nodes=40, radius_fraction=0.11 if broad else 0.07
            )
            summary["failure_location_mode"] = region_mode
            summary["failure_region_nodes"] = rnodes
            summary["failure_region_positions"] = rpositions
    elif material_model == "layer_aware":
        # Orthotropic stiffness was used in the solver, so an isotropic von
        # Mises/allowable verdict would be misleading. Preserve the numerical
        # results but do not manufacture a PASS/FAIL conclusion.
        summary["verdict"] = "INCOMPLETE"
        summary["verdict_detail"] = (
            "The orthotropic solve completed, but PrintFEA could not obtain a complete local-axis stress tensor for the "
            "directional failure screen. Stress/displacement maps remain valid; the safety verdict is intentionally withheld."
        )
    elif stresses:
        peak = summary["stress_peak_mpa"]
        p99 = summary["stress_p99_mpa"]
        if allowable > 0 and p99 is not None and p99 > 1e-12:
            summary["estimated_safety_factor"] = allowable / p99
        elif allowable > 0:
            summary["estimated_safety_factor"] = float("inf")
        if allowable > 0 and peak > 1e-12:
            summary["peak_safety_factor"] = allowable / peak
        elif allowable > 0:
            summary["peak_safety_factor"] = float("inf")
        if p99 is not None and p99 > 1e-12:
            summary["concentration_ratio"] = peak / p99
        try:
            raw_stresses = list(getattr(result, "vonMises", []))
            valid_stress = []
            for i, value in enumerate(raw_stresses):
                try:
                    f = float(value)
                except Exception:
                    continue
                if math.isfinite(f):
                    valid_stress.append((i, f))
            if valid_stress:
                peak_index = max(valid_stress, key=lambda item: item[1])[0]
                p99_value = summary.get("stress_p99_mpa")
                p99_index = min(valid_stress, key=lambda item: abs(item[1] - p99_value))[0] if p99_value is not None else peak_index
                node, pos = _node_position(result, peak_index)
                summary["peak_node"] = node
                summary["peak_position"] = pos
                rep_node, rep_pos = _node_position(result, p99_index)
                summary["representative_node"] = rep_node
                summary["representative_position"] = rep_pos
        except Exception:
            pass
        esf = summary["estimated_safety_factor"]
        psf = summary["peak_safety_factor"]
        if esf is None:
            summary["verdict"] = "INCOMPLETE"
            summary["verdict_detail"] = "No material allowable was available for comparison."
        elif esf < 1.0:
            summary["verdict"] = "FAIL"
            summary["verdict_detail"] = "The 99th-percentile von Mises stress exceeds the selected isotropic allowable."
        elif esf < target:
            summary["verdict"] = "CAUTION"
            summary["verdict_detail"] = f"Estimated isotropic safety factor is below the target of {target:.2f}."
        elif psf is not None and psf < 1.0:
            summary["verdict"] = "CAUTION"
            summary["verdict_detail"] = (
                "A localized peak exceeds the isotropic material allowable even though the broader high-stress region passes. "
                "Inspect the hotspot and repeat with a finer mesh."
            )
        else:
            summary["verdict"] = "PASS"
            summary["verdict_detail"] = f"The isotropic 99th-percentile stress meets the target safety factor of {target:.2f}."

        if summary["verdict"] in ("FAIL", "CAUTION"):
            if esf is not None and esf < target:
                summary["failure_location_node"] = summary.get("representative_node")
                summary["failure_location_position"] = summary.get("representative_position")
                summary["failure_location_kind"] = "representative high-stress region"
            else:
                summary["failure_location_node"] = summary.get("peak_node")
                summary["failure_location_position"] = summary.get("peak_position")
                summary["failure_location_kind"] = "localized peak hotspot"

    return summary


def create_summary_object(doc, analysis, summary, material_name):
    """Persist the screening summary and FDM-analysis context in the document."""
    obj = doc.addObject("App::FeaturePython", "PrintFEA_Summary")
    obj.Label = "PrintFEA Result Summary"

    def add(ptype, name, group, desc):
        obj.addProperty(ptype, name, group, desc)

    add("App::PropertyString", "Verdict", "PrintFEA", "Screening verdict")
    add("App::PropertyString", "VerdictDetail", "PrintFEA", "Explanation")
    add("App::PropertyString", "RunTimestamp", "PrintFEA", "Local time when this run finished")
    add("App::PropertyString", "MaterialProfile", "PrintFEA", "Selected material profile")
    add("App::PropertyString", "MaterialModel", "PrintFEA", "Isotropic or layer-aware material model")
    add("App::PropertyString", "FailureMethod", "PrintFEA", "Failure-screening method")
    add("App::PropertyString", "GoverningFailureMode", "PrintFEA", "Directional mode controlling the screen")
    add("App::PropertyString", "MeshSolverMode", "PrintFEA", "Meshing / solver mode used")
    add("App::PropertyLink", "AnalysisObject", "PrintFEA Links", "Owning PrintFEA FEM analysis")
    add("App::PropertyLink", "ResultObject", "PrintFEA Links", "Mechanical CalculiX result object")
    add("App::PropertyLink", "PipelineObject", "PrintFEA Links", "FreeCAD post-processing pipeline")
    add("App::PropertyLink", "UtilizationObject", "PrintFEA Links", "FDM utilization calculator-filter heat map")
    add("App::PropertyBool", "UtilizationHeatmapAvailable", "PrintFEA FDM", "Whether a layer-aware utilization heat map is available")
    add("App::PropertyString", "UtilizationFieldName", "PrintFEA FDM", "Calculated utilization field shown by the heat map")
    add("App::PropertyFloat", "AllowableStressMPa", "PrintFEA", "Legacy/in-plane conservative material allowable")
    add("App::PropertyFloat", "LayerAllowableMPa", "PrintFEA FDM", "Through-layer normal allowable")
    add("App::PropertyFloat", "InPlaneShearAllowableMPa", "PrintFEA FDM", "In-layer shear allowable")
    add("App::PropertyFloat", "InterlayerShearAllowableMPa", "PrintFEA FDM", "Inter-layer shear allowable")
    add("App::PropertyFloat", "InPlaneModulusMPa", "PrintFEA FDM", "E1/E2 layer-plane Young's modulus")
    add("App::PropertyFloat", "BuildModulusMPa", "PrintFEA FDM", "E3 build-direction Young's modulus")
    add("App::PropertyFloat", "InPlaneShearModulusMPa", "PrintFEA FDM", "G12 in-layer shear modulus")
    add("App::PropertyFloat", "InterlayerShearModulusMPa", "PrintFEA FDM", "G13/G23 inter-layer shear modulus")
    add("App::PropertyFloat", "PeakVonMisesMPa", "PrintFEA", "Absolute peak nodal von Mises stress")
    add("App::PropertyFloat", "P99VonMisesMPa", "PrintFEA", "99th-percentile nodal von Mises stress")
    add("App::PropertyFloat", "MaxDisplacementMM", "PrintFEA", "Maximum displacement magnitude")
    add("App::PropertyFloat", "EstimatedSafetyFactor", "PrintFEA", "99th-percentile screening safety factor")
    add("App::PropertyFloat", "PeakSafetyFactor", "PrintFEA", "Absolute peak-node safety factor")
    add("App::PropertyFloat", "TargetSafetyFactor", "PrintFEA", "User target safety factor")
    add("App::PropertyFloat", "FailureIndexP99", "PrintFEA FDM", "99th-percentile directional utilization")
    add("App::PropertyFloat", "FailureIndexPeak", "PrintFEA FDM", "Peak directional utilization")
    add("App::PropertyFloat", "GoverningStressMPa", "PrintFEA FDM", "Representative governing directional stress")
    add("App::PropertyFloat", "GoverningAllowableMPa", "PrintFEA FDM", "Allowable corresponding to governing directional stress")
    add("App::PropertyFloat", "StressConcentrationRatio", "PrintFEA", "Peak / 99th-percentile screening utilization or stress")
    add("App::PropertyInteger", "PeakNode", "PrintFEA", "Node containing the absolute screening peak")
    add("App::PropertyVector", "PeakPosition", "PrintFEA", "Approximate screening-peak node location")
    add("App::PropertyInteger", "RepresentativeNode", "PrintFEA", "Node representing the 99th-percentile high-utilization region")
    add("App::PropertyVector", "RepresentativePosition", "PrintFEA", "Representative 99th-percentile high-utilization location")
    add("App::PropertyInteger", "FailureLocationNode", "PrintFEA", "Node highlighted as the likely failure region for CAUTION/FAIL")
    add("App::PropertyVector", "FailureLocationPosition", "PrintFEA", "Location highlighted as the likely failure region for CAUTION/FAIL")
    add("App::PropertyString", "FailureLocationKind", "PrintFEA", "Whether the highlighted location is representative or an isolated peak")
    add("App::PropertyString", "FailureLocationMode", "PrintFEA", "Directional failure mode associated with the highlighted region")
    add("App::PropertyVectorList", "FailureRegionPositions", "PrintFEA", "High-utilization FEM node cloud around the likely failure region")
    add("App::PropertyInteger", "FailureRegionNodeCount", "PrintFEA", "Number of FEM nodes highlighted in the likely failure region")
    add("App::PropertyVector", "BuildDirection", "PrintFEA FDM", "Material axis 3 / print build direction")
    add("App::PropertyFloat", "LayerHeightMM", "PrintFEA Print Settings", "Recorded layer height")
    add("App::PropertyInteger", "Walls", "PrintFEA Print Settings", "Recorded perimeter/wall count")
    add("App::PropertyInteger", "InfillPercent", "PrintFEA Print Settings", "Recorded infill percentage")
    add("App::PropertyFloat", "WallLineWidthMM", "PrintFEA Print Settings", "Recorded perimeter/wall line width")
    add("App::PropertyBool", "StructureModelEnabled", "PrintFEA Structure", "Whether wall/infill homogenization was enabled")
    add("App::PropertyFloat", "EstimatedShellFraction", "PrintFEA Structure", "Estimated continuous perimeter-shell fraction of CAD volume")
    add("App::PropertyFloat", "EffectiveMaterialFraction", "PrintFEA Structure", "Estimated shell + sparse-infill material fraction")
    add("App::PropertyFloat", "StructureStiffnessScale", "PrintFEA Structure", "Applied stiffness multiplier relative to 100% structural fill")
    add("App::PropertyFloat", "StructureStrengthScale", "PrintFEA Structure", "Applied strength multiplier relative to 100% structural fill")
    add("App::PropertyString", "StructureMethod", "PrintFEA Structure", "Wall/infill homogenization method")
    add("App::PropertyFloat", "EstimatedCoreVolumeMM3", "PrintFEA Structure", "Integrated sparse-core volume after layer-sliced perimeter offsets")
    add("App::PropertyFloat", "EstimatedShellVolumeMM3", "PrintFEA Structure", "Geometric shell volume around the core")
    add("App::PropertyBool", "StructureFallbackUsed", "PrintFEA Structure", "Whether any slice/perimeter fallback was used")
    add("App::PropertyString", "StructureFallbackReason", "PrintFEA Structure", "Reason the geometric shell/core calculation fell back")
    add("App::PropertyString", "BuildPlateFace", "PrintFEA Print Settings", "Captured build-plate face")
    add("App::PropertyInteger", "PointLoadCount", "PrintFEA Loads", "Number of arbitrary point forces")
    add("App::PropertyString", "PointLoadSummary", "PrintFEA Loads", "Clicked point forces and mapped FEM nodes")
    add("App::PropertyString", "ImportantLimitations", "PrintFEA", "Model limitations")

    obj.Verdict = summary.get("verdict", "INCOMPLETE")
    obj.VerdictDetail = summary.get("verdict_detail", "")
    obj.RunTimestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    obj.MaterialProfile = material_name
    obj.MaterialModel = "Layer-aware orthotropic FDM" if summary.get("material_model") == "layer_aware" else "Isotropic conservative"
    obj.FailureMethod = summary.get("failure_method", "")
    obj.GoverningFailureMode = summary.get("governing_failure_mode", "")
    solve_info = summary.get("solve_info", {}) or {}
    mesh_mode = str(solve_info.get("mesh_mode", "not reported") or "not reported")
    if solve_info.get("fallback_used"):
        mesh_mode += " (automatic Jacobian fallback)"
    obj.MeshSolverMode = mesh_mode
    try:
        obj.AnalysisObject = analysis
        obj.ResultObject = summary.get("result_object")
        obj.PipelineObject = summary.get("pipeline")
        obj.UtilizationObject = summary.get("utilization_object")
    except Exception:
        pass
    obj.UtilizationHeatmapAvailable = bool(summary.get("utilization_heatmap_available"))
    obj.UtilizationFieldName = str(summary.get("utilization_field_name") or UTILIZATION_FIELD)
    obj.AllowableStressMPa = float(summary.get("allowable_mpa") or 0.0)
    ortho = summary.get("orthotropic", {}) or {}
    obj.LayerAllowableMPa = float(ortho.get("allow_z") or 0.0)
    obj.InPlaneShearAllowableMPa = float(ortho.get("allow_shear_xy") or 0.0)
    obj.InterlayerShearAllowableMPa = float(ortho.get("allow_shear_z") or 0.0)
    obj.InPlaneModulusMPa = float(ortho.get("E1") or 0.0)
    obj.BuildModulusMPa = float(ortho.get("E3") or 0.0)
    obj.InPlaneShearModulusMPa = float(ortho.get("G12") or 0.0)
    obj.InterlayerShearModulusMPa = float(ortho.get("G13") or 0.0)
    obj.PeakVonMisesMPa = float(summary.get("stress_peak_mpa") or 0.0)
    obj.P99VonMisesMPa = float(summary.get("stress_p99_mpa") or 0.0)
    obj.MaxDisplacementMM = float(summary.get("displacement_max_mm") or 0.0)
    obj.EstimatedSafetyFactor = _finite_property(summary.get("estimated_safety_factor"))
    obj.PeakSafetyFactor = _finite_property(summary.get("peak_safety_factor"))
    obj.TargetSafetyFactor = float(summary.get("target_safety_factor") or 0.0)
    obj.FailureIndexP99 = _finite_property(summary.get("failure_index_p99"))
    obj.FailureIndexPeak = _finite_property(summary.get("failure_index_peak"))
    obj.GoverningStressMPa = float(summary.get("governing_stress_mpa") or 0.0)
    obj.GoverningAllowableMPa = float(summary.get("governing_allowable_mpa") or 0.0)
    obj.StressConcentrationRatio = _finite_property(summary.get("concentration_ratio"))
    obj.PeakNode = int(summary.get("peak_node") or 0)
    obj.PeakPosition = summary.get("peak_position") or App.Vector(0, 0, 0)
    obj.RepresentativeNode = int(summary.get("representative_node") or 0)
    obj.RepresentativePosition = summary.get("representative_position") or App.Vector(0, 0, 0)
    obj.FailureLocationNode = int(summary.get("failure_location_node") or 0)
    obj.FailureLocationPosition = summary.get("failure_location_position") or App.Vector(0, 0, 0)
    obj.FailureLocationKind = str(summary.get("failure_location_kind") or "")
    obj.FailureLocationMode = str(summary.get("failure_location_mode") or summary.get("governing_failure_mode") or "")
    try:
        obj.FailureRegionPositions = list(summary.get("failure_region_positions") or [])
    except Exception:
        pass
    obj.FailureRegionNodeCount = len(summary.get("failure_region_nodes") or [])
    settings = summary.get("print_settings", {}) or {}
    bd = settings.get("build_direction")
    if bd:
        try:
            obj.BuildDirection = App.Vector(*bd)
        except Exception:
            pass
    obj.LayerHeightMM = float(settings.get("layer_height_mm") or 0.0)
    obj.Walls = int(settings.get("walls") or 0)
    obj.InfillPercent = int(settings.get("infill_percent") or 0)
    obj.WallLineWidthMM = float(settings.get("line_width_mm") or 0.0)
    structure = summary.get("print_structure", {}) or {}
    obj.StructureModelEnabled = bool(structure.get("enabled"))
    obj.EstimatedShellFraction = float(structure.get("shell_fraction") or 0.0)
    obj.EffectiveMaterialFraction = float(structure.get("effective_material_fraction") or 0.0)
    obj.StructureStiffnessScale = float(structure.get("stiffness_scale") or 1.0)
    obj.StructureStrengthScale = float(structure.get("strength_scale") or 1.0)
    obj.StructureMethod = str(structure.get("method") or "not recorded")
    obj.EstimatedCoreVolumeMM3 = float(structure.get("core_volume_mm3") or 0.0)
    obj.EstimatedShellVolumeMM3 = float(structure.get("shell_volume_mm3") or 0.0)
    obj.StructureFallbackUsed = bool(structure.get("fallback_used"))
    obj.StructureFallbackReason = str(structure.get("fallback_reason") or "")
    # v0.3.0 slicer-style geometry diagnostics. Add properties dynamically so
    # older saved result objects remain compatible with the recent-results UI.
    for prop, kind in (
        ("EstimatedPrintLayers", "App::PropertyInteger"),
        ("SampledSlices", "App::PropertyInteger"),
        ("PartialWallSlices", "App::PropertyInteger"),
        ("SectionFailureSlices", "App::PropertyInteger"),
        ("LayerSampleStepMM", "App::PropertyLength"),
        ("SliceIntegrationErrorPercent", "App::PropertyFloat"),
        ("ParallelStructureWorkers", "App::PropertyInteger"),
        ("TimedOutStructureSlices", "App::PropertyInteger"),
        ("SlowSliceLimitSeconds", "App::PropertyFloat"),
    ):
        if not hasattr(obj, prop):
            try:
                obj.addProperty(kind, prop, "PrintFEA Structure")
            except Exception:
                pass
    try:
        obj.EstimatedPrintLayers = int(structure.get("estimated_layer_count") or 0)
        obj.SampledSlices = int(structure.get("sampled_slice_count") or 0)
        obj.PartialWallSlices = int(structure.get("partial_wall_slice_count") or 0)
        obj.SectionFailureSlices = int(structure.get("section_failure_count") or 0)
        obj.LayerSampleStepMM = float(structure.get("sample_step_mm") or 0.0)
        obj.SliceIntegrationErrorPercent = float(structure.get("integration_error_fraction") or 0.0) * 100.0
        obj.ParallelStructureWorkers = int(structure.get("parallel_workers") or 1)
        obj.TimedOutStructureSlices = int(structure.get("timed_out_slice_count") or 0)
        obj.SlowSliceLimitSeconds = float(structure.get("slow_slice_timeout_seconds") or 0.0)
    except Exception:
        pass
    obj.BuildPlateFace = str(settings.get("build_face") or "")
    point_loads = list(summary.get("point_loads") or [])
    mapped_point_loads = list(summary.get("mapped_point_loads") or [])
    obj.PointLoadCount = len(point_loads)
    mapped_by_index = {int(x.get("index", 0)): x for x in mapped_point_loads if isinstance(x, dict)}
    point_lines = []
    for i, load in enumerate(point_loads, start=1):
        p = load.get("point", (0, 0, 0))
        d = load.get("direction", (0, 0, 0))
        app = str(load.get("application") or "ideal_point")
        if app == "contact_patch":
            app_text = f"Ø{float(load.get('contact_diameter_mm', 0.0)):.2f} mm contact patch"
        else:
            app_text = "ideal point"
        line = f"#{i}: {float(load.get('force_n', 0.0)):.3f} N TOTAL dir {tuple(round(float(v), 4) for v in d)} at ({float(p[0]):.3f}, {float(p[1]):.3f}, {float(p[2]):.3f}) mm; {app_text}"
        mapped = mapped_by_index.get(i)
        if mapped:
            if str(mapped.get("application") or "ideal_point") == "contact_patch":
                line += f" -> {int(mapped.get('node_count') or 0)} mesh nodes (nearest {mapped.get('mesh_node')}, {float(mapped.get('distance_mm', 0.0)):.3f} mm)"
                if mapped.get("patch_underresolved"):
                    line += " [patch under-resolved by current mesh]"
            else:
                line += f" -> node {mapped.get('mesh_node')} ({float(mapped.get('distance_mm', 0.0)):.3f} mm mapping distance)"
        point_lines.append(line)
    obj.PointLoadSummary = " | ".join(point_lines)
    if summary.get("material_model") == "layer_aware":
        obj.ImportantLimitations = (
            "Screening result only. PrintFEA models one homogenized orthotropic solid aligned to the build direction using generic conservative FDM ratios. "
            "Perimeter count and infill density use layer-sliced 2D perimeter/core homogenization aligned to the captured build direction, but separate top/bottom skin counts, exact extrusion paths, individual infill cells, infill pattern, raster direction, voids, seams, supports, print defects, creep, fatigue, impact, and temperature are not explicitly resolved. "
            "Coupon-calibrated properties are recommended for important parts. Clicked contact patches are approximated by normalized nodal loads over nearby surface mesh nodes; refine the mesh if the patch is under-resolved. Ideal point loads remain singular. Peak nodal values can remain mesh-sensitive near constraints and sharp corners."
        )
    else:
        obj.ImportantLimitations = (
            "Screening result only. Legacy isotropic mode ignores print-direction anisotropy and layer adhesion. "
            "Wall/infill uses the same layer-sliced shell/core homogenization, but separate top/bottom skins, defects, creep, fatigue, impact, and temperature are not explicitly resolved."
        )
    try:
        obj.ViewObject.Visibility = False
    except Exception:
        pass
    try:
        analysis.addObject(obj)
    except Exception:
        pass
    doc.recompute()
    return obj


def _finite_property(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 1.0e99


def enum_options(obj, prop):
    try:
        return list(obj.getEnumerationsOfProperty(prop))
    except Exception:
        return []


def _choose_option(options, needles):
    lowered = [(opt, str(opt).lower()) for opt in options]
    for needle in needles:
        n = needle.lower()
        for opt, lo in lowered:
            if n == lo:
                return opt
        for opt, lo in lowered:
            if n in lo:
                return opt
    return None


def configure_pipeline(pipeline, quantity):
    """Switch FreeCAD's native FEM post pipeline to a useful result field.

    Field names have changed slightly across FreeCAD versions, so selections
    are matched dynamically from the enum choices rather than hard-coded.
    Returns the actual field name selected, or ``None`` if unavailable.
    """
    if pipeline is None:
        return None
    view = pipeline.ViewObject

    try:
        if "Surface" in enum_options(view, "DisplayMode"):
            view.DisplayMode = "Surface"
    except Exception:
        pass

    field_options = enum_options(view, "Field")
    if quantity == "displacement":
        chosen = _choose_option(field_options, ["Displacement"])
    elif quantity == "utilization":
        chosen = _choose_option(field_options, [UTILIZATION_FIELD, "FDM Utilization", "Utilization"])
    elif quantity == "stress":
        chosen = _choose_option(
            field_options,
            ["von Mises Stress", "von Mises", "Mises", "Equivalent Stress"],
        )
        if chosen is None:
            # Last-resort fallback, but deliberately avoid principal stress if
            # a more generic stress field exists.
            stress_options = [o for o in field_options if "stress" in str(o).lower()]
            chosen = stress_options[0] if stress_options else None
    else:
        raise ValueError(f"Unknown pipeline quantity: {quantity}")

    if chosen is None:
        return None

    try:
        view.Field = chosen
    except Exception:
        return None

    component_options = enum_options(view, "Component")
    if quantity == "displacement":
        magnitude = _choose_option(component_options, ["Magnitude"])
        if magnitude is not None:
            try:
                view.Component = magnitude
            except Exception:
                pass
    elif quantity == "utilization":
        scalar = _choose_option(component_options, ["Not a vector"])
        if scalar is not None:
            try:
                view.Component = scalar
            except Exception:
                pass

    try:
        pipeline.ViewObject.Visibility = True
    except Exception:
        pass
    try:
        view.updateColorBars()
    except Exception:
        pass
    try:
        pipeline.Document.recompute()
    except Exception:
        pass
    return str(chosen)



def isolate_and_focus_pipeline(pipeline, open_task_panel=True):
    """Show only the newest post pipeline and bring its result controls forward.

    This intentionally hides geometry, meshes, constraints, helper objects and
    older result pipelines in the document. Re-running PrintFEA therefore lands
    on the newest ``Pipeline_CCX_Results`` instead of leaving several analyses
    visually stacked on top of one another.
    """
    if pipeline is None:
        return False

    doc = getattr(pipeline, "Document", None)
    if doc is None:
        return False

    try:
        gui_doc = Gui.getDocument(doc.Name)
    except Exception:
        gui_doc = Gui.activeDocument()

    # Close an older pipeline's task panel before opening the new one.
    try:
        gui_doc.resetEdit()
    except Exception:
        pass

    # Hide every other drawable document object. Set the desired pipeline last
    # so group/parent visibility changes cannot leave it hidden.
    for obj in list(getattr(doc, "Objects", [])):
        if obj is pipeline:
            continue
        try:
            obj.ViewObject.Visibility = False
        except Exception:
            pass

    try:
        pipeline.ViewObject.Visibility = True
        pipeline.ViewObject.updateColorBars()
    except Exception:
        pass

    try:
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(pipeline)
    except Exception:
        pass

    opened = False
    if open_task_panel and gui_doc is not None:
        try:
            opened = bool(gui_doc.setEdit(pipeline.Name, 0))
        except TypeError:
            try:
                opened = bool(gui_doc.setEdit(pipeline.Name))
            except Exception:
                opened = False
        except Exception:
            opened = False

    try:
        pipeline.ViewObject.Visibility = True
        pipeline.ViewObject.updateColorBars()
    except Exception:
        pass
    return True
