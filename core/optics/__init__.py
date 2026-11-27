"""光学物理核心：从 myapp/ray_tracer_v3.py 抽出的纯计算部分。

来源: ray_tracer_v3.py 第 1 - 707 行（SurfaceEditor 之前）。
原文件的 tkinter GUI（SurfaceEditor / RayTraceApp）不在此处，
由 research-gui 的 PySide6 界面重写。

本模块不含任何 GUI 依赖（只有 numpy + 标准库），可独立测试：
    python -m core.optics
"""

# --- 以下内容原样来自 ray_tracer_v3.py，仅去掉原文档字符串 ---
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from typing import Optional

import numpy as np

EPS = 1e-9
DEFAULT_APERTURE = 12.5   # mm; an explicit design default, NOT derived from R
MAX_EVENTS = 80           # interface events per ray before giving up
MAX_TOTAL_RAYS = 20000    # refuse to trace more than this in one click


@dataclass(frozen=True)
class Surface:
    name: str
    R: float                              # curvature radius (mm), inf = plane
    t: float                              # vertex spacing to next surface (mm)
    n: float                              # index on the +z side of this surface
    semi_aper: Optional[float] = None     # clear semi-aperture (mm)


# --------------------------------------------------------------------------
#  Geometry helpers
# --------------------------------------------------------------------------

def vertex_positions(surfaces):
    """z of every vertex, with the object plane fixed at z = 0."""
    z = [0.0]
    for surface in surfaces[:-1]:
        z.append(z[-1] + surface.t)
    return np.asarray(z, dtype=float)


def aperture_of(surface, index, size):
    """Clear semi-aperture; object and image planes are unbounded."""
    if surface.semi_aper is not None:
        return surface.semi_aper
    return math.inf if index in (0, size - 1) else DEFAULT_APERTURE


def surface_z(y, R, vertex):
    """z(y) on the physical cap that contains the vertex (correct branch)."""
    if math.isinf(R):
        return np.full_like(np.asarray(y, dtype=float), vertex, dtype=float)
    y = np.asarray(y, dtype=float)
    return vertex + R - math.copysign(1.0, R) * np.sqrt(np.maximum(0.0, R * R - y * y))


def validate_surfaces(surfaces):
    """Check the lens prescription is physically realisable; return vertices."""
    if len(surfaces) < 2:
        raise ValueError('At least an object plane and an image plane are required.')
    z = vertex_positions(surfaces)
    N = len(surfaces)

    for i, s in enumerate(surfaces):
        if not math.isfinite(s.n) or s.n <= 0:
            raise ValueError(f'{s.name}: refractive index must be positive and finite.')
        if math.isnan(s.R) or s.R == 0 or s.R == -math.inf:
            raise ValueError(f'{s.name}: radius must be a nonzero finite value or +inf.')
        is_last = (i == N - 1)
        # The last surface has no successor, so its t is decoration: allow 0.
        if not math.isfinite(s.t) or (not is_last and s.t <= 0) or (is_last and s.t < 0):
            what = 'may not be negative' if is_last else 'must be > 0'
            raise ValueError(f'{s.name}: spacing to the next vertex {what}.')
        a = aperture_of(s, i, N)
        if math.isnan(a) or a <= 0:
            raise ValueError(f'{s.name}: clear semi-aperture must be positive.')
        if not math.isinf(s.R) and (math.isinf(a) or a >= abs(s.R)):
            raise ValueError(f'{s.name}: semi-aperture must be smaller than '
                             f'|R|={abs(s.R):g} mm (only that much of the cap exists).')

    if not math.isinf(surfaces[0].R) or not math.isinf(surfaces[-1].R):
        raise ValueError('The first (object) and last (image) surfaces must be planes.')
    if not math.isclose(surfaces[-1].n, surfaces[-2].n, abs_tol=1e-10):
        raise ValueError('The image plane must not change refractive index.')

    # Adjacent caps must stay ordered over their common clear aperture, else the
    # lens profile would be self-intersecting.
    for i in range(N - 1):
        a = min(aperture_of(surfaces[i], i, N),
                aperture_of(surfaces[i + 1], i + 1, N))
        limits = [abs(s.R) for s in surfaces[i:i + 2] if math.isfinite(s.R)]
        if math.isinf(a):
            a = min(limits) * 0.999 if limits else DEFAULT_APERTURE
        y = np.linspace(-a, a, 101)
        left = surface_z(y, surfaces[i].R, z[i])
        right = surface_z(y, surfaces[i + 1].R, z[i + 1])
        if np.any(left >= right - 1e-8):
            raise ValueError(f"Surfaces '{surfaces[i].name}' and "
                             f"'{surfaces[i + 1].name}' overlap or cross within "
                             'their common clear aperture.')
    return z


def _unit(d):
    """Normalise a 2-vector, tolerating the already-normalised common case."""
    norm = math.hypot(float(d[0]), float(d[1]))
    if norm <= 0.0:
        raise ValueError('ray direction has zero length')
    if abs(norm - 1.0) <= 1e-12:
        return np.asarray(d, dtype=float)
    return np.asarray(d, dtype=float) / norm


def intersect_surface(P, d, s, z_v, aperture):
    """Nearest forward intersection with the vertex-side cap, inside `aperture`.

    P, d use [y, z] ordering. Returns None when the ray misses the modelled
    clear surface -- there is deliberately no fictitious fallback refraction.

    `d` is normalised on entry: the quadratic below assumes |d| = 1, and silently
    accepting a scaled direction used to return a wrong intersection point.
    """
    d = _unit(d)
    P = np.asarray(P, dtype=float)

    if math.isinf(s.R):
        if abs(d[1]) < 1e-13:
            return None
        t = (z_v - P[1]) / d[1]
        if t <= EPS:
            return None
        Q = P + t * d
        return Q if abs(Q[0]) <= aperture + EPS else None

    center = np.array([0.0, z_v + s.R])
    rel = P - center
    b = 2.0 * np.dot(rel, d)
    c = np.dot(rel, rel) - s.R * s.R
    disc = b * b - 4.0 * c          # a = |d|^2 = 1
    if disc < -1e-10:
        return None
    root = math.sqrt(max(0.0, disc))
    candidates = sorted(((-b - root) / 2.0, (-b + root) / 2.0))
    for t in candidates:
        if t <= EPS:
            continue
        Q = P + t * d
        # Reject the far hemisphere: keep only the cap that owns the vertex.
        if math.copysign(1.0, s.R) * (Q[1] - center[1]) > 1e-8:
            continue
        if abs(Q[0]) > aperture + EPS:
            continue
        return Q
    return None


def oriented_normal(Q, direction, R, z_v):
    """Unit surface normal, flipped to face against the incident ray."""
    if math.isinf(R):
        normal = np.array([0.0, -1.0])
    else:
        normal = np.asarray(Q, dtype=float) - np.array([0.0, z_v + R])
        normal = normal / np.linalg.norm(normal)
    if np.dot(normal, direction) > 0:
        normal = -normal
    return normal


def snell_or_reflect(d, normal, n1, n2):
    """Refract d across `normal` from n1 into n2; fall back to reflection on TIR.

    Returns (outgoing unit direction, total_internal_reflection_flag).
    """
    d = _unit(d)
    ci = float(np.clip(-np.dot(d, normal), 0.0, 1.0))   # cosine of incidence
    eta = n1 / n2
    k = 1.0 - eta ** 2 * (1.0 - ci ** 2)                # cos^2 of refraction
    if k < -1e-12:
        reflected = d - 2.0 * np.dot(d, normal) * normal
        return reflected / np.linalg.norm(reflected), True
    # k in [-1e-12, 0] is a grazing refraction: the sqrt clamp keeps it finite.
    outgoing = eta * (d + ci * normal) - math.sqrt(max(0.0, k)) * normal
    return outgoing / np.linalg.norm(outgoing), False


def project_to_plane(P, d, z_v):
    """Where the ray *would* cross the vertex plane. Visualisation only."""
    if abs(d[1]) > EPS:
        t = (z_v - P[1]) / d[1]
        if t > EPS:
            return P + t * d
    return np.asarray(P, dtype=float).copy()


def _make_ray(y0, angle_deg, path, status, reflections, final_dir, n_curr,
              stop_point=None):
    return {
        'y0': y0,
        'angle': angle_deg,
        'path': path,
        'status': status,
        'reflections': reflections,
        'final_dir': np.asarray(final_dir, dtype=float).copy(),
        'n': n_curr,
        #: Where the ray stopped when it was clipped; None if it reached the image.
        'stop_point': stop_point,
    }


# --------------------------------------------------------------------------
#  Exact tracing
# --------------------------------------------------------------------------

def trace_exact(surfaces, z, y0, angle_deg):
    """Sequential exact tracing; direction may reverse after total reflection.

    `path` holds only genuinely visited intersections. A clipped ray additionally
    reports `stop_point`, the projection onto the plane it failed to reach, which
    the plot draws as a red cross.
    """
    theta = math.radians(float(angle_deg))
    P = np.array([float(y0), 0.0])
    d = np.array([math.sin(theta), math.cos(theta)])
    path = [(float(P[1]), float(P[0]))]
    n_curr = surfaces[0].n
    step = 1          # +1 travels toward the image, -1 back toward the object
    j = 1
    reflections = 0
    status = 'Event limit'
    stop_point = None

    for _ in range(MAX_EVENTS):
        if j < 0 or j >= len(surfaces):
            status = 'Exited modelled system'
            break

        if j == 0 and step == -1:
            a0 = aperture_of(surfaces[0], 0, len(surfaces))
            Q = intersect_surface(P, d, surfaces[0], z[0], a0)
            if Q is not None:
                path.append((float(Q[1]), float(Q[0])))
            status = 'Returned to object plane'
            break

        # The medium we are actually in must match the interface we approach.
        expected = surfaces[j - 1].n if step == 1 else surfaces[j].n
        if not math.isclose(n_curr, expected, rel_tol=1e-9, abs_tol=1e-9):
            status = 'Medium/geometry mismatch'
            break

        s = surfaces[j]
        a = aperture_of(s, j, len(surfaces))
        Q = intersect_surface(P, d, s, z[j], a)
        if Q is None:
            stop_point = project_to_plane(P, d, z[j])
            status = f'Vignetted / missed S{j}'
            break

        P = Q
        path.append((float(P[1]), float(P[0])))
        if j == len(surfaces) - 1:
            status = 'Reached image plane'
            break

        # Index on the far side of this interface, for the current direction.
        n_next = surfaces[j].n if step == 1 else surfaces[j - 1].n
        # Aperture stop, dummy plane, or an index-matched boundary: no bending.
        if math.isclose(n_next, n_curr, rel_tol=1e-12, abs_tol=1e-12):
            j += step
            continue

        normal = oriented_normal(P, d, s.R, z[j])
        d_new, reflected = snell_or_reflect(d, normal, n_curr, n_next)
        new_step = 1 if d_new[1] > 1e-10 else -1 if d_new[1] < -1e-10 else 0
        if new_step == 0:
            status = 'Grazing propagation (unsupported)'
            break

        if reflected:
            reflections += 1
            # Reflecting off the same cap we just met is a non-sequential path.
            if new_step == step:
                status = 'Non-sequential grazing reflection'
                break
            d = d_new
            step = new_step
            j += step
            continue

        if new_step != step:
            status = 'Non-sequential reverse refraction'
            break
        d = d_new
        n_curr = n_next
        j += step

    return _make_ray(y0, angle_deg, path, status, reflections, d, n_curr, stop_point)


# --------------------------------------------------------------------------
#  Paraxial tracing
# --------------------------------------------------------------------------

def trace_paraxial(surfaces, z, y0, angle_deg):
    """First-order tracing in (y, u = n*theta) at each vertex plane.

    Small-angle only: it uses no surface sag and ignores TIR, both of which are
    outside paraxial optics' regime of validity.
    """
    y = float(y0)
    n_curr = surfaces[0].n
    u = n_curr * math.radians(float(angle_deg))
    path = [(float(z[0]), y)]
    status = 'Reached image plane'
    stop_point = None

    for j in range(1, len(surfaces)):
        dz = z[j] - z[j - 1]
        y += dz * u / n_curr
        path.append((float(z[j]), float(y)))
        if abs(y) > aperture_of(surfaces[j], j, len(surfaces)) + EPS:
            stop_point = np.array([float(y), float(z[j])])
            status = f'Vignetted at S{j}'
            break
        if j == len(surfaces) - 1:
            break
        n_next = surfaces[j].n
        if math.isfinite(surfaces[j].R):
            u -= ((n_next - n_curr) / surfaces[j].R) * y
        n_curr = n_next

    direction = np.array([u / n_curr, 1.0], dtype=float)
    direction /= np.linalg.norm(direction)
    return _make_ray(y0, angle_deg, path, status, 0, direction, n_curr, stop_point)


def trace_rays(surfaces, object_heights, angles_deg, mode='Exact'):
    """Trace every (height, angle) pair. Returns (rays, vertex_positions)."""
    if mode not in ('Exact', 'Paraxial'):
        raise ValueError("mode must be 'Exact' or 'Paraxial'.")
    heights = [float(h) for h in object_heights]
    angles = [float(a) for a in angles_deg]
    total = len(heights) * len(angles)
    if total > MAX_TOTAL_RAYS:
        raise ValueError(f'That would trace {total} rays; the limit is '
                         f'{MAX_TOTAL_RAYS}. Reduce the heights or the ray count.')
    z = validate_surfaces(surfaces)
    tracer = trace_exact if mode == 'Exact' else trace_paraxial
    rays = []
    for group, height in enumerate(heights):
        for angle in angles:
            ray = tracer(surfaces, z, height, angle)
            # Carrying the group index avoids heights.index(), which collapsed
            # duplicate object heights onto a single colour.
            ray['group'] = group
            rays.append(ray)
    return rays, z


# --------------------------------------------------------------------------
#  Drawing
# --------------------------------------------------------------------------

def find_lens_pairs(surfaces):
    """Indices (front, back) of homogeneous elements denser than their surround.

    A pair qualifies when surface i changes index, surface i+1 restores the
    original index, AND the enclosed medium is denser than the surroundings.

    That last condition matters: without it the 20 mm air gap between two lenses
    is also "a medium bounded by glass" and gets filled as if it were an element
    (the previous version filled 3 blocks for the default 2-lens layout).

    Limitation: a low-index element (an air bubble inside glass) is not filled.
    Such a geometry is outside what this sequential 2D model claims to draw.
    """
    pairs = []
    N = len(surfaces)
    for i in range(1, N - 1):
        n_in, n_mid, n_out = surfaces[i - 1].n, surfaces[i].n, surfaces[i + 1].n
        if (not math.isclose(n_mid, n_in, abs_tol=1e-10)
                and math.isclose(n_out, n_in, abs_tol=1e-10)
                and n_mid > n_in):
            pairs.append((i, i + 1))
    return pairs


def draw_surfaces(ax, surfaces, z):
    """Draw the curved profiles and fill homogeneous elements."""
    N = len(surfaces)
    pairs = find_lens_pairs(surfaces)
    lens_indices = set()
    for front, back in pairs:
        a = min(aperture_of(surfaces[front], front, N),
                aperture_of(surfaces[back], back, N))
        ys = np.linspace(-a, a, 200)
        fz = surface_z(ys, surfaces[front].R, z[front])
        bz = surface_z(ys, surfaces[back].R, z[back])
        ax.fill_betweenx(ys, fz, bz, color='lightskyblue', alpha=0.30, zorder=0)
        ax.plot([fz[0], bz[0]], [ys[0], ys[0]], color='steelblue', lw=1)
        ax.plot([fz[-1], bz[-1]], [ys[-1], ys[-1]], color='steelblue', lw=1)
        lens_indices.add(front)
        lens_indices.add(back)

    for i in range(1, N - 1):
        s = surfaces[i]
        a = aperture_of(s, i, N)
        ys = np.linspace(-a, a, 200)
        ax.plot(surface_z(ys, s.R, z[i]), ys,
                color='royalblue' if i in lens_indices else 'slategray', lw=1.2)

    ax.axvline(z[0], color='gray', ls=':', alpha=.5)
    ax.axvline(z[-1], color='gray', ls='--', alpha=.65)
    ax.axhline(0, color='black', lw=.65, alpha=.7)
    for i, s in enumerate(surfaces):
        ax.text(z[i], 0.97, f'S{i}: {s.name}', rotation=90, fontsize=7,
                ha='right', va='top', alpha=.75, transform=ax.get_xaxis_transform())


# --------------------------------------------------------------------------
#  Image-quality analysis
#
#  Conventions used below, stated once so the numbers are interpretable:
#    * The paraxial state is (y, u = n*theta), i.e. u carries the refractive index.
#    * Focal properties come from the ABCD matrix built between the FIRST and the
#      LAST REAL surface, so BFL/FFL are measured from those two vertices (the
#      object and image planes are excluded on purpose).
#    * Spot statistics assume an object at infinity, so "object height" at z=0 IS
#      the entrance-pupil height and the ray angle is the field angle. A spot is
#      therefore "fix the field angle, sweep the pupil height".
#    * All RAYS ARE MERIDIONAL. The RMS reported here is the meridional RMS, not a
#      true 2D/3D RMS spot radius; it also cannot show sagittal-only aberrations.
# --------------------------------------------------------------------------

def system_matrix(surfaces, z):
    """Paraxial ABCD matrix acting on (y, u = n*theta).

    Built from the first real surface to the last real surface. Object and image
    planes are planar and index-matched, so including them would change nothing
    but the measurement origin; they are excluded so BFL/FFL are referenced to
    the outer real vertices, which is the usual convention.
    """
    N = len(surfaces)
    if N < 3:
        raise ValueError('Need at least one real surface between object and image.')
    M = np.eye(2)
    n_prev = surfaces[0].n

    s1 = surfaces[1]
    if math.isfinite(s1.R) and not math.isclose(s1.n, n_prev, abs_tol=1e-12):
        M = np.array([[1.0, 0.0], [-(s1.n - n_prev) / s1.R, 1.0]]) @ M
    n_prev = s1.n

    for j in range(2, N - 1):
        d = z[j] - z[j - 1]
        M = np.array([[1.0, d / n_prev], [0.0, 1.0]]) @ M
        s = surfaces[j]
        if math.isfinite(s.R) and not math.isclose(s.n, n_prev, abs_tol=1e-12):
            M = np.array([[1.0, 0.0], [-(s.n - n_prev) / s.R, 1.0]]) @ M
        n_prev = s.n
    return M


def focal_properties(surfaces, z):
    """EFL / BFL / FFL and principal-plane positions from the ABCD matrix.

    BFL and FFL are multiplied by the refractive index of the medium they sit in:
    with (y, u = n*theta) the outgoing slope is u_out/n_out, so the vertex-to-focus
    distance is -y_out*n_out/u_out, not -y_out/u_out. A single refracting surface
    (n_out = 1.5, R = 50) is the counter-example that catches this: omitting n_out
    reports 100 mm when the focus really is 150 mm past the vertex.

    Definitions returned (all lengths in mm, +z to the right):
      efl      effective focal length
      bfl      last real vertex -> rear focus
      ffl      first real vertex -> front focus (positive = to the left)
      h_front  front principal plane, relative to the first real vertex
      h_back   rear principal plane, relative to the last real vertex
    """
    M = system_matrix(surfaces, z)
    A, C, D = M[0, 0], M[1, 0], M[1, 1]
    if abs(C) < 1e-15:
        raise ValueError('No optical power (C = 0): a plane-parallel plate or an empty system has no focal length.')
    n_in = surfaces[0].n
    n_out = surfaces[-2].n

    # 物方/像方焦距**不相等**（除非 n_in == n_out）：
    #   f_front = -n_in / C,   f_back = -n_out / C
    # 主平面要拿各自那边的焦距算，不能都用 -1/C：
    #   h_front = f_front - ffl = n_in * (D - 1) / C
    #   h_back  = bfl - f_back = n_out * (1 - A) / C
    #
    # 单球面 n_in=1, n_out=1.5, R=50 的例子能验出来：A=D=1, C=-0.01，
    # 两主平面都该在顶点（0），旧写法给出 h_back=50。
    # 全空气系统里 n_in == n_out == 1，两种写法重合，所以默认案例看不出来。
    f_front = -n_in / C
    f_back = -n_out / C
    bfl = -A * n_out / C
    ffl = -D * n_in / C
    return {
        'matrix': M,
        'efl': -1.0 / C,            # 约化焦距（= 单位折射率下的焦距）
        'f_front': f_front,
        'f_back': f_back,
        'bfl': bfl,
        'ffl': ffl,
        'h_front': f_front - ffl,
        'h_back': bfl - f_back,
        'n_in': n_in,
        'n_out': n_out,
        'power': C * -1.0,          # 光焦度 1/EFL 的等价量，仅供显示
    }


def pupil_limit(surfaces, z):
    """Entrance-pupil height limit: semi-aperture of the first finite aperture."""
    N = len(surfaces)
    for i in range(1, N - 1):
        a = aperture_of(surfaces[i], i, N)
        if math.isfinite(a):
            return float(a)
    return float(DEFAULT_APERTURE)


def _with_image_gap(surfaces, gap):
    """Return a copy with the last real surface's spacing replaced."""
    out = list(surfaces)
    last = out[-2]
    out[-2] = Surface(last.name, last.R, float(gap), last.n, last.semi_aper)
    return out


def spot_analysis(surfaces, z, field_deg, n_pupil=9, image_gap=None):
    """Spot statistics at one field: fix the angle, sweep the pupil height.

    Returns a dict with RMS / peak-to-valley / centroid, plus how many rays were
    vignetted. RMS is given both about the centroid and about the chief ray.
    """
    field_deg = float(field_deg)
    if image_gap is None:
        system, zz = surfaces, z
    else:
        system = _with_image_gap(surfaces, image_gap)
        zz = validate_surfaces(system)

    pmax = pupil_limit(system, zz)
    if n_pupil < 2:
        heights = [0.0]
    else:
        # 光线起点在 z=0，但**物理上有意义的通光范围在第一个面上**。
        # trace_exact 的 y0 是 z=0 处的高度，离轴时整束光到第一面会
        # 平移 z_first*tan(θ) —— 若直接在 z=0 铺开 [-pmax, pmax]，
        # 第一面处的光束就会偏出孔径，t0 一变 RMS 和渐晕跟着变。
        # 无穷远物点的成像结果不应依赖 t0，所以这里按第一面反推起点：
        #     y_first = y0 + z_first*tan(θ)  =>  y0 = y_first - z_first*tan(θ)
        z_first = float(zz[1]) if len(zz) > 1 else 0.0
        slope = math.tan(math.radians(field_deg))
        heights = [float(h - z_first * slope)
                   for h in np.linspace(-pmax, pmax, n_pupil)]

    rays, _ = trace_rays(system, heights, [field_deg], 'Exact')
    pts, chief, vignetted = [], None, 0
    for ray in rays:
        if ray['status'] != 'Reached image plane':
            vignetted += 1
            continue
        y_img = float(ray['path'][-1][1])
        pts.append(y_img)
        if chief is None and abs(ray['y0']) < 1e-12:
            chief = y_img
    if not pts:
        raise ValueError(f'No ray reached the image at field {field_deg:g} deg (everything was vignetted).')

    arr = np.asarray(pts, dtype=float)
    centroid = float(arr.mean())
    rms_centroid = float(arr.std())
    rms_chief = (float(np.sqrt(np.mean((arr - chief) ** 2)))
                 if chief is not None else None)
    return {
        'field': field_deg,
        'pupil_max': pmax,
        'n_reached': len(pts),
        'n_vignetted': vignetted,
        'centroid': centroid,
        'rms': rms_centroid,
        'rms_about_chief': rms_chief,
        'pv': float(arr.max() - arr.min()),
        'chief': chief,
        'points': arr,
    }


def best_focus(surfaces, z, field_deg=0.0, gap_range=None, n_scan=21, n_pupil=9):
    """Scan the image-plane spacing and report the minimum spot RMS.

    This removes the manual "nudge the last thickness and look" loop: for each
    candidate spacing the system is re-validated and re-traced.
    """
    base_gap = float(surfaces[-2].t)
    if gap_range is None:
        lo = max(1e-3, base_gap * 0.55)
        hi = base_gap * 1.55
    else:
        lo, hi = float(gap_range[0]), float(gap_range[1])
    if hi <= lo:
        raise ValueError('Invalid focus-scan range.')

    def scan(gaps):
        out = []
        for gap in gaps:
            try:
                r = spot_analysis(surfaces, z, field_deg, n_pupil=n_pupil,
                                  image_gap=float(gap))
            except Exception:
                continue
            out.append((float(gap), float(r['rms']), int(r['n_reached'])))
        return out

    def pick(samples):
        """在保持完整通光的位置里挑最小 RMS。

        比较候选焦面时如果幸存光线集合会变，RMS 的下降可能只是把边缘
        大像差光线切掉了 —— 像面口径一收紧，严重离焦处只剩中心一条，
        RMS 直接归零。那种位置不能算"更优"。
        """
        if not samples:
            return None, 0
        full = max(item[2] for item in samples)
        usable = [item for item in samples if item[2] >= full]
        return min(usable, key=lambda item: item[1]), full

    coarse = scan(np.linspace(lo, hi, max(3, n_scan)))
    if not coarse:
        raise ValueError('The focus scan produced no valid image plane.')

    best, full = pick(coarse)
    if best is None or full < n_pupil:
        raise ValueError(
            f'The tightest aperture only let {full} of {n_pupil} rays '
            f'through at every trial spacing, so the scan cannot compare '
            f'image quality. Widen the image-plane aperture, or reduce '
            f'n_pupil to match what actually gets through.')

    # 粗扫只保证落在步长内；在最优点左右一个步长内再细化一次，
    # 否则 21 点扫描在 40 mm 上的步长是 2 mm，会直接跨过真实焦面。
    step = (hi - lo) / max(2, n_scan - 1)
    fine_lo = max(lo, best[0] - step)
    fine_hi = min(hi, best[0] + step)
    # 101 点是为了让最优点精度达到步长的 1/100：
    # 21 点在 4 mm 区间上步长 0.2 mm，还会差出几十微米。
    fine = scan(np.linspace(fine_lo, fine_hi, 101))
    if fine:
        refined, _ = pick(fine)
        if refined is not None and refined[1] < best[1]:
            best = refined

    curve = [(gap, rms) for gap, rms, _n in coarse]
    return {
        'base_gap': base_gap,
        'best_gap': best[0],
        'best_rms': best[1],
        'curve': curve,
        'n_reached': best[2],
    }


def longitudinal_spherical(surfaces, z, pupil_heights=None, n_samples=7):
    """Axial focus position versus pupil height (longitudinal spherical aberration).

    For each pupil height a parallel ray is traced and its paraxial-axis crossing
    is computed after the last real surface. The crossing is expressed relative
    to the **last surface vertex** (z[-2]) so that every ray shares one reference
    plane — using each ray's own intersection z would fold in the surface sag,
    which varies with pupil height. LSA is quoted relative to the smallest
    sampled height, which stands in for the paraxial limit.
    """
    pmax = pupil_limit(surfaces, z)
    if pupil_heights is None:
        pupil_heights = np.linspace(max(1e-6, pmax * 0.08), pmax, n_samples)

    rows = []
    for h in pupil_heights:
        h = float(h)
        try:
            rays, _ = trace_rays(surfaces, [h], [0.0], 'Exact')
        except Exception:
            rows.append({'h': h, 'focus': None, 'lsa': None, 'status': 'trace error'})
            continue
        ray = rays[0]
        if ray['status'] != 'Reached image plane' or len(ray['path']) < 2:
            rows.append({'h': h, 'focus': None, 'lsa': None, 'status': ray['status']})
            continue
        z_b, y_b = ray['path'][-2]
        d = ray['final_dir']
        if abs(d[1]) < 1e-12:
            rows.append({'h': h, 'focus': None, 'lsa': None, 'status': 'no slope'})
            continue
        slope = d[0] / d[1]
        if abs(slope) < 1e-15:
            rows.append({'h': h, 'focus': None, 'lsa': None, 'status': 'parallel'})
            continue
        # 每条光线都与最后曲面交于不同的 z（球面上 y 不同 => z 不同/sag 差）。
        # 若只算 -y_b/slope，得到的是「相对该交点」的距离，各光线参考面不同，
        # 相减会混入 sag 差。所以先换成绝对 z，再统一减到最后曲面的顶点 z[-2]。
        focus_abs = z_b - y_b / slope
        rows.append({'h': h, 'focus': float(focus_abs - z[-2]), 'lsa': None,
                     'status': ray['status']})

    valid = [r for r in rows if r['focus'] is not None]
    if not valid:
        raise ValueError('No pupil height produced a valid focus; cannot compute LSA.')
    ref = valid[0]['focus']
    for r in rows:
        if r['focus'] is not None:
            r['lsa'] = r['focus'] - ref
    return rows


# --------------------------------------------------------------------------
#  Tkinter interface
# --------------------------------------------------------------------------
