"""理想薄透镜的近轴模型。

和 ``core/optics`` 里的曲面追迹是**两套不同的模型**，不能混用：

    thin   (y, θ) 空气中     自由传播 [[1, d], [0, 1]]
                             薄透镜   [[1, 0], [-1/f, 1]]
    surf   (y, nθ)            球面折射 + 精确 Snell

thin 没有球差、彗差、色差，也没有衍射 —— 光斑只由口径和离焦决定。
所以摘要里必须写明"理想近轴模型，忽略像差与衍射"。

单位：距离 mm，焦距 mm，角度弧度（内部），外部接口用度。

坐标原点
--------
有限物距时 z = 0 是物面，第一片透镜在 z = object_distance。
物在无穷远时 ``object_distance`` 为 ``inf``，没有物面位置，
这时把**第一片透镜**放在 z = 0。

把 inf 加进累加式会得到 inf 或 nan，没有意义，所以这两种情形分开处理。

有没有像面
----------
``image_distance`` 返回 ``inf`` 表示无焦（出射平行光）—— 没有有限像面。
此时 ``image_plane_z`` 返回 ``None``，``trace`` 的 ``y_image`` 为 ``None``，
``sample_rays`` 不产生光斑统计。不要给一个看起来正常的光斑数值。
"""

from __future__ import annotations

import math
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

    ``object_distance`` 是物面到第一片透镜的距离；物在无穷远时给 ``inf``。
    """

    object_distance: float
    lenses: list[LensSpec] = field(default_factory=list)

    # -- 校验 ----------------------------------------------------------
    def validate(self) -> None:
        if not self.lenses:
            raise ValueError("At least one lens is required.")
        if math.isnan(self.object_distance) or self.object_distance <= 0:
            raise ValueError(
                f"object_distance must be positive, got "
                f"{self.object_distance!r}. Use inf for an object at "
                f"infinity.")

        last = len(self.lenses) - 1
        for index, lens in enumerate(self.lenses):
            if not np.isfinite(lens.focal) or lens.focal == 0.0:
                raise ValueError(
                    f"{lens.label}: focal must be finite and non-zero, "
                    f"got {lens.focal!r}.")
            if lens.aperture is not None:
                if not np.isfinite(lens.aperture) or lens.aperture <= 0:
                    raise ValueError(
                        f"{lens.label}: aperture must be a finite positive "
                        f"number or None, got {lens.aperture!r}.")
            if index < last:
                gap = lens.distance_to_next
                if not np.isfinite(gap) or gap <= 0:
                    raise ValueError(
                        f"{lens.label}: spacing to the next lens must be a "
                        f"finite positive number, got {gap!r}.")

    # -- 坐标 ----------------------------------------------------------
    @property
    def object_at_infinity(self) -> bool:
        return not np.isfinite(self.object_distance)

    def first_lens_z(self) -> float:
        """第一片透镜的 z。有限物距时物面在 0，无穷远时第一片在 0。"""
        return 0.0 if self.object_at_infinity else float(self.object_distance)

    def lens_z(self) -> list[float]:
        """所有透镜的 z。"""
        positions = []
        z = self.first_lens_z()
        last = len(self.lenses) - 1
        for index, lens in enumerate(self.lenses):
            positions.append(z)
            if index < last:
                z += lens.distance_to_next
        return positions


# ----------------------------------------------------------------------
# 基本矩阵
# ----------------------------------------------------------------------
def _propagate(distance: float) -> np.ndarray:
    return np.array([[1.0, float(distance)], [0.0, 1.0]])


def _lens(focal: float) -> np.ndarray:
    return np.array([[1.0, 0.0], [-1.0 / float(focal), 1.0]])


def system_matrix(system: ThinSystem) -> np.ndarray:
    """物面到**最后一片透镜**的矩阵（不含到像面的那段）。

    物在无穷远时跳过物面到第一片的传播段 —— 那时输入的 ``y, θ``
    就是光线在第一个透镜处的状态，``P(inf)`` 在数值上没有意义。
    """
    system.validate()
    M = np.eye(2) if system.object_at_infinity \
        else _propagate(system.object_distance)
    last = len(system.lenses) - 1
    for index, lens in enumerate(system.lenses):
        M = _lens(lens.focal) @ M
        if index < last:
            M = _propagate(lens.distance_to_next) @ M
    return M


# ----------------------------------------------------------------------
# 像距 / 放大率 / 像面
# ----------------------------------------------------------------------
def image_distance(system: ThinSystem) -> float:
    """满足成像条件的像距；``inf`` 表示无焦，负值表示虚像。

    成像条件取决于物的位置：

      * 有限物 ``y_out = A·y_in + B·u_in``，要与入射角无关 -> ``B = 0``
      * 无穷远 ``u_in = 0``，要所有平行光汇聚一点 -> ``A = 0``
    """
    M = system_matrix(system)
    if system.object_at_infinity:
        a0, c0 = float(M[0, 0]), float(M[1, 0])
        if abs(c0) < EPS:
            return float("inf")
        return -a0 / c0
    b0, d0 = float(M[0, 1]), float(M[1, 1])
    if abs(d0) < EPS:
        return float("inf")
    return -b0 / d0


def has_finite_image(system: ThinSystem) -> bool:
    return np.isfinite(image_distance(system))


def magnification(system: ThinSystem) -> float | None:
    """横向放大率；物在无穷远或无有限像面时返回 ``None``。"""
    if system.object_at_infinity:
        return None
    gap = image_distance(system)
    if not np.isfinite(gap):
        return None
    M = system_matrix(system)
    return float(M[0, 0] + gap * M[1, 0])


def image_plane_z(system: ThinSystem) -> float | None:
    """像面的绝对 z；没有有限像面时返回 ``None``。"""
    gap = image_distance(system)
    if not np.isfinite(gap):
        return None
    return system.lens_z()[-1] + gap


# ----------------------------------------------------------------------
# 追迹
# ----------------------------------------------------------------------
def trace(system: ThinSystem, entry: float, theta: float,
          image_gap: float | None = None) -> dict:
    """追迹一条光线。

    entry 是**物面高度**（有限物距）或**第一片透镜处的高度**（无穷远）。
    theta 是入射角（弧度）。

    ``image_gap`` 为 None 时用 :func:`image_distance`；像距为 inf 时
    不会再有到像面的那一段，``y_image`` 返回 ``None``。
    """
    system.validate()
    if image_gap is None:
        image_gap = image_distance(system)

    y = float(entry)
    u = float(theta)
    path: list[tuple[float, float]] = [(0.0, y)]

    if not system.object_at_infinity:
        y += system.object_distance * u
        path.append((system.object_distance, y))

    positions = system.lens_z()
    last = len(system.lenses) - 1
    for index, lens in enumerate(system.lenses):
        z = positions[index]
        # 第一片已经作为 path 的最后一个点存在（有限物是
        # (object_distance, y)，无穷远是 (0, y)），不要重复；
        # 其余每片都要在折射和口径判断**之前**记下来，这样
        # 轨迹有完整的折转点，被挡时的 path[-1] 就是停止坐标。
        if index > 0:
            path.append((z, y))
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
        if index < last:
            y += lens.distance_to_next * u

    y_image = None
    if np.isfinite(image_gap):
        y += image_gap * u
        path.append((positions[-1] + image_gap, y))
        y_image = y
    else:
        path.append((positions[-1], y))

    return {
        "blocked_at": None,
        "z": path[-1][0],
        "y": path[-1][1],
        "path": path,
        "y_image": y_image,
        "angle_image": u,
    }


# ----------------------------------------------------------------------
# 采样
# ----------------------------------------------------------------------
def entrance_radius(system: ThinSystem) -> float:
    """入口采样半径：**第一片有口径的透镜**的半口径。

    不能用全场最小口径 —— 下游透镜的束半径可能远小于它的口径，
    那样它根本不截断光线，先缩小入口等于改变了入射光束。
    遮挡由 :func:`sample_rays` 逐片检查。
    """
    for lens in system.lenses:
        if lens.aperture is not None:
            return float(lens.aperture)
    raise ValueError("No lens has an aperture, so the entrance sampling range is undefined.")


def sample_rays(system: ThinSystem, n_rays: int = 9,
                image_gap: float | None = None) -> dict:
    """生成采样光线并统计光斑。

    这是**唯一**的采样来源：绘图和摘要都从这里取，保证画出来的就是
    被统计的那些线。

    物在无穷远：扫入口高度（平行入射）。
    有限物距：同一个轴上物点，扫角度，幅值为第一片口径 / 物距。
    """
    system.validate()
    if int(n_rays) < 1:
        raise ValueError(f"n_rays must be at least 1, got {n_rays!r}.")
    if image_gap is None:
        image_gap = image_distance(system)

    radius = entrance_radius(system)

    if system.object_at_infinity:
        entries = ([0.0] if n_rays == 1
                   else list(np.linspace(-radius, radius, int(n_rays))))
        launch = [(float(h), 0.0) for h in entries]
    else:
        theta_max = radius / float(system.object_distance)
        angles = ([0.0] if n_rays == 1
                  else list(np.linspace(-theta_max, theta_max,
                                        int(n_rays))))
        launch = [(0.0, float(t)) for t in angles]

    rays = []
    for entry, theta in launch:
        result = trace(system, entry, theta, image_gap=image_gap)
        rays.append({
            "entry": entry,
            "theta": theta,
            "path": result["path"],
            "blocked_at": result["blocked_at"],
            "y_image": result["y_image"],
        })

    finite = np.isfinite(image_gap)
    reached = [r for r in rays if r["blocked_at"] is None]

    if not finite:
        return {
            "radius": radius,
            "n_requested": int(n_rays),
            "n_reached": len(reached),
            "n_blocked": len(rays) - len(reached),
            "rays": rays,
            "image_gap": float("inf"),
            "has_image": False,
            "centroid": None,
            "rms": None,
            "pv": None,
        }

    if not reached:
        raise ValueError("All sampled rays were blocked by an aperture.")
    values = np.asarray([r["y_image"] for r in reached], dtype=float)
    return {
        "radius": radius,
        "n_requested": int(n_rays),
        "n_reached": len(reached),
        "n_blocked": len(rays) - len(reached),
        "rays": rays,
        "image_gap": float(image_gap),
        "has_image": True,
        "centroid": float(values.mean()),
        "rms": float(values.std()),
        "pv": float(values.max() - values.min()),
    }


def spot(system: ThinSystem, n_rays: int = 9,
         image_gap: float | None = None) -> dict:
    """:func:`sample_rays` 的别名。"""
    return sample_rays(system, n_rays=n_rays, image_gap=image_gap)
