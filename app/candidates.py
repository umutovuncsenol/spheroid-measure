"""Turn a well's cell mask into candidate objects.

Rules (all parameters in config/profiles.json -> "grouping"):
  * parts = 4-connected components of the cell mask
  * seeds = parts with area >= seed_min_px AND area >= seed_rel_frac * largest part in the well
            (seed_rel_frac = 0 disables the relative rule, so a large wall artefact cannot
            suppress real clusters) -> each seed starts its OWN candidate (never merged)
  * every other part is assigned to the nearest seed if its pixel distance to that seed's
    pixels is <= assign_dist_px; otherwise it stays "unassigned" (visible, user-assignable)
  * every pixel removed by a clean-up step is kept as an "excluded" part with its reason
    (wall_shape, wall_band, thin, small, split_line): visible, assignable, or promotable to
    an object. A watershed cut (split_line) is one part per cut, with the pieces it lies
    between (`between`)
No gaps are filled between parts. Proximity assignments carry assigned_by='proximity' so the
review UI and the export can show them as unconfirmed suggestions.
"""
import numpy as np
from scipy import ndimage as ndi

FOUR = ndi.generate_binary_structure(2, 1)
EIGHT = ndi.generate_binary_structure(2, 2)
WHOLE_REASONS = ('split_line',)   # removed masks kept as whole connected pieces, never split per neighbour


def resolve_grouping(gp, well_size_px):
    """Grouping settings in px. *_frac settings are relative to the well size (side length),
    so the same profile works at different magnifications."""
    g = dict(gp)
    s = float(well_size_px or 0)
    if s and 'seed_min_frac' in g:
        g['seed_min_px'] = g['seed_min_frac'] * s * s
    if s and 'assign_dist_frac' in g:
        g['assign_dist_px'] = g['assign_dist_frac'] * s
    g.setdefault('seed_rel_frac', 0.0)
    return g


def group_parts(cells, excluded, gp, removed=None):
    lab, n = ndi.label(cells, structure=FOUR)
    parts = []
    if n:
        sizes = np.bincount(lab.ravel())
        objs = ndi.find_objects(lab)
        largest = sizes[1:].max()
        for i in range(1, n + 1):
            parts.append(dict(label=i, area=int(sizes[i]), slc=objs[i - 1],
                              seed=bool(sizes[i] >= gp['seed_min_px'] and sizes[i] >= gp['seed_rel_frac'] * largest)))
    seeds = [p for p in parts if p['seed']]
    # distance from every pixel to each seed (EDT per seed; few seeds per well)
    seed_dist = [ndi.distance_transform_edt(lab != s['label']) for s in seeds]
    assignment = {}
    for p in parts:
        if p['seed']:
            assignment[p['label']] = p['label']
            continue
        if not seeds:
            assignment[p['label']] = None
            continue
        m = lab[p['slc']] == p['label']
        d = [sd[p['slc']][m].min() for sd in seed_dist]
        k = int(np.argmin(d))
        assignment[p['label']] = seeds[k]['label'] if d[k] <= gp['assign_dist_px'] else None

    # excluded / removed parts get labels after the kept ones, one reason at a time
    labels = lab.astype(np.int32)
    meta = []
    for p in parts:
        a = assignment[p['label']]
        meta.append(dict(part=int(p['label']), area=p['area'],
                         kind='seed' if p['seed'] else ('assigned' if a else 'unassigned'),
                         seed=int(a) if a else None,
                         # proximity assignment is a SUGGESTION, shown as such to the reviewer
                         assigned_by='proximity' if (a and not p['seed']) else None))
    nxt = n
    # wall_shape components first (they are whole components already)
    if excluded is not None and excluded.any():
        xlab, xn = ndi.label(excluded & (labels == 0), structure=FOUR)
        if xn:
            labels[xlab > 0] = xlab[xlab > 0] + nxt
            xs = np.bincount(xlab.ravel())
            for j in range(1, xn + 1):
                meta.append(dict(part=int(nxt + j), area=int(xs[j]), kind='excluded', reason='wall_shape', seed=None))
            nxt += xn
    # other removed pixels: fragments touching (<= attach_px) an existing part become ONE part per
    # (reason, neighbour) -- e.g. the fringe of a cluster cut off by opening, or the piece of a
    # cluster lying in the wall band -- so one click restores it; the rest are grouped by proximity.
    attach = float(gp.get('attach_px', 2))
    group_r = int(gp.get('fragment_group_px', 3))
    for reason, mask in (removed or {}).items():
        if mask is None or not mask.any():
            continue
        free = mask & (labels == 0)
        if not free.any():
            continue
        if reason in WHOLE_REASONS:
            # a watershed cut stays ONE part (not split between its two neighbours), so a single
            # click puts it back; next_to = the piece it borders most, between = all bordering pieces
            lab8, k = ndi.label(free, structure=EIGHT)
            for j, sl in enumerate(ndi.find_objects(lab8), start=1):
                sl = tuple(slice(max(s.start - 1, 0), s.stop + 1) for s in sl)
                m = lab8[sl] == j
                ring = ndi.binary_dilation(m, structure=EIGHT) & ~m
                nb = labels[sl][ring]
                nb = nb[(nb > 0) & (nb <= n)]          # kept parts only
                nxt += 1
                labels[sl][m] = nxt
                d = dict(part=int(nxt), area=int(m.sum()), kind='excluded', reason=reason, seed=None)
                if nb.size:
                    ids, cnt = np.unique(nb, return_counts=True)
                    d['next_to'] = int(ids[np.argmax(cnt)])
                    d['between'] = [int(i) for i in ids]
                meta.append(d)
            continue
        if labels.any():
            dist, (iy, ix) = ndi.distance_transform_edt(labels == 0, return_indices=True)
            near = labels[iy, ix]
            near_ok = free & (dist <= attach)
        else:
            near_ok = np.zeros_like(free)
            near = labels
        for nb in np.unique(near[near_ok]):
            m = near_ok & (near == nb)
            nxt += 1
            labels[m] = nxt
            meta.append(dict(part=int(nxt), area=int(m.sum()), kind='excluded', reason=reason, seed=None,
                             next_to=int(nb)))
        rest = free & ~near_ok
        if rest.any():
            grp, gn = ndi.label(ndi.binary_dilation(rest, iterations=group_r) if group_r > 0 else rest,
                                structure=ndi.generate_binary_structure(2, 2))
            grp = np.where(rest, grp, 0)
            ids = np.unique(grp[grp > 0])
            for gid in ids:
                m = grp == gid
                nxt += 1
                labels[m] = nxt
                meta.append(dict(part=int(nxt), area=int(m.sum()), kind='excluded', reason=reason, seed=None))
    return labels, meta


def touches_region_edge(obj_mask_full_crop, region_crop, margin_px):
    """True if any object pixel lies within margin+2 px of the well boundary (or outside it)."""
    dist_in = ndi.distance_transform_edt(np.pad(region_crop, 1))[1:-1, 1:-1]
    return bool((obj_mask_full_crop & (dist_in <= margin_px + 2)).any())
