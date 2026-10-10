# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = [
#   "numpy",
#   "scipy",
#   "scikit-fem>=10",
#   "triangle",
#   "matplotlib",
#   "shapely>=2.1",
#   "trimesh",
#   "networkx",
#   "pyyaml",
# ]
# ///
"""Predict where a hook breaks and at what load (2D plane-stress FEA, linear elastic).

Usage:
    uv run analyze.py calibration/calib_weak.stl calibration/calib_strong.stl
    uv run analyze.py designs/opus/run1/hook.stl --measured-kg 2.85

Model:
- The hook profile (cross-section at mid-thickness) is meshed with quadratic triangles;
  the part thickness comes from the constraints file.
- The screw holds the top of the eye hole (fixed over ±35° around P2).
- The pin pushes down on the bowl: radial pressure, cosine-weighted toward straight down,
  on the boundary within 0.35 mm of the pin surface, over a patch symmetric about straight
  down (as wide as the bowl allows on both sides, max ±60°), so the net force is vertical.
  Its resultant is the pull force F.
- Failure = max principal (tensile) stress reaches sigma_t (PLA is brittle). Stresses inside
  the eye zone are ignored: the screw contact is idealised there and the zone is
  over-dimensioned on purpose.
- Linear: the predicted load scales with sigma_t / sigma_max. Large deflections (the J
  opening up) are not modelled; calibrate sigma_t against the two calibration hooks.

Writes <stl>_fea.png (stress map) and <stl>_fea.json next to each input.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import matplotlib
import numpy as np
import triangle
import trimesh
import yaml

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402
from skfem import Basis, ElementTriP2, ElementVector, FacetBasis, LinearForm, MeshTri, asm, condense, solve  # noqa: E402
from skfem.helpers import sym_grad  # noqa: E402
from skfem.models.elasticity import lame_parameters, linear_elasticity  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from validate import _parts, load_cfg, section_profile  # noqa: E402

G = 9.81
F_REF = 10.0          # N; everything is linear, results are scaled from this
H_ELEM = 0.25         # target element size, mm (~9 quadratic elements across a 2.2 mm band)
EYE_FIX_DEG = 35.0
PIN_CONTACT_GAP = 0.35

# Colors (dataviz reference palette: blue <-> red diverging, gray midpoint, light surface)
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
DIVERGING = LinearSegmentedColormap.from_list("tension", ["#2a78d6", "#f0efec", "#e34948"])


def load_material(path: Path = HERE / "fea_material.yaml") -> dict:
    return yaml.safe_load(path.read_text())


def mesh_profile(prof: Polygon) -> MeshTri:
    prof = prof.simplify(0.005)
    verts, segs, holes = [], [], []

    def add_ring(coords):
        pts = np.asarray(coords)[:-1]
        n0, k = len(verts), len(pts)
        verts.extend(pts.tolist())
        segs.extend([[n0 + i, n0 + (i + 1) % k] for i in range(k)])

    add_ring(prof.exterior.coords)
    for ring in prof.interiors:
        add_ring(ring.coords)
        holes.append(Polygon(ring).representative_point().coords[0])
    data = {"vertices": np.array(verts), "segments": np.array(segs)}
    if holes:
        data["holes"] = np.array(holes)
    max_area = math.sqrt(3) / 4 * H_ELEM**2
    out = triangle.triangulate(data, f"pq30a{max_area:.6f}")
    return MeshTri(out["vertices"].T, out["triangles"].T)


def analyze(stl: Path, cfg: dict, mat: dict) -> dict:
    t = cfg["thickness_y"]
    mesh3d = trimesh.load_mesh(stl, process=True)
    prof = section_profile(mesh3d, t)
    if prof is None:
        raise ValueError("no cross-section")
    prof = max(_parts(prof), key=lambda p: p.area)
    m = mesh_profile(prof)

    E, nu = mat["E_mpa"], mat["nu"]
    lam, mu = lame_parameters(E, nu)
    lam = 2 * lam * mu / (lam + 2 * mu)          # plane stress
    basis = Basis(m, ElementVector(ElementTriP2()), intorder=4)
    K = asm(linear_elasticity(lam, mu), basis)    # per unit thickness

    pc = np.array(cfg["pin"]["center"], float)
    r_pin = cfg["pin"]["dia"] / 2
    ec = np.array(cfg["eye"]["center"], float)
    r_hole = cfg["eye"]["hole_dia"] / 2

    def on_pin(x):
        d = np.hypot(x[0] - pc[0], x[1] - pc[1])
        return (d < r_pin + PIN_CONTACT_GAP) & (x[1] < pc[1])

    def on_eye_top(x):
        d = np.hypot(x[0] - ec[0], x[1] - ec[1])
        return (np.abs(d - r_hole) < 0.2) & (x[1] - ec[1] > r_hole * math.cos(math.radians(EYE_FIX_DEG)))

    load_facets = m.facets_satisfying(on_pin, boundaries_only=True)
    fix_facets = m.facets_satisfying(on_eye_top, boundaries_only=True)
    if len(load_facets) == 0:
        raise ValueError("no boundary near the pin: the bowl does not touch the pin")
    # The pin settles where the net contact force is vertical: keep the contact patch
    # symmetric about straight down, as wide as the bowl allows on both sides (max ±60°).
    mid = m.p[:, m.facets[:, load_facets]].mean(axis=1)
    phi = np.degrees(np.arctan2(mid[0] - pc[0], -(mid[1] - pc[1])))   # 0 = straight down
    half = min(-phi.min(), phi.max(), 60.0)
    if half < 5.0:
        raise ValueError(f"the bowl does not cradle the pin from both sides (contact spans {phi.min():.0f}..{phi.max():.0f} deg)")
    load_facets = load_facets[np.abs(phi) <= half]
    if len(fix_facets) == 0:
        raise ValueError("no eye hole boundary found at the screw contact")

    @LinearForm
    def pin_pressure(v, w):
        dx, dz = w.x[0] - pc[0], w.x[1] - pc[1]
        r = np.sqrt(dx**2 + dz**2)
        p = np.clip(-dz / r, 0, None)               # 1 straight below the pin, 0 at the sides
        return p * (dx / r * v[0] + dz / r * v[1])  # pushes the material away from the pin

    f = asm(pin_pressure, FacetBasis(m, basis.elem, facets=load_facets, intorder=4))
    uz = np.zeros(basis.N)
    uz[basis.nodal_dofs[1]] = 1
    uz[basis.facet_dofs[1]] = 1
    f *= (F_REF / t) / abs(f @ uz)                  # resultant = F_REF over the full thickness

    u = solve(*condense(K, f, D=basis.get_dofs(fix_facets).all()))

    eps = sym_grad(basis.interpolate(u))
    tr = eps[0, 0] + eps[1, 1]
    s11 = 2 * mu * eps[0, 0] + lam * tr
    s22 = 2 * mu * eps[1, 1] + lam * tr
    s12 = 2 * mu * eps[0, 1]
    c, R = (s11 + s22) / 2, np.sqrt(((s11 - s22) / 2) ** 2 + s12**2)
    s1 = c + R                                      # max principal, MPa at F_REF
    xq = basis.mapping.F(basis.X)                   # quadrature point coordinates (2, nel, nqp)

    outside_eye = np.hypot(xq[0] - ec[0], xq[1] - ec[1]) > cfg["eye"]["zone_radius"]
    s1_masked = np.where(outside_eye, s1, -np.inf)
    iel, iq = np.unravel_index(np.argmax(s1_masked), s1.shape)
    s1_max = float(s1[iel, iq])
    hot = (float(xq[0, iel, iq]), float(xq[1, iel, iq]))

    scale = mat["sigma_t_mpa"] / s1_max             # F_fail / F_REF
    f_fail = F_REF * scale
    load_nodes = np.unique(m.facets[:, load_facets])
    pin_drop = -float(np.mean(u[basis.nodal_dofs[1, load_nodes]])) * scale
    pin_dx = float(np.mean(u[basis.nodal_dofs[0, load_nodes]])) * scale

    return {
        "file": str(stl),
        "mesh": m, "s1_elem": s1.mean(axis=1) * scale, "u": u * scale, "basis": basis,
        "hotspot_xz": hot,
        "sigma1_per_N_mpa": s1_max / F_REF,
        "pred_fail_N": f_fail,
        "pred_fail_kg": f_fail / G,
        "pin_drop_at_fail_mm": pin_drop,
        "pin_dx": pin_dx,
        "pivot_xz": tuple(cfg["eye"]["contact"]),
        "contact_xz": tuple(cfg["pin"]["contact"]),
        "elements": m.t.shape[1],
    }


def plot(res: dict, mat: dict, out: Path) -> None:
    m, basis, u = res["mesh"], res["basis"], res["u"]
    ux = u[basis.nodal_dofs[0]][: m.p.shape[1]]
    uz = u[basis.nodal_dofs[1]][: m.p.shape[1]]
    st = mat["sigma_t_mpa"]
    # The analysis holds the eye fixed; the real hook hangs freely from the screw and swings
    # until the pin is straight below it. Rotate the deformed shape about P2 to show that.
    (px, pz), (cx, cz) = res["pivot_xz"], res["contact_xz"]
    vx, vz = cx + res["pin_dx"] - px, cz - res["pin_drop_at_fail_mm"] - pz
    a = math.atan(vx / vz)                          # rotation that puts the pin back under P2
    xd, zd = m.p[0] + ux - px, m.p[1] + uz - pz
    rx, rz = px + xd * math.cos(a) - zd * math.sin(a), pz + xd * math.sin(a) + zd * math.cos(a)

    fig, axes = plt.subplots(1, 2, figsize=(9, 6.2), facecolor=SURFACE, sharey=True)
    titles = ("Undeformed", "At predicted failure, hanging from the screw (true scale)")
    for ax, (xs, zs), title in zip(axes, ((m.p[0], m.p[1]), (rx, rz)), titles):
        ax.set_facecolor(SURFACE)
        pc = ax.tripcolor(xs, zs, m.t.T, facecolors=res["s1_elem"],
                          cmap=DIVERGING, norm=TwoSlopeNorm(vmin=-st, vcenter=0, vmax=st), shading="flat")
        ax.set_aspect("equal")
        ax.set_title(title, color=INK_2, fontsize=10, loc="left")
        ax.tick_params(colors=INK_2, labelsize=8)
        for s in ax.spines.values():
            s.set_visible(False)
        ax.set_xlabel("x (mm)", color=INK_2, fontsize=9)
    axes[0].set_ylabel("z (mm)", color=INK_2, fontsize=9)

    hx, hz = res["hotspot_xz"]
    axes[0].plot(hx, hz, marker="o", ms=14, mfc="none", mec=INK, mew=2)
    axes[0].annotate("predicted break", (hx, hz), xytext=(hx - 14, hz + 8), color=INK, fontsize=9,
                     arrowprops={"arrowstyle": "-", "color": INK, "lw": 1})

    cb = fig.colorbar(pc, ax=axes, shrink=0.7, pad=0.02)
    cb.set_label("max principal stress at failure load (MPa)\nred = tension, blue = compression",
                 color=INK_2, fontsize=9)
    cb.ax.tick_params(colors=INK_2, labelsize=8)
    cb.outline.set_visible(False)

    fig.suptitle(f"{Path(res['file']).name}:  predicted failure {res['pred_fail_kg']:.2f} kg "
                 f"({res['pred_fail_N']:.1f} N) at x={hx:.1f}, z={hz:.1f} mm",
                 color=INK, fontsize=12, x=0.02, ha="left")
    fig.text(0.02, 0.015, f"2D plane stress, linear elastic, E={mat['E_mpa']} MPa, sigma_t={st} MPa, "
             f"{res['elements']} quadratic elements. Eye zone excluded from the failure search.",
             color=INK_2, fontsize=8)
    fig.savefig(out, dpi=150, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stl", nargs="+")
    ap.add_argument("--config", default=str(HERE / "constraints_v2_3.yaml"))
    ap.add_argument("--material", default=str(HERE / "fea_material.yaml"))
    ap.add_argument("--measured-kg", type=float, nargs="+",
                    help="measured failure loads, one per STL; prints the sigma_t that would match")
    args = ap.parse_args()
    cfg = load_cfg(Path(args.config))
    mat = load_material(Path(args.material))
    if args.measured_kg and len(args.measured_kg) != len(args.stl):
        ap.error("--measured-kg needs one value per STL")

    print(f"{'file':40s} {'pred kg':>8s} {'pred N':>8s} {'break at (x, z)':>18s} {'pin drop':>9s}"
          + (f" {'meas kg':>8s} {'error':>7s} {'sigma_t fit':>11s}" if args.measured_kg else ""))
    for i, path in enumerate(args.stl):
        stl = Path(path)
        res = analyze(stl, cfg, mat)
        png = stl.with_name(stl.stem + "_fea.png")
        plot(res, mat, png)
        summary = {k: v for k, v in res.items() if k not in ("mesh", "s1_elem", "u", "basis", "pin_dx")}
        summary["material"] = mat
        line = (f"{path:40s} {res['pred_fail_kg']:8.2f} {res['pred_fail_N']:8.1f} "
                f"{'(%.1f, %.1f)' % res['hotspot_xz']:>18s} {res['pin_drop_at_fail_mm']:7.2f}mm")
        if args.measured_kg:
            meas = args.measured_kg[i]
            fit = res["sigma1_per_N_mpa"] * meas * G
            summary.update(measured_kg=meas, sigma_t_fit_mpa=fit)
            line += f" {meas:8.2f} {100 * (res['pred_fail_kg'] - meas) / meas:+6.0f}% {fit:9.1f} MPa"
        stl.with_name(stl.stem + "_fea.json").write_text(json.dumps(summary, indent=2))
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
