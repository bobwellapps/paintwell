"""Content-Aware Fill: rebuild a selected area from the surrounding picture.

PatchMatch-based image completion (Barnes et al. 2009, Wexler et al. 2007), coarse to
fine on an image pyramid. At each level, patches touching the hole are matched to
similar patches outside it (PatchMatch propagation plus random search, vectorised), the
hole is repainted by letting overlapping matches vote, and the result seeds the next
level. Only a box around the hole is processed.
"""
import numpy as np

PATCH = 7                       # patch size (pixels) used for matching and voting
PR = PATCH // 2
# matching uses every other pixel of the patch (much faster, nearly as good)
_SEARCH_OFFS = [(dy, dx) for dy in range(-PR, PR + 1, 2) for dx in range(-PR, PR + 1, 2)]
_VOTE_OFFS = [(dy, dx) for dy in range(-PR, PR + 1) for dx in range(-PR, PR + 1)]


class Cancelled(Exception):
    pass


def _down2(a):
    """Halve an image (area average); odd sizes keep their last row / column."""
    h, w = a.shape[:2]
    h2, w2 = (h + 1) // 2, (w + 1) // 2
    p = np.pad(a, ((0, h2 * 2 - h), (0, w2 * 2 - w)) + ((0, 0),) * (a.ndim - 2), mode="edge")
    return p.reshape(h2, 2, w2, 2, *a.shape[2:]).mean(axis=(1, 3)).astype(a.dtype)


def _down2_mask(m):
    """Halve a mask: a coarse pixel is in the hole if any of its four pixels are."""
    h, w = m.shape
    h2, w2 = (h + 1) // 2, (w + 1) // 2
    p = np.pad(m, ((0, h2 * 2 - h), (0, w2 * 2 - w)), mode="edge")
    return p.reshape(h2, 2, w2, 2).any(axis=(1, 3))


def _dilate(m, r):
    if r <= 0:
        return m.copy()
    out = m.copy()
    h, w = m.shape
    for _ in range(r):       # 3x3 steps (square neighbourhood)
        nxt = out.copy()
        nxt[1:, :] |= out[:-1, :]; nxt[:-1, :] |= out[1:, :]
        nxt[:, 1:] |= out[:, :-1]; nxt[:, :-1] |= out[:, 1:]
        nxt[1:, 1:] |= out[:-1, :-1]; nxt[:-1, :-1] |= out[1:, 1:]
        nxt[1:, :-1] |= out[:-1, 1:]; nxt[:-1, 1:] |= out[1:, :-1]
        out = nxt
    return out


def _onion_fill(img, hole, check):
    """Initial fill for the coarsest level: work inwards ring by ring, copying each pixel from
    the patch that best matches its already-known neighbours.
    """
    a = img.astype(np.float32).copy()
    H, W, C = a.shape
    valid = ~_dilate(hole, PR)
    valid[:PR, :] = valid[-PR:, :] = False
    valid[:, :PR] = valid[:, -PR:] = False
    vy, vx = np.nonzero(valid)
    if len(vy) == 0:
        return a
    offs = _VOTE_OFFS
    P = np.pad(a, ((PR, PR), (PR, PR), (0, 0)), mode="edge")
    # source patches as rows: S x (K*C)
    S = np.stack([P[vy + dy + PR, vx + dx + PR] for dy, dx in offs], axis=1).reshape(len(vy), -1)
    S2 = S * S
    known = ~hole
    while not known.all():
        check()
        front = _dilate(known, 1) & ~known
        if not front.any():
            break
        fy, fx = np.nonzero(front)
        P = np.pad(a, ((PR, PR), (PR, PR), (0, 0)), mode="edge")
        Kp = np.pad(known, PR, mode="constant")
        T = np.stack([P[fy + dy + PR, fx + dx + PR] for dy, dx in offs], axis=1)          # F x K x C
        M = np.stack([Kp[fy + dy + PR, fx + dx + PR] for dy, dx in offs], axis=1).astype(np.float32)
        M = np.repeat(M[..., None], C, axis=2).reshape(len(fy), -1)                        # F x K*C
        T = T.reshape(len(fy), -1)
        best_i = np.empty(len(fy), np.int64)
        step = max(1, 4_000_000 // max(1, len(vy)))
        for k0 in range(0, len(fy), step):
            m, t = M[k0:k0 + step], T[k0:k0 + step]
            # masked SSD = sum m*t^2 - 2 sum m*t*s + sum m*s^2
            d = (m * t * t).sum(1)[:, None] - 2.0 * (m * t) @ S.T + m @ S2.T
            best_i[k0:k0 + step] = np.argmin(d, axis=1)
        a[fy, fx] = a[vy[best_i], vx[best_i]]
        known |= front
    return a


class _Level:
    def __init__(self, img, hole):
        self.img = img.astype(np.float32)              # H x W x C, current estimate in the hole
        self.hole = hole
        h, w = hole.shape
        self.h, self.w = h, w
        # patch centres that may be copied from: the whole patch is known and inside the box
        valid = ~_dilate(hole, PR)
        valid[:PR, :] = False; valid[-PR:, :] = False; valid[:, :PR] = False; valid[:, -PR:] = False
        self.valid = valid
        self.vy, self.vx = np.nonzero(valid)
        # patch centres whose patch touches the hole: these get matched
        self.tmask = _dilate(hole, PR)
        self.ty, self.tx = np.nonzero(self.tmask)

    def padded(self):
        return np.pad(self.img, ((PR, PR), (PR, PR), (0, 0)), mode="edge")


HOLE_TRUST = 0.35   # how much the current guesses inside the hole count when comparing patches


def _patch_dist(P, ty, tx, sy, sx, offs, Wt=None):
    """SSD between patches centred at (ty, tx) and (sy, sx) in padded image P (unpadded
    coordinates). Wt weights the target side, so guessed hole pixels count less.
    """
    d = np.zeros(ty.shape, np.float32)
    for dy, dx in offs:
        a = P[ty + (dy + PR), tx + (dx + PR)]
        b = P[sy + (dy + PR), sx + (dx + PR)]
        e = a - b
        if Wt is None:
            d += (e * e).sum(axis=1)
        else:
            d += (e * e).sum(axis=1) * Wt[ty + (dy + PR), tx + (dx + PR)]
    return d


def _random_valid(L, n, rng):
    i = rng.integers(0, len(L.vy), n)
    return L.vy[i].astype(np.int32), L.vx[i].astype(np.int32)


def _patchmatch(L, nny, nnx, rng, iters, check):
    """Improve the nearest-neighbour field (nny, nnx: full-size arrays of source centres)
    for the target centres of level L."""
    P = L.padded()
    Wt = np.pad(np.where(L.hole, HOLE_TRUST, 1.0).astype(np.float32), PR, mode="edge")
    ty, tx = L.ty, L.tx
    sy, sx = nny[ty, tx], nnx[ty, tx]
    best = _patch_dist(P, ty, tx, sy, sx, _SEARCH_OFFS, Wt)
    H, W = L.h, L.w
    maxr = max(H, W)

    def consider(cy, cx):
        ok = (cy >= 0) & (cy < H) & (cx >= 0) & (cx < W)
        cy = np.clip(cy, 0, H - 1); cx = np.clip(cx, 0, W - 1)
        ok &= L.valid[cy, cx]
        if not ok.any():
            return
        idx = np.nonzero(ok)[0]
        d = _patch_dist(P, ty[idx], tx[idx], cy[idx], cx[idx], _SEARCH_OFFS, Wt)
        better = d < best[idx]
        j = idx[better]
        sy[j] = cy[j]; sx[j] = cx[j]; best[j] = d[better]

    for it in range(iters):
        check()
        # propagation: a neighbour's good match, shifted by the same step, is often good here
        steps = (1, 2, 4) if it == 0 else (1, 2)
        for s in steps:
            for dy, dx in ((0, s), (s, 0), (0, -s), (-s, 0)):
                ny = np.clip(ty - dy, 0, H - 1); nx = np.clip(tx - dx, 0, W - 1)
                nny[ty, tx] = sy; nnx[ty, tx] = sx
                consider(nny[ny, nx] + dy, nnx[ny, nx] + dx)
        # random search around the current match, in shrinking circles
        r = maxr
        while r >= 1:
            check()
            cy = (sy + rng.integers(-r, r + 1, sy.shape)).astype(np.int32)
            cx = (sx + rng.integers(-r, r + 1, sx.shape)).astype(np.int32)
            consider(cy, cx)
            r //= 2
        # and a few completely fresh guesses (helps escape poor matches)
        cy, cx = _random_valid(L, len(ty), rng)
        consider(cy, cx)
        nny[ty, tx] = sy; nnx[ty, tx] = sx
    return best


def _vote(L, nny, nnx, dist):
    """Repaint the hole: every matched patch votes for the pixels it covers."""
    P = L.padded()
    ty, tx = L.ty, L.tx
    sy, sx = nny[ty, tx], nnx[ty, tx]
    sig2 = float(np.percentile(dist, 75)) + 1e-6
    wgt = np.exp(-dist / (2.0 * sig2)).astype(np.float32)
    C = L.img.shape[2]
    acc = np.zeros((L.h + 2 * PR, L.w + 2 * PR, C), np.float32)
    wsum = np.zeros((L.h + 2 * PR, L.w + 2 * PR), np.float32)
    for dy, dx in _VOTE_OFFS:
        qy, qx = ty + (dy + PR), tx + (dx + PR)      # one offset: each target hits a different pixel
        acc[qy, qx] += P[sy + (dy + PR), sx + (dx + PR)] * wgt[:, None]
        wsum[qy, qx] += wgt
    acc = acc[PR:-PR, PR:-PR]
    wsum = wsum[PR:-PR, PR:-PR]
    h = L.hole & (wsum > 0)
    L.img[h] = acc[h] / wsum[h, None]


def fill(img, hole, progress=None, cancelled=None, seed=1):
    """img: H x W x C float32 in 0..1, hole: H x W bool. Returns img with the hole filled.
    progress(fraction) is called now and then; cancelled() returning True raises Cancelled.
    """
    def check():
        if cancelled is not None and cancelled():
            raise Cancelled()

    rng = np.random.default_rng(seed)
    if not hole.any():
        return img.copy()
    known = ~_dilate(hole, PR)
    known[:PR, :] = known[-PR:, :] = False
    known[:, :PR] = known[:, -PR:] = False
    if known.sum() < PATCH * PATCH * 4:
        raise ValueError("There isn't enough picture around the selection to fill it from.")

    # pyramid: shrink until the hole is only a few patches across
    # how thick the hole is (twice the deepest distance from its edge), not how far apart
    # separate holes are: several small holes still get only a few levels
    known_g = ~hole
    depth = 0
    while not known_g.all() and depth < 4096:
        known_g = _dilate(known_g, 1) if depth < 64 else _dilate(known_g, 8)
        depth += 1 if depth < 64 else 8
    extent = 2 * depth + 1
    levels = 0
    while extent / (2 ** levels) > PATCH * 2.5 and min(img.shape[:2]) / (2 ** (levels + 1)) > PATCH * 4 and levels < 7:
        levels += 1
    imgs, holes = [img.astype(np.float32)], [hole]
    for _ in range(levels):
        imgs.append(_down2(imgs[-1]))
        holes.append(_down2_mask(holes[-1]))

    total = sum((imgs[i].shape[0] * imgs[i].shape[1]) for i in range(len(imgs)))
    done = 0.0
    nny = nnx = None
    prev_img = prev_nny = prev_nnx = None   # the coarser level's result, set at the end of each pass
    for lv in range(levels, -1, -1):
        check()
        cur = imgs[lv].copy()
        hl = holes[lv]
        if lv == levels:
            cur = _onion_fill(cur, hl, check)
        else:
            # start from the coarser result, enlarged
            up = np.repeat(np.repeat(prev_img, 2, axis=0), 2, axis=1)[:cur.shape[0], :cur.shape[1]]
            cur[hl] = up[hl]
        L = _Level(cur, hl)
        if len(L.vy) == 0:
            # this level is too small to have any whole known patch: just keep the rough fill
            prev_img = L.img
            nny = nnx = None
            continue
        H, W = L.h, L.w
        if nny is None:
            nny = np.empty((H, W), np.int32); nnx = np.empty((H, W), np.int32)
            ry, rx = _random_valid(L, H * W, rng)
            nny[:] = ry.reshape(H, W); nnx[:] = rx.reshape(H, W)
        else:
            gy, gx = np.mgrid[0:H, 0:W]
            ny = np.clip(prev_nny[np.minimum(gy // 2, prev_nny.shape[0] - 1), np.minimum(gx // 2, prev_nny.shape[1] - 1)] * 2 + gy % 2, 0, H - 1)
            nx = np.clip(prev_nnx[np.minimum(gy // 2, prev_nnx.shape[0] - 1), np.minimum(gx // 2, prev_nnx.shape[1] - 1)] * 2 + gx % 2, 0, W - 1)
            bad = ~L.valid[ny, nx]
            if bad.any():
                ry, rx = _random_valid(L, int(bad.sum()), rng)
                ny[bad] = ry; nx[bad] = rx
            nny, nnx = ny.astype(np.int32), nx.astype(np.int32)
        em_iters = 6 if lv == levels else (4 if lv > 0 else 3)
        for k in range(em_iters):
            dist = _patchmatch(L, nny, nnx, rng, 2 if k else 3, check)
            _vote(L, nny, nnx, dist)
            if progress is not None:
                progress(min(0.99, (done + (k + 1) / em_iters * H * W) / total))
        done += H * W
        prev_img, prev_nny, prev_nnx = L.img, nny, nnx
    out = img.astype(np.float32).copy()
    out[hole] = prev_img[hole]
    return out


# --- Thin areas (Remove tool) ---
def _harmonic(a, hole, iters=40):
    """Membrane fill: each hole pixel becomes the average of its neighbours (Laplace),
    solved coarse to fine.
    """
    H, W = hole.shape
    out = a.copy()
    if min(H, W) > 16:
        a2, h2 = _down2(a), _down2_mask(hole)
        if h2.any() and (~h2).any():
            c = _harmonic(a2, h2, iters)
            up = np.repeat(np.repeat(c, 2, 0), 2, 1)[:H, :W]
            if up.shape[0] < H or up.shape[1] < W:
                up = np.pad(up, ((0, H - up.shape[0]), (0, W - up.shape[1]), (0, 0)), mode="edge")
            out[hole] = up[hole]
        else:
            out[hole] = a[~hole].mean(0)
    else:
        out[hole] = a[~hole].mean(0) if (~hole).any() else 0
    for _ in range(iters):
        p = np.pad(out, ((1, 1), (1, 1), (0, 0)), mode="edge")
        avg = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) * 0.25
        out[hole] = avg[hole]
    return out


def _onion_fast(img, hole, check, stride=2):
    """_onion_fill sampling source patches on a coarser grid. Much faster and fine for texture."""
    a = img.astype(np.float32).copy()
    H, W, C = a.shape
    valid = ~_dilate(hole, PR)
    valid[:PR, :] = valid[-PR:, :] = False
    valid[:, :PR] = valid[:, -PR:] = False
    grid = np.zeros_like(valid)
    grid[::stride, ::stride] = True
    vy, vx = np.nonzero(valid & grid)
    if len(vy) == 0:
        return a
    offs = _VOTE_OFFS
    P = np.pad(a, ((PR, PR), (PR, PR), (0, 0)), mode="edge")
    S = np.stack([P[vy + dy + PR, vx + dx + PR] for dy, dx in offs], axis=1).reshape(len(vy), -1)
    S2 = S * S
    known = ~hole
    while not known.all():
        check()
        front = _dilate(known, 1) & ~known
        if not front.any():
            break
        fy, fx = np.nonzero(front)
        P = np.pad(a, ((PR, PR), (PR, PR), (0, 0)), mode="edge")
        Kp = np.pad(known, PR, mode="constant")
        T = np.stack([P[fy + dy + PR, fx + dx + PR] for dy, dx in offs], axis=1).reshape(len(fy), -1)
        M = np.stack([Kp[fy + dy + PR, fx + dx + PR] for dy, dx in offs], axis=1).astype(np.float32)
        M = np.repeat(M[..., None], C, axis=2).reshape(len(fy), -1)
        best = np.empty(len(fy), np.int64)
        step = max(1, 4_000_000 // max(1, len(vy)))
        for k0 in range(0, len(fy), step):
            m, t = M[k0:k0 + step], T[k0:k0 + step]
            d = (m * t * t).sum(1)[:, None] - 2.0 * (m * t) @ S.T + m @ S2.T
            best[k0:k0 + step] = np.argmin(d, axis=1)
        a[fy, fx] = a[vy[best], vx[best]]
        known |= front
    return a


def hole_depth(hole, limit=64):
    """How many pixels deep the hole goes (distance from its edge to its middle)."""
    h = hole.copy()
    d = 0
    while h.any() and d < limit:
        h &= ~_dilate(~h, 1)
        d += 1
    return d


def fill_thin(img, hole, progress=None, cancelled=None):
    """Fill a thin hole (wire, scratch, blemish): blend colour in smoothly from the edges, then
    add back texture from nearby, scaled to how textured the surroundings are.
    """
    def check():
        if cancelled is not None and cancelled():
            raise Cancelled()
    from . import imaging
    if not hole.any():
        return img.copy()
    if (~hole).sum() < PATCH * PATCH * 2:
        raise ValueError("There isn't enough picture around the area to fill it from.")
    s = img.astype(np.float32)
    hm = _harmonic(s, hole)
    check()
    on = _onion_fast(s, hole, check)
    if progress:
        progress(0.7)
    detail = on - imaging.gaussian_blur(on, 6.0)
    known = (~hole).astype(np.float32)
    hp = (s - imaging.gaussian_blur(s, 6.0))[..., :3]
    e_known = (hp * hp).sum(-1) * known
    num, den = imaging.box_blur(e_known, 8), imaging.box_blur(known, 8)
    e = np.where(den > 1e-3, num / np.maximum(den, 1e-3), 0.0).astype(np.float32)
    e_field = _harmonic(e[..., None], hole)[..., 0]
    d_en = imaging.box_blur((detail[..., :3] ** 2).sum(-1), 4)
    scale = np.clip(np.sqrt(np.maximum(e_field, 0) / np.maximum(d_en, 1e-6)), 0, 1)
    out = hm.copy()
    out[..., :3] = np.clip(hm[..., :3] + detail[..., :3] * scale[..., None], 0, 1)
    if progress:
        progress(1.0)
    return out
