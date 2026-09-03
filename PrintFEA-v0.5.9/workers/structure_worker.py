"""Headless PrintFEA wall/infill structure estimator.

v0.4.2 executes representative slices concurrently on Linux using forked
worker processes inside the already-headless FreeCADCmd process.  Each slice
has a watchdog: if an individual OCCT slice/offset operation exceeds the
configured time limit it is terminated and that slice is treated
conservatively (no additional dense-shell credit).
"""
import json
import math
import multiprocessing as mp
import os
import sys
import time
import traceback

# Globals inherited by forked slice workers. They are read-only in children.
_SHAPE = None
_NORMAL = None
_WALLS = 0
_LINE_WIDTH = 0.42


def _emit_progress(done, total):
    try:
        print(f"PRINTFEA_PROGRESS:{int(done)}:{int(total)}", flush=True)
    except Exception:
        pass


def _write_result(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True)
    os.replace(tmp, path)


def _slice_child(index, distance, queue):
    """Process one representative slice and return area/core information."""
    try:
        from materials.library import _make_slice_face, _core_area_after_walls
        wires = _SHAPE.slice(_NORMAL, float(distance))
        face = _make_slice_face(wires)
        if face is None:
            queue.put(("empty", int(index)))
            return
        area = float(face.Area)
        if area <= 1e-10:
            queue.put(("empty", int(index)))
            return
        # Send the section area before doing offsets. If an offset later hangs,
        # the parent can terminate this child and conservatively retain the full
        # section as sparse core (zero shell credit for this slice).
        queue.put(("area", int(index), area))
        try:
            core_area, passes, partial = _core_area_after_walls(
                face, area, _WALLS, _LINE_WIDTH, yield_callback=None
            )
        except Exception:
            core_area, passes, partial = area, 0, True
        queue.put(("done", int(index), area, float(core_area), int(passes), bool(partial)))
    except BaseException as exc:
        try:
            queue.put(("error", int(index), str(exc)))
        except Exception:
            pass


def _parallel_geometry(shape, build_direction, walls, line_width_mm, layer_height_mm,
                       volume, max_samples, workers, timeout_s):
    import FreeCAD as App
    from materials.library import _projected_bounds, _clamp

    dmin, dmax, n = _projected_bounds(shape, build_direction)
    span = max(0.0, dmax - dmin)
    h = max(1e-4, float(layer_height_mm or 0.20))
    if span <= 1e-9:
        raise RuntimeError("Model has no measurable thickness along the captured build direction.")

    physical_layers = max(1, int(math.ceil(span / h)))
    sample_count = max(1, min(int(max_samples), physical_layers))
    sample_step = span / float(sample_count)
    distances = [dmin + (i + 0.5) * sample_step for i in range(sample_count)]

    # Fork is intentional: the child inherits FreeCAD/OCCT and the imported
    # B-Rep without attempting to pickle TopoShape objects.
    try:
        ctx = mp.get_context("fork")
    except ValueError:
        raise RuntimeError("Parallel structure slicing currently requires a platform with fork support.")

    global _SHAPE, _NORMAL, _WALLS, _LINE_WIDTH
    _SHAPE = shape
    _NORMAL = App.Vector(*n)
    _WALLS = int(walls)
    _LINE_WIDTH = float(line_width_mm)

    worker_count = max(1, min(int(workers), sample_count))
    timeout_s = max(2.0, float(timeout_s))
    queue = ctx.Queue()
    pending = list(range(sample_count))
    active = {}
    areas_seen = {}
    results = {}
    completed = 0
    timed_out = 0

    def launch_one(idx):
        proc = ctx.Process(target=_slice_child, args=(idx, distances[idx], queue))
        proc.daemon = True
        proc.start()
        active[idx] = {"proc": proc, "started": time.monotonic(), "area": None}

    def finish_idx(idx, result):
        nonlocal completed
        entry = active.pop(idx, None)
        if entry:
            try:
                entry["proc"].join(timeout=0.2)
            except Exception:
                pass
        results[idx] = result
        completed += 1
        _emit_progress(completed, sample_count)

    while pending or active:
        while pending and len(active) < worker_count:
            launch_one(pending.pop(0))

        # Drain all currently available worker messages.
        while True:
            try:
                msg = queue.get_nowait()
            except Exception:
                break
            kind, idx = msg[0], int(msg[1])
            if idx not in active:
                continue
            if kind == "area":
                area = float(msg[2])
                active[idx]["area"] = area
                areas_seen[idx] = area
            elif kind == "done":
                _kind, _idx, area, core_area, passes, partial = msg
                finish_idx(idx, {
                    "kind": "done", "area": float(area), "core_area": float(core_area),
                    "passes": int(passes), "partial": bool(partial), "timeout": False,
                })
            elif kind == "empty":
                finish_idx(idx, {"kind": "empty", "timeout": False})
            elif kind == "error":
                area = active[idx].get("area")
                finish_idx(idx, {
                    "kind": "error", "area": area, "error": str(msg[2]), "timeout": False,
                })

        now = time.monotonic()
        for idx, entry in list(active.items()):
            proc = entry["proc"]
            if not proc.is_alive():
                # Give queued final messages one more loop iteration to arrive.
                if now - entry["started"] < 0.3:
                    continue
                area = entry.get("area")
                finish_idx(idx, {"kind": "error", "area": area, "error": "slice worker exited without a final result", "timeout": False})
                continue
            if now - entry["started"] > timeout_s:
                try:
                    proc.terminate()
                    proc.join(timeout=0.75)
                    if proc.is_alive():
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        proc.join(timeout=0.75)
                except Exception:
                    pass
                timed_out += 1
                area = entry.get("area")
                # If area is known, full area as core = no shell credit. If the
                # slice itself hung before area was available, fill later using
                # the largest observed section area, also conservative.
                finish_idx(idx, {"kind": "timeout", "area": area, "timeout": True})

        time.sleep(0.025)

    valid_areas = [float(r.get("area")) for r in results.values() if r.get("area") is not None]
    conservative_missing_area = max(valid_areas) if valid_areas else 0.0

    sampled_volume = 0.0
    sampled_core_volume = 0.0
    valid_slices = 0
    empty_slices = 0
    section_failures = 0
    partial_wall_slices = 0
    completed_wall_passes = 0
    requested_wall_passes = 0

    for idx in range(sample_count):
        r = results.get(idx, {"kind": "error", "area": None})
        kind = r.get("kind")
        if kind == "empty":
            empty_slices += 1
            continue
        area = r.get("area")
        if area is None:
            section_failures += 1
            area = conservative_missing_area
            if area <= 1e-12:
                continue
            # Treat the approximated entire section as core: zero shell credit.
            core_area = area
            partial_wall_slices += 1
        elif kind == "done":
            core_area = min(float(area), max(0.0, float(r.get("core_area", area))))
            completed_wall_passes += int(r.get("passes", 0))
            if r.get("partial"):
                partial_wall_slices += 1
        else:
            section_failures += 1
            core_area = float(area)
            partial_wall_slices += 1

        valid_slices += 1
        sampled_volume += float(area) * sample_step
        sampled_core_volume += float(core_area) * sample_step
        requested_wall_passes += int(walls)

    if sampled_volume <= 1e-9 or valid_slices <= 0:
        raise RuntimeError("FreeCAD could not build usable planar cross-sections along the captured build direction.")

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
        "parallel_workers": worker_count,
        "timed_out_slice_count": timed_out,
        "slow_slice_timeout_seconds": timeout_s,
    }


def _build_structure(cfg, shape):
    from materials.library import _clamp, _normalized_tuple

    enabled = bool(cfg.get("enabled", True))
    walls = max(0, int(cfg.get("walls", 0)))
    line_width = max(0.0, float(cfg.get("line_width_mm", 0.42)))
    layer_height = max(0.01, float(cfg.get("layer_height_mm", 0.20)))
    infill = _clamp(float(cfg.get("infill_percent", 100)) / 100.0)
    volume = float(shape.Volume)
    n = _normalized_tuple(tuple(cfg.get("build_direction", (0, 0, 0))))

    base = {
        "enabled": enabled,
        "method": "parallel layer-sliced 2D perimeter/core integration" if enabled else "fully-dense legacy",
        "geometry_method": "parallel TopoShape.slice + incremental 2D perimeter offsets" if enabled else "disabled",
        "walls": walls,
        "line_width_mm": line_width,
        "layer_height_mm": layer_height,
        "infill_fraction": infill,
        "wall_thickness_mm": walls * line_width,
        "source_volume_mm3": volume,
        "core_volume_mm3": volume,
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
        "parallel_workers": 1,
        "timed_out_slice_count": 0,
        "slow_slice_timeout_seconds": float(cfg.get("slice_timeout_seconds", 12.0)),
    }
    if not enabled:
        return base
    if volume <= 1e-9:
        return base
    if n is None:
        base.update({
            "method": "build direction unavailable; conservative no-shell estimate",
            "geometry_method": "no build direction",
            "fallback_used": True,
            "fallback_reason": "Capture the BUILD PLATE / BOTTOM face to enable layer-sliced wall geometry.",
        })
    elif walls <= 0 or line_width <= 1e-9:
        pass
    else:
        workers = int(cfg.get("parallel_workers") or min(4, os.cpu_count() or 1))
        timeout_s = float(cfg.get("slice_timeout_seconds", 12.0))
        geom = _parallel_geometry(
            shape, n, walls, line_width, layer_height, volume,
            int(cfg.get("max_slice_samples", 48)), workers, timeout_s,
        )
        base.update(geom)
        partial = int(base.get("partial_wall_slice_count", 0))
        section_failures = int(base.get("section_failure_count", 0))
        timed_out = int(base.get("timed_out_slice_count", 0))
        if partial or section_failures or timed_out:
            base["fallback_used"] = True
            notes = []
            if timed_out:
                notes.append(f"{timed_out} slow slice(s) exceeded {timeout_s:g}s and were conservatively given no additional shell credit")
            if partial:
                notes.append(f"{partial} sampled slice(s) used conservative partial/no-shell treatment")
            if section_failures:
                notes.append(f"{section_failures} sampled slice(s) could not be fully reconstructed")
            base["fallback_reason"] = "; ".join(notes)

    shell_fraction = _clamp(float(base.get("shell_fraction", 0.0)))
    effective = shell_fraction + (1.0 - shell_fraction) * infill
    stiffness = max(shell_fraction + (1.0 - shell_fraction) * (infill ** 1.35), 0.02)
    strength = max(shell_fraction + (1.0 - shell_fraction) * (infill ** 1.10), 0.02)
    base.update({
        "effective_material_fraction": effective,
        "stiffness_scale": stiffness,
        "strength_scale": strength,
    })
    return base


def _config_input_path():
    value = os.environ.get("PRINTFEA_STRUCTURE_CONFIG", "").strip()
    if value:
        return os.path.abspath(value)
    if len(sys.argv) >= 2:
        return os.path.abspath(sys.argv[-1])
    return ""


def _run():
    input_path = _config_input_path()
    if not input_path:
        raise RuntimeError("PrintFEA structure worker did not receive an input JSON path.")
    with open(input_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    result_path = os.path.abspath(cfg["result_path"])
    plugin_root = os.path.abspath(cfg["plugin_root"])
    if plugin_root not in sys.path:
        sys.path.insert(0, plugin_root)

    import Part
    shape = Part.Shape()
    shape.importBrep(os.path.abspath(cfg["brep_path"]))
    structure = _build_structure(cfg, shape)
    _write_result(result_path, {"ok": True, "structure": structure})


try:
    _run()
except BaseException as exc:
    try:
        input_path = _config_input_path()
        result_path = ""
        if input_path and os.path.exists(input_path):
            with open(input_path, "r", encoding="utf-8") as fh:
                result_path = os.path.abspath(json.load(fh).get("result_path", ""))
        payload = {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}
        if result_path:
            _write_result(result_path, payload)
        print("PRINTFEA_ERROR:" + str(exc), flush=True)
    except Exception:
        print("PRINTFEA_ERROR:" + traceback.format_exc(), flush=True)
