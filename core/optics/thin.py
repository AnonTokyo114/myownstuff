"""理想薄透镜的近轴模型。

和 ``core/optics`` 里的曲面追迹是**两套不同的模型**，不能混用：

    thin   (y, θ) 空气中     自由传播 [[1, d], [0, 1]]
                             薄透镜   [[1, 0], [-1/f, 1]]
    surf   (y, nθ)            球面折射 + 精确 Snell

两者的差别不只是符号：
  * thin 没有球差、没有彗差、没有色差 —— 它是**理想**元件
  * thin 没有衍射 —— 光斑大小只由口径和离焦决定
  * thin 的光瞳要指定光阑后才谈得上，不是"第一面半口径"

所以摘要里必须写明"理想近轴模型，忽略像差与衍射"，否则容易误读。

单位约定：距离 mm，焦距 mm，角度弧度（内部），外部接口用度。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


EPS = 1e-12


@dataclass
class LensSpec:
    """一片理想薄透镜。

    ``distance_to_next`` 是从这片透镜到**下一片**的距离；最后一片的这个
    字段不用 —— 像距由 :func:`image_distance` 求出来。
    """

    label: str
    focal: float
    aperture: float | None = None       # 半口径；None = 不限
    distance_to_next: float = 0.0


@dataclass
class ThinSystem:
    """物面 → 若干薄透镜 → 像面。

    ``object_distance`` 是物面到第一片透镜的距离。物在无穷远时给一个
    很大的值（例如 1e9）而不是 inf，避免矩阵里出现 inf * 0。
    """

    object_distance: float
    lenses: list[LensSpec] = field(default_factory=list)

    def validate(self) -> None:
        if not self.lenses:
            raise ValueError("至少需要一片透镜。")
        if np.isnan(self.object_distance) or self.object_distance <= 0:
            raise ValueError(
                f"object_distance 必须为正，得到 {self.object_distance!r}。"
                f"物在无穷远时可以直接给 inf。")
        for lens in self.lenses:
            if not np.isfinite(lens.focal) or lens.focal == 0.0:
                raise ValueError(
                    f"{lens.label}: focal 必须是有限的非零值，"
                    f"得到 {lens.focal!r}。")
            if lens.aperture is not None and lens.aperture <= 0:
                raise ValueError(
                    f"{lens.label}: aperture 必须为正，得到 {lens.aperture!r}。")


# ----------------------------------------------------------------------
# 基本矩阵
# ----------------------------------------------------------------------
def _propagate(distance: float) -> np.ndarray:
    return np.array([[1.0, float(distance)], [0.0, 1.0]])


def _lens(focal: float) -> np.ndarray:
    return np.array([[1.0, 0.0], [-1.0 / float(focal), 1.0]])


def system_matrix(system: ThinSystem) -> np.ndarray:
    """物面到**最后一片透镜**的矩阵（不含到像面的那段）。

    物在无穷远时跳过物面到第一片透镜的传播段 —— 那时输入的 ``y, θ``
    就是光线在第一个透镜处的状态，``P(inf)`` 在数值上没有意义
    （它会把 y 放大到 1e9 量级，再被透镜除回来，全是舍入误差）。
    """
    system.validate()
    if np.isfinite(system.object_distance):
        M = _propagate(system.object_distance)
    else:
        M = np.eye(2)
    last = len(system.lenses) - 1
    for index, lens in enumerate(system.lenses):
        M = _lens(lens.focal) @ M
        if index < last:
            M = _propagate(lens.distance_to_next) @ M
    return M


# ----------------------------------------------------------------------
# 像距
# ----------------------------------------------------------------------
def image_distance(system: ThinSystem) -> float:
    """满足成像条件的像距。

    返回 ``inf`` 表示无焦系统（出射平行光），负值表示虚像。

    成像条件取决于物的位置，两者不同：

      * 有限物距 ``y_out = A·y_in + B·u_in``，要像与入射角度无关 -> ``B = 0``
      * 物在无穷远 ``u_in = 0``，要所有平行光汇聚成一点 -> ``A = 0``
        （此时 ``y_out = (A + g·C)·y_in``，与 y_in 无关才成点像）

    用 ``P(g) @ M`` 展开：
      有限物  ``B(g) = B0 + g·D0 = 0``
      无穷远  ``A(g) = A0 + g·C0 = 0``
    """
    M = system_matrix(system)
    if np.isfinite(system.object_distance):
        b0, d0 = float(M[0, 1]), float(M[1, 1])
        if abs(d0) < EPS:
            return float("inf")
        return -b0 / d0
    a0, c0 = float(M[0, 0]), float(M[1, 0])
    if abs(c0) < EPS:
        return float("inf")
    return -a0 / c0


def magnification(system: ThinSystem) -> float:
    """横向放大率（在最佳像面上）。

    物在无穷远时横向放大率无定义（物高不存在），返回 ``inf``。
    """
    if not np.isfinite(system.object_distance):
        return float("inf")
    M = system_matrix(system)
    gap = image_distance(system)
    if not np.isfinite(gap):
        return float("inf")
    return float(M[0, 0] + gap * M[1, 0])


# ----------------------------------------------------------------------
# 追迹
# ----------------------------------------------------------------------
def trace(system: ThinSystem, y0: float, theta: float,
          image_gap: float | None = None) -> dict:
    """追迹一条光线。

    ``theta`` 是弧度。``image_gap`` 为 None 时用 :func:`image_distance`
    求出的最佳像距。

    返回的 ``path`` 是 ``[(z, y), ...]``，包含物面、每片透镜和像面。
    ``blocked_at`` 非 None 表示光线在某片透镜处超过口径。
    """
    system.validate()
    if image_gap is None:
        image_gap = image_distance(system)

    y = float(y0)
    u = float(theta)
    z = 0.0
    path = [(z, y)]

    if np.isfinite(system.object_distance):
        z += system.object_distance
        y += system.object_distance * u
        path.append((z, y))
    # 物在无穷远：(y0, theta) 已经是第一片透镜处的状态

    for lens in system.lenses:
        if lens.aperture is not None and abs(y) > lens.aperture + EPS:
            return {
                "blocked_at": lens.label,
                "z": z,
                "y": y,
                "path": path,
                "y_image": None,
                "angle_image": None,
            }
        u -= y / lens.focal
        z += lens.distance_to_next
        y += lens.distance_to_next * u
        path.append((z, y))

    # 最后一片透镜之后还能有一段到像面
    if np.isfinite(image_gap):
        z += image_gap
        y += image_gap * u
        path.append((z, y))

    return {
        "blocked_at": None,
        "z": z,
        "y": y,
        "path": path,
        "y_image": y,
        "angle_image": u,
    }


def image_plane_z(system: ThinSystem) -> float:
    """像面的绝对 z（相对物面）。"""
    gap = image_distance(system)
    z = float(system.object_distance)
    last = len(system.lenses) - 1
    for index, lens in enumerate(system.lenses):
        if index < last:
            z += lens.distance_to_next
    if np.isfinite(gap):
        z += gap
    return z


# ----------------------------------------------------------------------
# 光斑（几何，无衍射）
# ----------------------------------------------------------------------
def spot(system: ThinSystem, n_rays: int = 9,
         image_gap: float | None = None) -> dict:
    """轴上光斑：按可见口径均匀采样若干条光线。

    半径取**最小的**那片透镜口径 —— 那是实际的限制光阑（在理想薄透镜
    模型里，口径限制就是光阑，不需要再求入瞳）。

    没有衍射，所以 RMS 只反映离焦和口径截断后的几何分布。
    """
    system.validate()
    if int(n_rays) < 1:
        raise ValueError(f"n_rays 至少为 1，得到 {n_rays!r}。")
    if image_gap is None:
        image_gap = image_distance(system)

    usable = [lens.aperture for lens in system.lenses
              if lens.aperture is not None]
    if not usable:
        raise ValueError(
            "所有透镜都没有给 aperture，无法定义光斑采样范围。")
    radius = min(usable)

    heights = [0.0] if n_rays == 1 else list(
        np.linspace(-radius, radius, int(n_rays)))
    hits, blocked = [], 0
    for h in heights:
        result = trace(system, h, 0.0, image_gap=image_gap)
        if result["blocked_at"] is not None:
            blocked += 1
            continue
        hits.append(result["y_image"])

    if not hits:
        raise ValueError("所有采样光线都被口径挡住。")
    arr = np.asarray(hits, dtype=float)
    return {
        "radius": radius,
        "n_requested": int(n_rays),
        "n_reached": len(hits),
        "n_blocked": blocked,
        "centroid": float(arr.mean()),
        "rms": float(arr.std()),
        "pv": float(arr.max() - arr.min()),
        "image_gap": float(image_gap) if np.isfinite(image_gap) else image_gap,
    }
