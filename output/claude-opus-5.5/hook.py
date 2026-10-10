import bpy, bmesh, math

# ================= PARAMETERS (mm) =================
T        = 5.0            # extrusion thickness along Y (0..T)
W        = 2.25           # strip width (inscribed-circle cap is 2.3)
CAV_C    = (0.20, 0.15)   # bowl cavity centre (x, z)
CAV_R    = 3.33           # bowl cavity radius (holds D6.5 insertion disk, touches P1)
SLOT_A   = 50.0           # entry-slot / lip direction from +X (deg)
LIP_L    = 5.0            # straight lip length past bowl arc
TURN_R   = 4.0            # bend radius from slot direction into vertical shank
SHANK_X  = 0.0            # shank on the load line x = 0
EYE_C    = (0.0, 50.0)    # eye centre
EYE_R    = 6.8            # eye outer radius
HOLE_R   = 3.0            # eye hole radius (D6.0)
FILLET   = 0.8            # concave blend radius shank <-> eye
GRID     = 0.04           # marching-squares grid
RESAMPLE = 0.08           # final outline vertex spacing
BOX      = (-8.0, 10.0, -7.0, 58.5)   # sampling window x0, x1, z0, z1

# ================= centreline =================
a = math.radians(SLOT_A); d = (math.cos(a), math.sin(a))
Rm = CAV_R + W/2; cx, cz = CAV_C
a_up = a + math.pi/2; a_lip = a - math.pi/2 + 2*math.pi
prims = [('arc', cx, cz, Rm, a_up, a_lip)]                      # bowl, concentric with cavity
pl0 = (cx + Rm*math.cos(a_lip), cz + Rm*math.sin(a_lip))
prims.append(('seg', pl0, (pl0[0] + LIP_L*d[0], pl0[1] + LIP_L*d[1])))   # lip
pa = (cx + Rm*math.cos(a_up), cz + Rm*math.sin(a_up))
s_len = (SHANK_X - TURN_R*(1 - math.sin(a)) - pa[0]) / d[0]
pb = (pa[0] + s_len*d[0], pa[1] + s_len*d[1])
prims.append(('seg', pa, pb))                                   # slot upper wall
tc = (pb[0] - TURN_R*math.sin(a), pb[1] + TURN_R*math.cos(a))
prims.append(('arc', tc[0], tc[1], TURN_R, a - math.pi/2, 0.0))  # bend to vertical
prims.append(('seg', (tc[0] + TURN_R, tc[1]), (SHANK_X, EYE_C[1])))   # shank

def dseg(px, pz, p, q):
    vx, vz = q[0]-p[0], q[1]-p[1]; wx, wz = px-p[0], pz-p[1]
    t = max(0.0, min(1.0, (wx*vx + wz*vz)/(vx*vx + vz*vz)))
    return math.hypot(wx - t*vx, wz - t*vz)

def darc(px, pz, ox, oz, R, a0, a1):
    ang = math.atan2(pz-oz, px-ox)
    while ang < a0: ang += 2*math.pi
    while ang >= a0 + 2*math.pi: ang -= 2*math.pi
    if ang <= a1: return abs(math.hypot(px-ox, pz-oz) - R)
    return min(math.hypot(px-ox-R*math.cos(a0), pz-oz-R*math.sin(a0)),
               math.hypot(px-ox-R*math.cos(a1), pz-oz-R*math.sin(a1)))

def sdf(px, pz):
    dc = min(dseg(px, pz, p[1], p[2]) if p[0] == 'seg' else darc(px, pz, *p[1:]) for p in prims)
    ds = dc - W/2
    re = math.hypot(px-EYE_C[0], pz-EYE_C[1]); de = re - EYE_R
    ux, uz = max(FILLET-ds, 0.0), max(FILLET-de, 0.0)
    du = max(FILLET, min(ds, de)) - math.hypot(ux, uz)        # round union
    return max(du, HOLE_R - re)                                # minus eye hole

# ================= marching squares =================
x0, x1, z0, z1 = BOX
nx = int(round((x1-x0)/GRID)); nz = int(round((z1-z0)/GRID))
F = [[(sdf(x0+i*GRID, z0+j*GRID) or 1e-9) for j in range(nz+1)] for i in range(nx+1)]
def ept(key):
    k, i, j = key
    if k == 'h':
        fa, fb = F[i][j], F[i+1][j]; t = fa/(fa-fb); return (x0+(i+t)*GRID, z0+j*GRID)
    fa, fb = F[i][j], F[i][j+1]; t = fa/(fa-fb); return (x0+i*GRID, z0+(j+t)*GRID)
adj = {}
def link(e1, e2):
    adj.setdefault(e1, []).append(e2); adj.setdefault(e2, []).append(e1)
for i in range(nx):
    for j in range(nz):
        fa, fb, fc, fd = F[i][j], F[i+1][j], F[i+1][j+1], F[i][j+1]
        sa, sb, sc, sd = fa < 0, fb < 0, fc < 0, fd < 0
        if sa == sb == sc == sd: continue
        e0, e1, e2, e3 = ('h',i,j), ('v',i+1,j), ('h',i,j+1), ('v',i,j)
        cr = [e for e, c in ((e0, sa != sb), (e1, sb != sc), (e2, sc != sd), (e3, sd != sa)) if c]
        if len(cr) == 2: link(*cr)
        elif ((fa+fb+fc+fd)/4 < 0) == sa: link(e0, e1); link(e2, e3)
        else: link(e3, e0); link(e1, e2)
loops, seen = [], set()
for start in adj:
    if start in seen: continue
    loop, prev, cur = [start], None, start; seen.add(start)
    while True:
        nb = [n for n in adj[cur] if n != prev]
        if not nb or nb[0] == start or nb[0] in seen: break
        prev, cur = cur, nb[0]; loop.append(cur); seen.add(cur)
    loops.append([ept(k) for k in loop])

def resample(L, s):
    n = len(L); cum = [0.0]
    for k in range(n):
        p, q = L[k], L[(k+1) % n]; cum.append(cum[-1] + math.hypot(q[0]-p[0], q[1]-p[1]))
    P = cum[-1]; m = int(round(P/s)); out = []; k = 0
    for i in range(m):
        t = i*P/m
        while cum[k+1] < t: k += 1
        p, q = L[k], L[(k+1) % n]; u = (t-cum[k])/(cum[k+1]-cum[k]) if cum[k+1] > cum[k] else 0.0
        out.append((p[0]+u*(q[0]-p[0]), p[1]+u*(q[1]-p[1])))
    return out
loops = [resample(L, RESAMPLE) for L in loops]
assert len(loops) == 2

# ================= extrude to solid =================
for o in list(bpy.data.objects):
    if o.name.startswith("hook"): bpy.data.objects.remove(o, do_unlink=True)
cu = bpy.data.curves.new("hook_prof", 'CURVE')
cu.dimensions = '2D'; cu.fill_mode = 'BOTH'; cu.extrude = T/2; cu.resolution_u = 1
for L in loops:
    sp = cu.splines.new('POLY'); sp.points.add(len(L)-1); sp.use_cyclic_u = True
    for k, p in enumerate(L): sp.points[k].co = (p[0], p[1], 0.0, 1.0)
tmp = bpy.data.objects.new("hook_tmp", cu); bpy.context.scene.collection.objects.link(tmp)
me = bpy.data.meshes.new_from_object(tmp.evaluated_get(bpy.context.evaluated_depsgraph_get()))
bpy.data.objects.remove(tmp, do_unlink=True); bpy.data.curves.remove(cu)
bm = bmesh.new(); bm.from_mesh(me)
for v in bm.verts:
    x, y, z = v.co; v.co = (x, z + T/2, y)            # profile XY -> XZ, thickness Y 0..T
bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
bm.to_mesh(me); bm.free()
me.name = "hook"
hook = bpy.data.objects.new("hook", me); bpy.context.scene.collection.objects.link(hook)

# ================= export =================
import os
MODEL = "claude-opus-5.5"
out = f"/Users/orcaim/Documents/code/personal/hook-bench/output/{MODEL}/hookv1.stl"
os.makedirs(os.path.dirname(out), exist_ok=True)
bpy.ops.object.select_all(action="DESELECT")
hook = bpy.data.objects["hook"]
hook.select_set(True)
bpy.context.view_layer.objects.active = hook
bpy.ops.wm.stl_export(filepath=out, export_selected_objects=True,
                      apply_modifiers=True, global_scale=1.0,
                      use_scene_unit=False, ascii_format=False)
