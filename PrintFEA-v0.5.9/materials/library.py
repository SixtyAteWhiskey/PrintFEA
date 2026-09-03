"""Built-in PrintFEA material screening profiles.

The base values are intentionally conservative generic FDM approximations, not
manufacturer datasheet values. v0.2 adds a layer-aware orthotropic screening
model by applying conservative ratios to the in-plane baseline. These defaults
are useful for design iteration, but should be replaced by coupon-derived values
for a specific printer / filament / process when accuracy matters.
"""

MATERIALS = {
    "PLA (generic conservative)": {
        "name": "PLA - Generic Conservative FDM",
        "youngs_modulus_mpa": 2400,
        "poisson_ratio": 0.36,
        "density_kg_m3": 1240,
        "allowable_mpa": 28,
        "z_modulus_ratio": 0.55,
        "z_allowable_ratio": 0.50,
        "shear_xy_allowable_ratio": 0.60,
        "shear_z_allowable_ratio": 0.42,
        "shear_z_modulus_ratio": 0.50,
    },
    "PETG (generic conservative)": {
        "name": "PETG - Generic Conservative FDM",
        "youngs_modulus_mpa": 1700,
        "poisson_ratio": 0.38,
        "density_kg_m3": 1270,
        "allowable_mpa": 22,
        "z_modulus_ratio": 0.58,
        "z_allowable_ratio": 0.52,
        "shear_xy_allowable_ratio": 0.60,
        "shear_z_allowable_ratio": 0.45,
        "shear_z_modulus_ratio": 0.52,
    },
    "ASA (generic conservative)": {
        "name": "ASA - Generic Conservative FDM",
        "youngs_modulus_mpa": 1800,
        "poisson_ratio": 0.35,
        "density_kg_m3": 1070,
        "allowable_mpa": 24,
        "z_modulus_ratio": 0.55,
        "z_allowable_ratio": 0.50,
        "shear_xy_allowable_ratio": 0.60,
        "shear_z_allowable_ratio": 0.42,
        "shear_z_modulus_ratio": 0.50,
    },
    "ABS (generic conservative)": {
        "name": "ABS - Generic Conservative FDM",
        "youngs_modulus_mpa": 1700,
        "poisson_ratio": 0.35,
        "density_kg_m3": 1040,
        "allowable_mpa": 22,
        "z_modulus_ratio": 0.52,
        "z_allowable_ratio": 0.48,
        "shear_xy_allowable_ratio": 0.60,
        "shear_z_allowable_ratio": 0.40,
        "shear_z_modulus_ratio": 0.48,
    },
    "Nylon / PA (generic conservative)": {
        "name": "PA - Generic Conservative FDM",
        "youngs_modulus_mpa": 1400,
        "poisson_ratio": 0.39,
        "density_kg_m3": 1140,
        "allowable_mpa": 24,
        "z_modulus_ratio": 0.50,
        "z_allowable_ratio": 0.45,
        "shear_xy_allowable_ratio": 0.58,
        "shear_z_allowable_ratio": 0.38,
        "shear_z_modulus_ratio": 0.45,
    },
}


def _clamp(value, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(value)))


# Geometry-only cache. Infill percentage is intentionally excluded because it
# does not change the perimeter/core split; changing only infill should reuse
# the expensive slice calculation.
_SLICE_CORE_CACHE = {}


def _normalized_tuple(values):
    try:
        x, y, z = [float(v) for v in values]
    except Exception:
        return None
    length = (x * x + y * y + z * z) ** 0.5
    if length <= 1e-12:
        return None
    return (x / length, y / length, z / length)


def _shape_signature(model_obj, shape):
    try:
        bb = shape.BoundBox
        dims = (float(bb.XLength), float(bb.YLength), float(bb.ZLength))
    except Exception:
        dims = (0.0, 0.0, 0.0)
    try:
        area = float(shape.Area)
    except Exception:
        area = 0.0
    try:
        volume = float(shape.Volume)
    except Exception:
        volume = 0.0
    return (
        getattr(model_obj, "Name", str(id(model_obj))),
        round(volume, 6),
        round(area, 6),
        *(round(v, 6) for v in dims),
    )


def _slice_cache_key(model_obj, shape, build_direction, walls, line_width_mm, layer_height_mm, max_samples):
    n = _normalized_tuple(build_direction)
    return (
        *_shape_signature(model_obj, shape),
        *(round(v, 8) for v in (n or (0.0, 0.0, 0.0))),
        int(walls),
        round(float(line_width_mm or 0.0), 6),
        round(float(layer_height_mm or 0.0), 6),
        int(max_samples),
    )


def _projected_bounds(shape, direction):
    """Return conservative min/max plane distances along a normalized vector."""
    n = _normalized_tuple(direction)
    if n is None:
        raise ValueError("Build direction is missing or invalid.")
    nx, ny, nz = n
    bb = shape.BoundBox
    corners = [
        (x, y, z)
        for x in (float(bb.XMin), float(bb.XMax))
        for y in (float(bb.YMin), float(bb.YMax))
        for z in (float(bb.ZMin), float(bb.ZMax))
    ]
    projections = [x * nx + y * ny + z * nz for x, y, z in corners]
    return min(projections), max(projections), n


def _make_slice_face(wires):
    """Build planar material face(s) from slice wires, preserving holes/islands."""
    import Part

    closed = []
    for wire in wires or []:
        try:
            if wire.isClosed() and len(wire.Edges) > 0:
                closed.append(wire)
        except Exception:
            continue
    if not closed:
        return None

    # FaceMakerBullseye is specifically intended for nested planar wires: outer
    # contours, holes, and islands. noElementMap is available in current
    # FreeCAD, while the fallback keeps compatibility with older builds.
    try:
        face = Part.makeFace(closed, "Part::FaceMakerBullseye", noElementMap=True)
    except TypeError:
        face = Part.makeFace(closed, "Part::FaceMakerBullseye")
    if face is None or face.isNull():
        return None
    return face


def _offset_candidate(shape, offset_mm, previous_area):
    """Return a conservative valid one-wall inward offset, or None.

    2-D offset orientation can vary with transient face orientation. Try both
    signs and multiple join/intersection choices, then select the *largest*
    valid area that is smaller than the current area. Choosing the larger core
    is conservative because it credits less dense perimeter material.
    """
    prev = max(0.0, float(previous_area))
    tol = max(1e-8, prev * 1e-5)
    candidates = []
    for sign in (-1.0, 1.0):
        for join in (0, 2):  # Arc, Intersection
            for collective in (True, False):
                try:
                    candidate = shape.makeOffset2D(
                        sign * float(offset_mm), join, False, False, collective
                    )
                    if candidate is None or candidate.isNull():
                        continue
                    area = float(candidate.Area)
                    if area < -tol or area > prev + tol:
                        continue
                    # Ignore a no-op offset. It is safer to report a partial
                    # perimeter calculation than to count a failed wall pass.
                    if prev > tol and area >= prev - tol:
                        continue
                    candidates.append((max(0.0, area), candidate))
                except Exception:
                    continue
    if not candidates:
        return None
    # Largest remaining core = least shell credit = conservative among the
    # successful equivalent offset variants.
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0]


def _core_area_after_walls(section_face, section_area, walls, line_width_mm, yield_callback=None):
    """Offset one perimeter line at a time, similar to a slicer wall stack.

    If an individual offset cannot be completed, retain the last valid core
    area. This under-credits the remaining requested walls instead of assuming
    the cross-section became solid, so the fallback is conservative.
    """
    current = section_face
    current_area = max(0.0, float(section_area))
    completed = 0
    if walls <= 0 or line_width_mm <= 1e-9 or current_area <= 1e-12:
        return current_area, completed, False

    for _ in range(int(walls)):
        if yield_callback is not None:
            try:
                yield_callback()
            except Exception:
                pass
        result = _offset_candidate(current, line_width_mm, current_area)
        if yield_callback is not None:
            try:
                yield_callback()
            except Exception:
                pass
        if result is None:
            return current_area, completed, True
        next_area, next_shape = result
        current_area = next_area
        current = next_shape
        completed += 1
        if current_area <= 1e-10:
            return 0.0, completed, False
    return current_area, completed, False


def _slice_shell_core_geometry(
    model_obj,
    shape,
    build_direction,
    walls,
    line_width_mm,
    layer_height_mm,
    volume,
    max_samples=48,
    progress_callback=None,
    yield_callback=None,
):
    """Integrate shell/core volume from slicer-style planar cross sections.

    FreeCAD exposes ``TopoShape.slice(direction, distance)`` which returns the
    closed section wires of a B-Rep at that plane. We reconstruct each planar
    material region with FaceMakerBullseye, then offset it inward one wall-line
    width at a time. Midpoint integration across the build span converts those
    2-D areas back into shell/core volume.

    For tall/fine-layer parts, the physical layer count can be very large. The
    estimator evaluates at most ``max_samples`` evenly-spaced layer samples and
    records both the physical layer estimate and actual sample count. This is a
    homogenization tool, not an exact slicer path generator.
    """
    import FreeCAD as App

    dmin, dmax, n = _projected_bounds(shape, build_direction)
    span = max(0.0, dmax - dmin)
    h = max(1e-4, float(layer_height_mm or 0.20))
    if span <= 1e-9:
        raise RuntimeError("Model has no measurable thickness along the captured build direction.")

    physical_layers = max(1, int(__import__('math').ceil(span / h)))
    sample_count = max(1, min(int(max_samples), physical_layers))
    sample_step = span / float(sample_count)
    normal = App.Vector(*n)

    sampled_volume = 0.0
    sampled_core_volume = 0.0
    valid_slices = 0
    empty_slices = 0
    section_failures = 0
    partial_wall_slices = 0
    completed_wall_passes = 0
    requested_wall_passes = 0

    for i in range(sample_count):
        if progress_callback is not None:
            try:
                progress_callback(i, sample_count)
            except Exception:
                pass
        distance = dmin + (i + 0.5) * sample_step
        try:
            if yield_callback is not None:
                try:
                    yield_callback()
                except Exception:
                    pass
            wires = shape.slice(normal, distance)
            if yield_callback is not None:
                try:
                    yield_callback()
                except Exception:
                    pass
            face = _make_slice_face(wires)
            if yield_callback is not None:
                try:
                    yield_callback()
                except Exception:
                    pass
            if face is None:
                empty_slices += 1
                continue
            area = float(face.Area)
            if area <= 1e-10:
                empty_slices += 1
                continue
        except Exception:
            section_failures += 1
            continue

        valid_slices += 1
        sampled_volume += area * sample_step
        requested_wall_passes += int(walls)
        try:
            core_area, passes, partial = _core_area_after_walls(
                face, area, walls, line_width_mm, yield_callback=yield_callback
            )
            completed_wall_passes += int(passes)
            if partial:
                partial_wall_slices += 1
        except Exception:
            # No shell credit on this slice is the conservative failure mode.
            core_area = area
            partial_wall_slices += 1
        sampled_core_volume += min(area, max(0.0, float(core_area))) * sample_step

    if progress_callback is not None:
        try:
            progress_callback(sample_count, sample_count)
        except Exception:
            pass

    if sampled_volume <= 1e-9 or valid_slices <= 0:
        raise RuntimeError(
            "FreeCAD could not build usable planar cross-sections along the captured build direction."
        )

    # Midpoint sampling is approximate. Normalize to the exact B-Rep volume so
    # the shell/core split does not inherit small numerical integration bias.
    integration_error = (sampled_volume - float(volume)) / float(volume)
    normalization = float(volume) / sampled_volume
    core_volume = sampled_core_volume * normalization
    core_volume = min(float(volume), max(0.0, core_volume))
    shell_volume = max(0.0, float(volume) - core_volume)

    return {
        "core_volume_mm3": core_volume,
        "shell_volume_mm3": shell_volume,
        "shell_fraction": _clamp(shell_volume / float(volume)),
        "estimated_layer_count": physical_layers,
        "sampled_slice_count": sample_count,
        "valid_slice_count": valid_slices,
        "empty_slice_count": empty_slices,
        "section_failure_count": section_failures,
        "partial_wall_slice_count": partial_wall_slices,
        "completed_wall_passes": completed_wall_passes,
        "requested_wall_passes": requested_wall_passes,
        "sample_step_mm": sample_step,
        "raw_sampled_volume_mm3": sampled_volume,
        "integration_error_fraction": integration_error,
        "normalization_factor": normalization,
    }


def estimate_print_structure(
    model_obj,
    build_direction,
    walls,
    line_width_mm,
    infill_percent,
    enabled=True,
    layer_height_mm=0.20,
    max_slice_samples=48,
    progress_callback=None,
    yield_callback=None,
):
    """Estimate dense perimeters + sparse infill with slicer-style slices.

    v0.3.3 keeps the layer-sliced estimator on demand and defaults to a balanced representative-slice budget so difficult orientations do not monopolize the FreeCAD UI. The actual CAD solid is
    sampled perpendicular to the captured build direction. On each sampled
    print layer, PrintFEA reconstructs the real cross-section (including holes
    and islands) and applies one 2-D inward offset per requested wall. The
    remaining 2-D core is integrated through the model and only that core is
    homogenized at the entered infill percentage.

    This avoids the old opposing-wall double-counting problem and is much
    closer to how an FDM slicer reasons about perimeters. If a particular wall
    offset fails, PrintFEA conservatively keeps the last valid core on that
    slice; if slicing fails entirely, it grants *no* perimeter-shell benefit
    rather than reverting to the optimistic legacy side-area estimator.
    """
    walls = max(0, int(walls or 0))
    line_width_mm = max(0.0, float(line_width_mm or 0.0))
    layer_height_mm = max(0.01, float(layer_height_mm or 0.20))
    infill = _clamp(float(infill_percent or 0.0) / 100.0)
    wall_thickness = walls * line_width_mm

    base = {
        "enabled": bool(enabled),
        "method": "layer-sliced 2D perimeter/core integration" if enabled else "fully-dense legacy",
        "geometry_method": "TopoShape.slice + incremental 2D perimeter offsets" if enabled else "disabled",
        "walls": walls,
        "line_width_mm": line_width_mm,
        "layer_height_mm": layer_height_mm,
        "infill_fraction": infill,
        "wall_thickness_mm": wall_thickness,
        "source_volume_mm3": 0.0,
        "core_volume_mm3": 0.0,
        "shell_volume_mm3": 0.0,
        "shell_fraction": 0.0,
        "effective_material_fraction": 1.0,
        "stiffness_scale": 1.0,
        "strength_scale": 1.0,
        "fallback_used": False,
        "fallback_reason": "",
        "scaling_disabled": False,
        "estimated_layer_count": 0,
        "sampled_slice_count": 0,
        "valid_slice_count": 0,
        "empty_slice_count": 0,
        "section_failure_count": 0,
        "partial_wall_slice_count": 0,
        "completed_wall_passes": 0,
        "requested_wall_passes": 0,
        "sample_step_mm": 0.0,
        "integration_error_fraction": 0.0,
        "normalization_factor": 1.0,
    }
    if not enabled:
        return base
    if model_obj is None or not hasattr(model_obj, "Shape"):
        return base
    try:
        shape = model_obj.Shape
        volume = float(shape.Volume)
    except Exception:
        return base
    if volume <= 1e-9:
        return base

    base["source_volume_mm3"] = volume
    n = _normalized_tuple(build_direction)
    if n is None:
        base.update({
            "method": "build direction unavailable; conservative no-shell estimate",
            "geometry_method": "no build direction",
            "fallback_used": True,
            "fallback_reason": "Capture the BUILD PLATE / BOTTOM face to enable layer-sliced wall geometry.",
            "core_volume_mm3": volume,
            "shell_volume_mm3": 0.0,
            "shell_fraction": 0.0,
        })
    elif walls <= 0 or line_width_mm <= 1e-9:
        base.update({
            "core_volume_mm3": volume,
            "shell_volume_mm3": 0.0,
            "shell_fraction": 0.0,
            "estimated_layer_count": max(1, int(__import__('math').ceil(max(shape.BoundBox.XLength, shape.BoundBox.YLength, shape.BoundBox.ZLength) / layer_height_mm))),
        })
    else:
        try:
            cache_key = _slice_cache_key(
                model_obj, shape, n, walls, line_width_mm, layer_height_mm, max_slice_samples
            )
            geometry = _SLICE_CORE_CACHE.get(cache_key)
            if geometry is None:
                geometry = _slice_shell_core_geometry(
                    model_obj,
                    shape,
                    n,
                    walls,
                    line_width_mm,
                    layer_height_mm,
                    volume,
                    max_samples=max_slice_samples,
                    progress_callback=progress_callback,
                    yield_callback=yield_callback,
                )
                _SLICE_CORE_CACHE[cache_key] = dict(geometry)
            base.update(dict(geometry))
            partial = int(base.get("partial_wall_slice_count", 0))
            section_failures = int(base.get("section_failure_count", 0))
            if partial or section_failures:
                base["fallback_used"] = True
                notes = []
                if partial:
                    notes.append(
                        f"{partial} sampled slice(s) could not complete every requested wall offset; the last valid core was retained conservatively"
                    )
                if section_failures:
                    notes.append(
                        f"{section_failures} slice plane(s) could not be reconstructed and received no shell credit"
                    )
                base["fallback_reason"] = "; ".join(notes)
        except Exception as exc:
            # Conservative global recovery: give the part no dense-perimeter
            # credit and homogenize the entire CAD solid at the entered infill.
            base.update({
                "method": "layer slicing unavailable; conservative no-shell fallback",
                "geometry_method": "slice calculation failed",
                "fallback_used": True,
                "fallback_reason": str(exc),
                "core_volume_mm3": volume,
                "shell_volume_mm3": 0.0,
                "shell_fraction": 0.0,
            })

    shell_fraction = _clamp(float(base.get("shell_fraction", 0.0)))

    # Dense perimeters are credited as continuous material. Only the remaining
    # cross-sectional core receives sparse-infill homogenization. The nonlinear
    # exponents intentionally penalize sparse infill stiffness slightly more
    # than strength; these are still screening approximations, not pattern-
    # specific coupon data.
    effective_material = shell_fraction + (1.0 - shell_fraction) * infill
    stiffness_scale = shell_fraction + (1.0 - shell_fraction) * (infill ** 1.35)
    strength_scale = shell_fraction + (1.0 - shell_fraction) * (infill ** 1.10)

    stiffness_scale = max(stiffness_scale, 0.02)
    strength_scale = max(strength_scale, 0.02)

    base.update({
        "effective_material_fraction": effective_material,
        "stiffness_scale": stiffness_scale,
        "strength_scale": strength_scale,
    })
    return base

def apply_print_structure_to_material(material, structure):
    """Return an isotropic base material adjusted for wall/infill structure."""
    adjusted = dict(material)
    if not structure or not structure.get("enabled"):
        return adjusted
    stiffness = float(structure.get("stiffness_scale", 1.0))
    strength = float(structure.get("strength_scale", 1.0))
    density_scale = float(structure.get("effective_material_fraction", 1.0))
    adjusted["youngs_modulus_mpa"] = float(material["youngs_modulus_mpa"]) * stiffness
    adjusted["allowable_mpa"] = float(material["allowable_mpa"]) * strength
    adjusted["density_kg_m3"] = float(material["density_kg_m3"]) * density_scale
    adjusted["name"] = material["name"] + " + wall/infill homogenization"
    return adjusted


def layer_aware_properties(material, structure=None):
    """Return orthotropic engineering constants and screening allowables.

    Local material axes are:
      1, 2 = within the print layer plane
      3    = build / layer-stack direction

    Values are in MPa except Poisson ratios. If a wall/infill structure estimate
    is supplied, stiffness and strength are reduced from the 100%-structural-fill
    baseline before the directional ratios are applied.
    """
    stiffness_scale = float((structure or {}).get("stiffness_scale", 1.0))
    strength_scale = float((structure or {}).get("strength_scale", 1.0))

    e_xy = float(material["youngs_modulus_mpa"]) * stiffness_scale
    e_z = e_xy * float(material.get("z_modulus_ratio", 0.55))
    nu12 = float(material.get("poisson_ratio", 0.35))
    # A modest cross-plane Poisson ratio is used for the generic screening
    # model. The reciprocal ratios are handled internally by CalculiX.
    nu13 = nu23 = min(0.30, nu12)
    g12 = e_xy / (2.0 * (1.0 + nu12))
    g13 = g23 = g12 * float(material.get("shear_z_modulus_ratio", 0.50))

    allow_xy = float(material["allowable_mpa"]) * strength_scale
    allow_z = allow_xy * float(material.get("z_allowable_ratio", 0.50))
    allow_shear_xy = allow_xy * float(material.get("shear_xy_allowable_ratio", 0.60))
    allow_shear_z = allow_xy * float(material.get("shear_z_allowable_ratio", 0.42))

    return {
        "E1": e_xy,
        "E2": e_xy,
        "E3": e_z,
        "nu12": nu12,
        "nu13": nu13,
        "nu23": nu23,
        "G12": g12,
        "G13": g13,
        "G23": g23,
        "allow_xy": allow_xy,
        "allow_z": allow_z,
        "allow_shear_xy": allow_shear_xy,
        "allow_shear_z": allow_shear_z,
        "structure": dict(structure or {}),
    }
