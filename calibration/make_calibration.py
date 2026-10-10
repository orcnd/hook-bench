# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy",
#   "trimesh",
#   "shapely>=2.1",
#   "scipy",
#   "networkx",
#   "pyyaml",
#   "mapbox-earcut",
# ]
# ///
"""Build the two human-designed calibration hooks and validate them.

    uv run calibration/make_calibration.py

Both are the same J: a solid eye ring around the Ø6 screw hole, a straight shank running
from the eye down to the bowl, and a bowl the pin sits in. Only the bowl differs:

weak   - loose bowl (inner R9), shank ~10 mm off the load line at the bowl
strong - tight bowl hugging the pin (inner R3.3), shank ~4.4 mm off the load line

Both are tuned to the same profile area (TARGET_AREA) so they use the same material.
Writes calib_weak.stl / calib_strong.stl next to this file.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import LineString, Point

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from validate import load_cfg, report, validate_mesh  # noqa: E402

CFG = load_cfg()
T = CFG["thickness_y"]
EYE_C = tuple(CFG["eye"]["center"])
EYE_RI = CFG["eye"]["hole_dia"] / 2                 # 3.0
EYE_RO = EYE_RI + CFG["eye"]["ring_min_width"]      # 6.0
TARGET_AREA = 250.0
FILLET = 0.6  # rounds every concave corner (rule: >= 0.5)


def arc(cx, cz, r, a0, a1, n=48):
    return [(cx + r * math.cos(a), cz + r * math.sin(a)) for a in np.linspace(a0, a1, n)]


def band(coords, w):
    return LineString(coords).buffer(w / 2, cap_style="round", join_style="round", quad_segs=32)


def eye_ring():
    c = Point(EYE_C)
    return c.buffer(EYE_RO, quad_segs=64).difference(c.buffer(EYE_RI, quad_segs=64))


def finish(shape):
    """Fillet concave corners, then re-cut the eye hole exactly."""
    shape = shape.buffer(FILLET, quad_segs=16).buffer(-FILLET, quad_segs=16)
    return shape.difference(Point(EYE_C).buffer(EYE_RI, quad_segs=64))


def j_hook(w, r_in, tip, flare_deg):
    """J with its bowl bottom at P1 (0, -3): bowl inner radius r_in, straight shank to the eye.

    The shank leans toward the eye, which narrows the mouth as it rises; flaring the tip
    outward keeps the mouth open for the Ø6.5 insertion disk.
    """
    cz = r_in - 3.0                     # bowl center height -> bowl bottom inner edge at z = -3.0
    rc = r_in + w / 2                   # bowl centerline radius
    a = math.radians(flare_deg)
    tip_top = (rc + tip * math.sin(a), cz + tip * math.cos(a))
    bowl = [tip_top, (rc, cz)] + arc(0, cz, rc, 0, -math.pi)
    shank_end = (EYE_C[0], EYE_C[1] - (EYE_RO - 1.0))   # straight into the ring, aimed at its center
    shank = [(-rc, cz), shank_end]
    return finish(band(bowl, w).union(band(shank, w)).union(eye_ring()))


def strong(tip):
    return j_hook(w=2.2, r_in=3.3, tip=tip, flare_deg=15)


def weak(tip):
    return j_hook(w=2.0, r_in=9.0, tip=tip, flare_deg=0)


def tune(make, lo, hi):
    """Pick the tip length that gives TARGET_AREA (area grows with tip length)."""
    for _ in range(40):
        mid = (lo + hi) / 2
        if make(mid).area < TARGET_AREA:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def to_mesh(profile) -> trimesh.Trimesh:
    m = trimesh.creation.extrude_polygon(profile, T)          # profile in XY, extruded +Z
    m.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))  # (x,y,z)->(x,-z,y)
    m.apply_translation([0, T, 0])                               # Y: [-T,0] -> [0,T]
    return m


def main() -> int:
    all_ok = True
    for name, make in (("weak", weak), ("strong", strong)):
        tip = tune(make, 0.5, 40.0)
        mesh = to_mesh(make(tip))
        out = HERE / f"calib_{name}.stl"
        mesh.export(out)
        # Validate the written file, exactly like an AI design.
        checks, metrics = validate_mesh(trimesh.load_mesh(out, process=True), CFG)
        print(f"== {name}: tip length {tip:.2f} mm")
        print(report(str(out.relative_to(HERE.parent)), checks, metrics, CFG))
        print()
        all_ok &= all(ok for _, ok, _ in checks)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
