# hook-bench — Methodology

AI models design a J-hook through Blender MCP. The hooks are printed in PLA, their breaking load is measured, and the
result is compared with an FEA prediction.

## 1. Files

| File | Purpose |
|---|---|
| `constraints_v2_3.yaml` | All constraints (single source of truth) |
| `prompts/hook_v2_3.md` | The prompt given to the models verbatim |
| `blender/base_scene.blend` | Starting scene: mm units, hidden `REF` collection |
| `validate.py` | STL rule check; writes `_print.stl` when the design is valid |
| `analyze.py` + `fea_material.yaml` | 2D FEA: failure location and predicted breaking load |
| `calibration/` | Human-designed reference hooks (weak / strong) |
| `print/hook-bench PLA 0.20 solid @FF AD5X.json` | Flash Studio print profile |
| `output/<model>/hookv1.stl` | Model outputs |

## 2. Design round (per model)

1. Open `blender/base_scene.blend` in Blender. Only one MCP client should be connected.
2. Give the model the content of `prompts/hook_v2_3.md` verbatim, **in a new conversation**.
3. The model writes `output/<model-name-version>/hookv1.stl`. Every revision overwrites the same file.
   - The model picks the folder name itself. Check it and correct it if needed.
   - Intermediate iterations are lost because they are overwritten. Record the model and the number of rounds in
     `results.csv`, and save the conversation log to `output/<model>/transcript.md`.
4. Validate:
   ```bash
   uv run validate.py output/<model>/hookv1.stl
   ```
   - If the result is `RESULT: INVALID`, paste the report to the model **verbatim**. Give no other hints.
   - At most 3 revision rounds. If the design is still invalid after 3 rounds, it is recorded as "invalid".
5. For a valid design, `output/<model>/hookv1_print.stl` is created automatically. This is the file that gets printed.
6. At least 3 independent sessions per model. Use a separate folder for each session, e.g.
   `output/claude-opus-5.5-s2/`.

## 3. FEA prediction

```bash
uv run analyze.py output/*/hookv1.stl calibration/calib_*.stl
```

- 2D plane stress, linear elastic, quadratic triangle elements (scikit-fem).
- Boundary conditions:
  - The screw holds the top ±35° arc of the eye hole.
  - The pin loads the bottom of the hook bowl with radial pressure.
- Failure criterion: maximum principal (tensile) stress reaches `sigma_t`. PLA behaves brittle, which is why this
  criterion was chosen. The eye zone (8 mm from its centre) is excluded from the search.
- Two outputs per STL:
  - `*_fea.png`: stress map, failure point, shape at the moment of failure
  - `*_fea.json`: numerical results
- **Calibration:** Once the reference hooks have been tested, pass in the measured values:
  `uv run analyze.py calibration/calib_weak.stl calibration/calib_strong.stl --measured-kg <weak> <strong>`.
  Write the mean of the two `sigma_t fit` values into `fea_material.yaml`. Predictions for the AI designs are made
  with this calibrated value.
- Limitations:
  - Large deformation is not modelled (the hook opens up under load).
  - Layer adhesion is not modelled.
  - The pin–hook contact is idealised.
  - If needed, FreeCAD FEM + CalculiX (open source) can be used for 3D verification.

## 4. Printing (Flashforge AD5X + Flash Studio)

**Profile:** Installed in Flash Studio's user presets. To use it on another computer, import the JSON file in the
`print/` folder via *File → Import → Import Configs*.

| Selection | Value |
|---|---|
| Printer | Flashforge AD5X 0.4 nozzle |
| Filament | Flashforge PLA Basic @FF AD5X (nozzle 210 °C, first layer 215 °C, bed 60 °C) |
| Process profile | **hook-bench PLA 0.20 solid @FF AD5X** |

Differences from the base AD5X 0.20 profile:

| Setting | Value | Why |
|---|---|---|
| Layer / first layer | 0.20 / 0.20 mm | 5 mm = exactly 25 layers |
| Walls | 2 | The rest of the band is filled by infill |
| Infill | 100%, rectilinear, 45° (±45° alternating between layers) | Infill density is not a variable |
| Top / bottom shells | 5 / 5 | No effect at 100% infill, kept fixed |
| Seam | back | The seam is in the same place on every part |
| Support / brim / prime tower / ironing | off | The hook prints flat and needs no support |
| Speed | Outer wall 60, inner wall and infill 80, first layer 30 mm/s | No speed-dependent quality differences |

**Workflow:**
1. In Flash Studio, select the printer, filament and process profile.
2. Drag in the `*_print.stl` file. The part is already lying flat: the 5 mm thickness is along Z, the profile is on
   the bed. Do **not** rotate or scale it.
3. One plate = 5 copies of one design. Place the copies randomly, and randomise the print order as well.
4. Filament: one spool, one colour, dried at 50 °C for 6 hours before printing. Use only one slot of the AD5X
   multi-colour system (IFS).
5. After printing, weigh (0.01 g) and label each specimen. If a specimen's mass deviates from the design's median by
   more than 5%, it counts as a print defect and is reprinted.
6. Condition the parts at room temperature for 48 hours before testing.

## 5. Breaking test

- Tare the scale with the bottle (~5 kg of water) standing on it.
- The pin is tied to the bottle neck with string at both ends. The hook carries the pin from below.
- The M5 screw passes through the eye; pull upward on the string **slowly** (about 0.1 kg/s).
- The camera records the scale display and the hook in the same frame (60 fps).
- Breaking load = the lowest (most negative) reading just before the hook breaks or the pin slips out.
- If the bottle lifts off, record the result as "≥ bottle weight" (censored data).
- Wear safety glasses.

## 6. Recording

`results.csv`:
```
model, session, specimen, valid, revisions, mass_g, fea_pred_kg, model_pred_N, measured_kg, break_location, notes
```
