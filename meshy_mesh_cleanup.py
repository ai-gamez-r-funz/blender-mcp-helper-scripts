"""
meshy_mesh_cleanup.py
=====================
Cleans up mesh geometry artifacts common in Meshy-generated GLB imports.

Typical issues fixed:
  - Non-manifold edges (crossed wires / clipping polygons)
  - Inverted / inward-facing normals
  - Small detached geometry islands (floating debris fragments)

Usage
-----
  Option A — Run via Blender MCP:
      Load this script and call it, or paste into execute_blender_code.

  Option B — Run inside Blender (Text Editor → Run Script):
      Open this file in Blender's Text Editor and press Run Script.
      The active selected object will be cleaned.

  Option C — Run as a standalone Blender CLI batch job:
      blender --background myfile.blend --python meshy_mesh_cleanup.py

Configuration
-------------
  Adjust MIN_ISLAND_VERTS below to control how aggressively small
  detached fragments are removed. Default is 100 verts.
  Anything below this threshold is treated as debris and deleted.
"""

import bpy
import bmesh
import mathutils

# ── Configuration ──────────────────────────────────────────────────────────────

MIN_ISLAND_VERTS = 100   # Islands smaller than this are removed as debris
MERGE_DISTANCE   = 0.0001  # Distance threshold for merge-by-distance (metres)

# ── Helpers ────────────────────────────────────────────────────────────────────

def get_geometry_islands(bm):
    """Return a list of vertex lists, one per connected geometry island."""
    for v in bm.verts:
        v.tag = False

    islands = []
    remaining = set(bm.verts)

    while remaining:
        seed = next(iter(remaining))
        island = []
        stack = [seed]
        while stack:
            v = stack.pop()
            if v.tag:
                continue
            v.tag = True
            island.append(v)
            remaining.discard(v)
            for e in v.link_edges:
                other = e.other_vert(v)
                if not other.tag:
                    stack.append(other)
        islands.append(island)

    return islands


def mesh_center(bm):
    """Return the average position of all vertices."""
    center = mathutils.Vector((0.0, 0.0, 0.0))
    for v in bm.verts:
        center += v.co
    if bm.verts:
        center /= len(bm.verts)
    return center


def count_inward_faces(bm, center):
    """Count faces whose normal points toward the mesh center."""
    return sum(
        1 for f in bm.faces
        if f.normal.dot(center - f.calc_center_median()) > 0
    )


def diagnose(bm):
    """Return a dict of mesh health stats."""
    center = mesh_center(bm)
    islands = get_geometry_islands(bm)
    return {
        "verts":              len(bm.verts),
        "edges":              len(bm.edges),
        "faces":              len(bm.faces),
        "non_manifold_edges": sum(1 for e in bm.edges if not e.is_manifold),
        "inward_faces":       count_inward_faces(bm, center),
        "loose_verts":        sum(1 for v in bm.verts if not v.link_faces),
        "geometry_islands":   len(islands),
        "island_vert_counts": sorted([len(i) for i in islands], reverse=True)[:10],
    }


# ── Main cleanup ───────────────────────────────────────────────────────────────

def cleanup_mesh(obj, min_island_verts=MIN_ISLAND_VERTS, merge_dist=MERGE_DISTANCE):
    """
    Run the full cleanup pipeline on *obj* (must be a MESH object).
    Returns a dict with before/after stats and a summary of changes made.
    """
    if obj is None or obj.type != 'MESH':
        return {"error": "No mesh object provided or selected."}

    mesh = obj.data
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(mesh)

    before = diagnose(bm)

    # ── Step 1: Merge by distance ──────────────────────────────────────────────
    # Collapses vertices that are closer together than merge_dist.
    # Fixes duplicate verts that prevent proper edge/face evaluation.
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=merge_dist)

    # ── Step 2: Dissolve degenerate geometry ──────────────────────────────────
    # Removes zero-length edges and zero-area faces produced by the merge step
    # or by export/triangulation artefacts.
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=merge_dist)

    # ── Step 3: Remove small detached islands ─────────────────────────────────
    # Meshy output often contains dozens of tiny geometry fragments that are
    # disconnected from the main mesh body. Anything below min_island_verts
    # is treated as unwanted debris and deleted.
    islands = get_geometry_islands(bm)
    small_island_faces = set()
    islands_removed = 0

    for island in islands:
        if len(island) < min_island_verts:
            for v in island:
                for f in v.link_faces:
                    small_island_faces.add(f)
            islands_removed += 1

    if small_island_faces:
        bmesh.ops.delete(bm, geom=list(small_island_faces), context='FACES')

    # Clean up any verts orphaned by the face deletion
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context='VERTS')

    # ── Step 4: Recalculate normals outward ───────────────────────────────────
    # After removing interior/overlapping geometry, recalculate all face
    # normals to point outward. This resolves the inverted-shading look.
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    bmesh.update_edit_mesh(mesh)
    bpy.ops.object.mode_set(mode='OBJECT')

    # Re-diagnose on a fresh bmesh for accurate after stats
    bm2 = bmesh.new()
    bm2.from_mesh(mesh)
    after = diagnose(bm2)
    bm2.free()

    return {
        "object":          obj.name,
        "before":          before,
        "after":           after,
        "islands_removed": islands_removed,
        "verts_removed":   before["verts"]  - after["verts"],
        "faces_removed":   before["faces"]  - after["faces"],
        "edges_removed":   before["edges"]  - after["edges"],
    }


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    """
    When run directly (Text Editor or CLI), operate on the active selected object.
    When called via Blender MCP execute_blender_code, call cleanup_mesh() directly
    and assign the return value to `result`.
    """
    obj = bpy.context.view_layer.objects.active

    if obj is None:
        print("[meshy_mesh_cleanup] ERROR: No active object selected.")
        return

    if obj.type != 'MESH':
        print(f"[meshy_mesh_cleanup] ERROR: Active object '{obj.name}' is not a mesh.")
        return

    print(f"[meshy_mesh_cleanup] Cleaning '{obj.name}' ...")
    stats = cleanup_mesh(obj)

    if "error" in stats:
        print(f"[meshy_mesh_cleanup] ERROR: {stats['error']}")
        return

    print(f"[meshy_mesh_cleanup] Done.")
    print(f"  Verts : {stats['before']['verts']:>6}  →  {stats['after']['verts']:>6}  ({stats['verts_removed']:+d})")
    print(f"  Edges : {stats['before']['edges']:>6}  →  {stats['after']['edges']:>6}  ({stats['edges_removed']:+d})")
    print(f"  Faces : {stats['before']['faces']:>6}  →  {stats['after']['faces']:>6}  ({stats['faces_removed']:+d})")
    print(f"  Islands removed (debris) : {stats['islands_removed']}")
    print(f"  Non-manifold edges after : {stats['after']['non_manifold_edges']}")
    print(f"  Inward-facing faces after: {stats['after']['inward_faces']}")


if __name__ == "__main__":
    main()


# ── MCP convenience call ───────────────────────────────────────────────────────
# When running via Blender MCP execute_blender_code, paste the block below
# (or import this module and call cleanup_mesh directly):
#
#   import importlib, meshy_mesh_cleanup as m
#   importlib.reload(m)
#   obj = bpy.data.objects.get("Mesh_0")   # ← replace with your object name
#   result = m.cleanup_mesh(obj)
