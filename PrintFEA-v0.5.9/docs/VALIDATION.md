# Validation Notes

PrintFEA is a screening tool, so validation is split into two questions:

1. Does the geometry/setup pipeline behave consistently?
2. Do the built-in generic material assumptions exactly reproduce a specific real print?

The first can be tested directly. The second requires printer/filament/process-specific experimental data and is intentionally **not** claimed by the built-in generic presets.

## Analytical shell/core check

The repository includes `examples/create_test_bar.py`, which creates a `100 × 10 × 10 mm` solid bar.

Assume:

- build direction perpendicular to the `100 × 10 mm` layer cross-section;
- wall line width = `0.42 mm`;
- infill = `40%`.

Total CAD volume:

```text
100 × 10 × 10 = 10,000 mm³
```

### Four walls

Requested wall depth:

```text
4 × 0.42 = 1.68 mm
```

Remaining core cross-section:

```text
(100 - 2×1.68) × (10 - 2×1.68)
= 96.64 × 6.64
= 641.6896 mm²
```

Core volume:

```text
641.6896 × 10 = 6,416.896 mm³
```

Dense shell fraction:

```text
1 - 6416.896/10000 = 35.83104%
```

Effective material fraction at 40% infill:

```text
35.83104% + 0.40 × 64.16896%
= 61.498624%
```

PrintFEA development testing reported approximately **61% effective material**, matching the analytical result to display precision.

### Eight walls

Requested wall depth:

```text
8 × 0.42 = 3.36 mm
```

Remaining core cross-section:

```text
(100 - 2×3.36) × (10 - 2×3.36)
= 93.28 × 3.28
= 305.9584 mm²
```

Core volume:

```text
305.9584 × 10 = 3,059.584 mm³
```

Dense shell fraction:

```text
1 - 3059.584/10000 = 69.40416%
```

Effective material fraction at 40% infill:

```text
69.40416% + 0.40 × 30.59584%
= 81.642496%
```

PrintFEA development testing reported approximately **81.6% effective material** and a core volume of approximately **3059.6 mm³**, matching the analytical geometry result.

## What this validates

This test provides evidence that the layer-sliced wall/core geometry calculation can reproduce an analytically known simple case.

It does **not** prove that:

- a 61.5% effective material fraction means exactly 61.5% of solid-part strength for every infill pattern;
- the generic ASA/PETG/PLA/ABS/PA profiles match a specific spool/printer/settings combination;
- the homogenized continuum captures seams, voids, raster paths, skin layers, print defects, or nonlinear failure.

## Recommended release smoke test

Before tagging a release:

1. Create the validation bar.
2. Run 4 walls / 40% infill and confirm effective material is approximately 61.5%.
3. Run 8 walls / 40% infill and confirm effective material is approximately 81.6%.
4. Confirm increased wall count reduces predicted movement/increases margin for an otherwise identical load case.
5. Verify Normal/Fine representative utilization is reasonably stable.
