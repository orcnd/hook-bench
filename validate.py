# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy",
#   "trimesh",
#   "shapely>=2.1",
#   "scipy",
#   "networkx",
#   "pyyaml",
# ]
# ///
"""Validate a hook-bench hook STL against the constraints file.

Usage:
    uv run validate.py output/<model>/hookv1.stl [--config constraints_v2_3.yaml] [--json]

Exit code 0 = valid, 1 = invalid, 2 = could not read input.
The plain-text report is what gets pasted back to the model as feedback.
A valid design also gets <name>_print.stl next to it: laid flat (5 mm thickness along Z),
centered, ready to drag into the slicer.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import shapely
import trimesh
import yaml
from shapely.geometry import MultiPolygon, Point, Polygon, box

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "constraints_v2_3.yaml"


def load_cfg(path: Path = DEFAULT_CONFIG) -> dict:
    return yaml.safe_load(Path(path).read_text())


def _parts(geom) -> list:
    if geom.is_empty:
        return []
    if hasattr(geom, "geoms"):
        return [g for g in geom.geoms if not g.is_empty]
    return [geom]


def _at(geom) -> str:
    p = geom.representative_point()
    return f"(x={p.x:.1f}, z={p.y:.1f})"


# ---------------------------------------------------------------- profile


def section_profile(mesh: trimesh.Trimesh, t: float) -> Polygon | MultiPolygon | None:
    """Cross-section at mid-thickness, as a shapely polygon in (x, z)."""
    sec = mesh.section(plane_origin=[0, t / 2, 0], plane_normal=[0, 1, 0])
    if sec is None:
        return None
    loops = [Polygon(d[:, [0, 2]]) for d in sec.discrete if len(d) >= 4]
    loops = [p if p.is_valid else shapely.make_valid(p) for p in loops]
    loops = sorted((p for p in loops if p.area > 1e-6), key=lambda p: p.area, reverse=True)
    if not loops:
        return None
    # Even-odd: a loop inside an odd number of others is a hole.
    result = None
    for p in loops:
        depth = sum(1 for q in loops if q is not p and q.area > p.area and q.contains(p.representative_point()))
        result = p if result is None else (result.difference(p) if depth % 2 else result.union(p))
    return result


def check_profile(profile, cfg: dict) -> tuple[list, dict]:
    """2D rules. Returns (checks, metrics); each check is (name, ok, detail)."""
    checks: list = []
    metrics: dict = {}

    def add(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    bodies = _parts(profile)
    add("profile.single_region", len(bodies) == 1,
        "one connected profile" if len(bodies) == 1 else f"cross-section has {len(bodies)} separate regions")
    if len(bodies) != 1:
        return checks, metrics
    prof: Polygon = bodies[0]

    metrics["profile_area_mm2"] = round(prof.area, 2)
    metrics["perimeter_mm"] = round(prof.length, 2)

    # keep-in
    (x0, x1), (z0, z1) = cfg["keep_in"]["x"], cfg["keep_in"]["z"]
    bx = prof.bounds
    inside = bx[0] >= x0 - 1e-3 and bx[2] <= x1 + 1e-3 and bx[1] >= z0 - 1e-3 and bx[3] <= z1 + 1e-3
    add("keep_in", inside, f"profile bounds x[{bx[0]:.2f}, {bx[2]:.2f}] z[{bx[1]:.2f}, {bx[3]:.2f}]"
        f" vs allowed x[{x0}, {x1}] z[{z0}, {z1}]")

    # eye: exactly one hole of the given diameter, with a solid ring around it
    eye = cfg["eye"]
    holes = list(prof.interiors)
    add("holes.count", len(holes) == cfg["holes_allowed"], f"{len(holes)} hole(s), allowed {cfg['holes_allowed']}")
    if holes:
        c = Point(eye["center"])
        hole_polys = [Polygon(h) for h in holes]
        hp = min(hole_polys, key=lambda h: h.centroid.distance(c))
        r_lo = (eye["hole_dia"] - eye["hole_tol"]) / 2
        r_hi = (eye["hole_dia"] + eye["hole_tol"]) / 2
        ok = c.buffer(r_lo, quad_segs=64).within(hp) and hp.within(c.buffer(r_hi, quad_segs=64))
        dists = [c.distance(Point(p)) for p in hp.exterior.coords]
        add("eye.hole", ok, f"hole radius range {min(dists):.3f}-{max(dists):.3f} mm, "
            f"needs {r_lo:.3f}-{r_hi:.3f} around {tuple(eye['center'])}")
        metrics["eye_hole_dia_mm"] = round(2 * float(np.mean(dists)), 3)
        r_ring = eye["hole_dia"] / 2 + eye["ring_min_width"]
        ring = c.buffer(r_ring, quad_segs=64).difference(c.buffer(r_hi, quad_segs=64))
        missing = ring.difference(prof).area
        add("eye.ring", missing < 0.05,
            f"{missing:.2f} mm^2 missing from the required solid ring r = {r_hi:.2f}..{r_ring:.2f} mm")

    # pin keep-out and contact
    pin = cfg["pin"]
    pc = Point(pin["center"])
    intr = prof.intersection(pc.buffer(pin["keep_out_dia"] / 2, quad_segs=64)).area
    add("pin.keep_out", intr < 1e-4, f"{intr:.3f} mm^2 of material inside Ø{pin['keep_out_dia']} around pin center")
    d1 = prof.distance(Point(pin["contact"]))
    add("pin.contact_P1", d1 <= pin["contact_tol"],
        f"material is {d1:.3f} mm from P1 {tuple(pin['contact'])}, max {pin['contact_tol']}")

    # insertion: a disk must reach the seat from outside the keep-in box
    ins = cfg["insertion"]
    r = ins["disk_dia"] / 2
    obstacle = prof.buffer(r, quad_segs=32)
    big = box(x0 - 3 * r - 5, z0 - 3 * r - 5, x1 + 3 * r + 5, z1 + 3 * r + 5)
    free = big.difference(obstacle)
    corner = Point(big.bounds[0] + 1, big.bounds[1] + 1)
    outer = next((g for g in _parts(free) if g.contains(corner)), None)
    dseat = outer.distance(pc) if outer is not None else float("inf")
    add("pin.insertable", dseat <= ins["seat_tol"],
        f"a Ø{ins['disk_dia']} disk coming from outside gets within {dseat:.2f} mm of the pin center, "
        f"needs ≤ {ins['seat_tol']}")

    # band: max inscribed circle, outside the eye zone
    band = cfg["band"]
    outside_eye = prof.difference(Point(eye["center"]).buffer(eye["zone_radius"], quad_segs=64))
    mics = [shapely.maximum_inscribed_circle(g, tolerance=0.002) for g in _parts(outside_eye)]
    mic = max(mics, key=lambda m: m.length)
    mic_d = 2 * mic.length
    metrics["max_inscribed_dia_mm"] = round(mic_d, 3)
    center = Point(mic.coords[0])
    add("band.max_inscribed", mic_d <= band["max_inscribed_dia"] + 0.01,
        f"largest inscribed circle Ø{mic_d:.2f} at (x={center.x:.1f}, z={center.y:.1f}), max Ø{band['max_inscribed_dia']}"
        f" (not checked within {eye['zone_radius']} mm of the eye center)")

    # band: min width (morphological opening)
    rr = band["min_width"] / 2 - 0.02
    opened = prof.buffer(-rr, quad_segs=16).buffer(rr, quad_segs=16)
    thin = [g for g in _parts(prof.difference(opened)) if g.area > 0.3]
    op_parts = _parts(opened)
    neck = len(op_parts) != 1 or len(op_parts[0].interiors) != len(holes)
    detail = "; ".join(f"{g.area:.2f} mm^2 near {_at(g)}" for g in thin[:5])
    if neck:
        detail = ("a neck narrower than the minimum splits the part or opens the eye. " + detail).strip()
    add("band.min_width", not thin and not neck, detail or f"all sections ≥ {band['min_width']} mm")

    # inner corners / narrow gaps (morphological closing)
    rc = band["min_inner_radius"] - 0.02
    closed = prof.buffer(rc, quad_segs=16).buffer(-rc, quad_segs=16)
    sharp = [g for g in _parts(closed.difference(prof)) if g.area > 0.01]
    add("band.inner_radius", not sharp,
        "; ".join(f"sharp concave corner or gap < {2 * band['min_inner_radius']} mm near {_at(g)}" for g in sharp[:5])
        or f"all concave corners ≥ R{band['min_inner_radius']}")

    return checks, metrics


# ---------------------------------------------------------------- mesh


def validate_mesh(mesh: trimesh.Trimesh, cfg: dict) -> tuple[list, dict]:
    checks: list = []
    metrics: dict = {}

    def add(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    t, tol = cfg["thickness_y"], cfg["thickness_tol"]
    bodies = mesh.split(only_watertight=False)
    add("mesh.single_hook", len(bodies) == 1,
        "exactly one hook" if len(bodies) == 1 else
        f"the STL must contain exactly one hook (one solid body); found {len(bodies)} separate bodies")
    add("mesh.watertight", mesh.is_watertight, "closed manifold" if mesh.is_watertight else "holes or non-manifold edges")
    add("mesh.normals", mesh.is_winding_consistent and mesh.volume > 0,
        "consistent, outward" if mesh.is_winding_consistent and mesh.volume > 0 else "inconsistent or inward normals")
    ntri = len(mesh.faces)
    metrics["triangles"] = ntri
    add("mesh.triangles", ntri <= cfg["max_triangles"], f"{ntri} triangles, max {cfg['max_triangles']}")

    # 2.5D: every vertex on Y=0 or Y=t, every face either a cap (±Y) or a side wall (normal ⟂ Y)
    y = mesh.vertices[:, 1]
    on_planes = np.minimum(np.abs(y), np.abs(y - t)) <= tol
    ny = np.abs(mesh.face_normals[:, 1])
    faces_ok = (ny > 0.9999) | (ny < 1e-3)
    add("form.extruded_2p5d", on_planes.all() and faces_ok.all(),
        f"Y range [{y.min():.3f}, {y.max():.3f}] (needs 0 and {t}); "
        f"{int((~on_planes).sum())} off-plane vertices, {int((~faces_ok).sum())} slanted faces")
    add("form.thickness", abs(y.min()) <= tol and abs(y.max() - t) <= tol, f"thickness {y.max() - y.min():.3f} mm")

    vol = float(mesh.volume)
    surf = float(mesh.area)
    metrics["volume_mm3"] = round(vol, 1)
    metrics["surface_mm2"] = round(surf, 1)
    metrics["est_mass_g"] = round(vol / 1000 * cfg["pla_density_g_cm3"], 3)
    v0, v1 = cfg["volume_mm3"]
    add("budget.volume", v0 <= vol <= v1, f"{vol:.1f} mm^3, allowed {v0}-{v1}")
    add("budget.surface", surf <= cfg["surface_area_mm2_max"], f"{surf:.1f} mm^2, max {cfg['surface_area_mm2_max']}")

    profile = section_profile(mesh, t)
    if profile is None:
        add("profile.section", False, "could not take a cross-section at Y = t/2")
        return checks, metrics
    pchecks, pmetrics = check_profile(profile, cfg)
    checks += pchecks
    metrics.update(pmetrics)
    return checks, metrics


def report(path: str, checks: list, metrics: dict, cfg: dict) -> str:
    fails = [c for c in checks if not c[1]]
    lines = [f"hook-bench validate (constraints v{cfg['version']})  file: {path}", ""]
    w = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        lines.append(f"[{'PASS' if ok else 'FAIL'}] {name.ljust(w)}  {detail}")
    lines += ["", "METRICS  " + "  ".join(f"{k}={v}" for k, v in metrics.items()), ""]
    lines.append("RESULT: VALID" if not fails else f"RESULT: INVALID ({len(fails)} failed check(s))")
    return "\n".join(lines)


def write_print_copy(mesh: trimesh.Trimesh, src: Path) -> Path:
    """Lay the hook flat for printing: design Y (thickness) becomes Z, profile on the bed."""
    m = mesh.copy()
    m.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))  # (x,y,z)->(x,z,-y)
    lo, hi = m.bounds
    m.apply_translation([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
    out = src.with_name(src.stem + "_print.stl")
    m.export(out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stl")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    try:
        mesh = trimesh.load_mesh(args.stl, process=True)
    except Exception as e:  # noqa: BLE001
        print(f"could not read {args.stl}: {e}", file=sys.stderr)
        return 2
    checks, metrics = validate_mesh(mesh, cfg)
    valid = all(ok for _, ok, _ in checks)
    if valid and not Path(args.stl).stem.endswith("_print"):
        metrics["print_file"] = str(write_print_copy(mesh, Path(args.stl)))
    if args.json:
        print(json.dumps({"file": args.stl, "valid": valid, "metrics": metrics,
                          "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks]}, indent=2))
    else:
        print(report(args.stl, checks, metrics, cfg))
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main())
