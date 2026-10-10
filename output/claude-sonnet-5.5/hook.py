import bpy, bmesh, numpy as np, math, os
from collections import deque
from mathutils import Vector

# ---------------- PARAMETERS (mm) ----------------
MODEL   = "claude-sonnet-5.5"
THICK   = 5.0
EYE_C   = (0.0, 50.0)
HOLE_R  = 3.0
EYE_R   = 7.0
LEG_W   = 2.2
PIN_R_IN= 3.1
ARC_A0  = -40.0
ARC_A1  = 70.0
LEG_TOP = (0.0, 46.0)
BEZ_P1_LEN = 7.0
BEZ_P2 = (0.0, 28.0)
FILLET_K = 1.2
GRID = 0.1
# -------------------------------------------------
rl = LEG_W/2
R = PIN_R_IN + rl
def arcpt(a):
    a = math.radians(a); return np.array([-R*math.sin(a), -R*math.cos(a)])
pts=[arcpt(a) for a in np.linspace(ARC_A0,ARC_A1,60)]
a1=math.radians(ARC_A1); t0=np.array([-math.cos(a1), math.sin(a1)])
P0=pts[-1]; P1=P0+BEZ_P1_LEN*t0; P2=np.array(BEZ_P2); P3=np.array(LEG_TOP)
for t in np.linspace(0,1,300)[1:]:
    pts.append((1-t)**3*P0+3*(1-t)**2*t*P1+3*(1-t)*t**2*P2+t**3*P3)
pts=np.array(pts)
dense=[]
for i in range(len(pts)-1):
    for s in np.linspace(0,1,6)[:-1]: dense.append(pts[i]*(1-s)+pts[i+1]*s)
dense.append(pts[-1]); dense=np.array(dense)

def sdf(X,Z):
    P=np.stack([X.ravel(),Z.ravel()],1)
    d=np.full(len(P),1e9)
    for c in range(0,len(dense),50):
        seg=dense[c:c+50]
        d=np.minimum(d,np.sqrt(((P[:,None,:]-seg[None])**2).sum(2)).min(1))
    dleg=d-rl
    re=np.hypot(P[:,0]-EYE_C[0],P[:,1]-EYE_C[1])
    deye=re-EYE_R
    h=np.maximum(FILLET_K-np.abs(dleg-deye),0)/FILLET_K
    u=np.minimum(dleg,deye)-h*h*FILLET_K/4
    u=np.maximum(u,HOLE_R-re)
    return u.reshape(X.shape)

xs=np.arange(-9,9+1e-9,GRID); zs=np.arange(-8,59+1e-9,GRID)
X,Z=np.meshgrid(xs,zs,indexing='ij')
F=sdf(X,Z); nx,nz=F.shape
def key(i,j,k,l): return (min((i,j),(k,l)),max((i,j),(k,l)))
adj={}; coord={}
for i in range(nx-1):
    for j in range(nz-1):
        cs=[(i,j),(i+1,j),(i+1,j+1),(i,j+1)]
        ed=[]
        for n in range(4):
            (a,b),(c,d)=cs[n],cs[(n+1)%4]
            if (F[a,b]<0)!=(F[c,d]<0):
                k=key(a,b,c,d); t=F[a,b]/(F[a,b]-F[c,d])
                coord[k]=(xs[a]+t*(xs[c]-xs[a]), zs[b]+t*(zs[d]-zs[b])); ed.append(k)
        if len(ed)==2: pairs=[(ed[0],ed[1])]
        elif len(ed)==4: pairs=[(ed[0],ed[1]),(ed[2],ed[3])]
        else: continue
        for p,q in pairs:
            adj.setdefault(p,[]).append(q); adj.setdefault(q,[]).append(p)
seen=set(); loops=[]
for s in adj:
    if s in seen: continue
    loop=[s]; seen.add(s); prev=None; cur=s
    while True:
        nxt=[n for n in adj[cur] if n!=prev and n not in seen]
        if not nxt: break
        prev=cur; cur=nxt[0]; seen.add(cur); loop.append(cur)
    loops.append([coord[k] for k in loop])
def area(l):
    return sum(l[i][0]*l[(i+1)%len(l)][1]-l[(i+1)%len(l)][0]*l[i][1] for i in range(len(l)))/2
loops=[l for l in loops if len(l)>10]
outer=max(loops,key=lambda l:abs(area(l))); hole=[l for l in loops if l is not outer][0]

def prism(loop,y0,y1,name):
    bm=bmesh.new()
    vs=[bm.verts.new((x,y0,z)) for x,z in loop]
    f=bm.faces.new(vs)
    ret=bmesh.ops.extrude_face_region(bm,geom=[f])
    vv=[v for v in ret['geom'] if isinstance(v,bmesh.types.BMVert)]
    bmesh.ops.translate(bm,verts=vv,vec=(0,y1-y0,0))
    bmesh.ops.recalc_face_normals(bm,faces=bm.faces)
    me=bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    o=bpy.data.objects.new(name,me); bpy.context.scene.collection.objects.link(o); return o
for n in ("hook","cut"):
    if n in bpy.data.objects: bpy.data.objects.remove(bpy.data.objects[n],do_unlink=True)
ob=prism(outer,0,THICK,"hook"); cut=prism(hole,-1,THICK+1,"cut")
m=ob.modifiers.new("b","BOOLEAN"); m.operation='DIFFERENCE'; m.object=cut; m.solver='EXACT'
bpy.context.view_layer.objects.active=ob
bpy.ops.object.modifier_apply(modifier="b")
bpy.data.objects.remove(cut,do_unlink=True)
bm=bmesh.new(); bm.from_mesh(ob.data)
bmesh.ops.triangulate(bm,faces=bm.faces)
bmesh.ops.recalc_face_normals(bm,faces=bm.faces)
bm.to_mesh(ob.data)

# revision 2: cleanup after the exact boolean
bm=bmesh.new(); bm.from_mesh(ob.data)
bmesh.ops.remove_doubles(bm,verts=bm.verts,dist=2e-3)
bmesh.ops.dissolve_degenerate(bm,dist=2e-3,edges=bm.edges)
bmesh.ops.triangulate(bm,faces=bm.faces)
bmesh.ops.recalc_face_normals(bm,faces=bm.faces)
bm.to_mesh(ob.data)

out = f"/Users/orcaim/Documents/code/personal/hook-bench/output/{MODEL}/hookv1.stl"
os.makedirs(os.path.dirname(out), exist_ok=True)
bpy.ops.object.select_all(action="DESELECT")
hook = bpy.data.objects["hook"]
hook.select_set(True)
bpy.context.view_layer.objects.active = hook
bpy.ops.wm.stl_export(filepath=out, export_selected_objects=True,
                      apply_modifiers=True, global_scale=1.0,
                      use_scene_unit=False, ascii_format=False)
