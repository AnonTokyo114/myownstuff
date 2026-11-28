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

有没有像面
----------
``image_distance`` 返回 ``inf`` 表示无焦（出射平行光）—— 没有有限像面。
此时 ``image_plane_z`` 返回 ``None``，``trace`` 的 ``y_image`` 为 ``None``，
``sample_fans`` 不给任何像面统计。

物点与光线扇
------------
``ObjectFan`` 描述"物面上某一点发出的一束光线"：高度、中心角、条数。
角度按中心对称展开（1 条：θc；3 条：θc±Δθ；5 条：θc±2Δθ）。

用户给的角度就是实际发射角；口径只负责逐片判断遮挡，**不会**为了
让光线通过而缩小角度范围。被挡的光线保留到真实停止位置。

``sample_fans`` 按物点分组统计，绝不把不同物点的像点混在一起算 RMS。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


EPS = 1e-12

#: 同一个物点的光线用同一种颜色；三组固定顺序
FAN_COLOURS = ("#c0392b", "#1f6feb", "#1a7f37")


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
class ObjectFan:
    """物面上一个点发出的光线扇。

    ``height`` 是物点高度，``centre_deg`` 是中心发射角，
    ``count`` 是条数（1/3/5）。``colour`` 只用于绘图。
    """

    height: float
    centre_deg: float = 0.0
    count: int = 3
    label: str = ""
    colour: str = ""

    def angles_deg(self, interval_deg: float) -> list[float]:
        """按中心对称展开的角度列表（度）。

        1 条： θc
        3 条： θc-Δθ, θc, θc+Δθ
        5 条： θc-2Δθ, θc-Δθ, θc, θc+Δθ, θc+2Δθ
        """
        count = int(self.count)
        if count < 1:
            raise ValueError(
                f"{self.label or 'fan'}: count must be >= 1, "
                f"got {self.count!r}.")
        if count == 1:
            return [float(self.centre_deg)]
        half = (count - 1) // 2
        return [float(self.centre_deg) + k * float(interval_deg)
                for k in range(-half, half + 1)]


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
    elif path[-1][0] != positions[-1]:
        # 无焦：没有像面。末片顶点可能还没记录（单片时不会进
        # index>0 分支），也可能已经记过（多片时循环里加了）。
        # 用坐标判重，避免多出一个零长度段。
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
# 采样：单光束（旧接口，保留）
# ----------------------------------------------------------------------
def entrance_radius(system: ThinSystem) -> float:
    """入口采样半径：**第一片有口径的透镜**的半口径。

    不能用全场最小口径 —— 下游透镜的束半径可能远小于它的口径，
    那样它根本不截断光线，先缩小入口等于改变了入射光束。
    遮挡由 :func:`sample_rays` 逐片检查。

    注意：第一片口径为 None 时这是"第一片有值的那片"的物理半口径，
    不是把下游口径折算回入口的结果。需要完整入口通光统计时应当显式
    给定照明范围。
    """
    for lens in system.lenses:
        if lens.aperture is not None:
            return float(lens.aperture)
    raise ValueError("No lens has an aperture, so the entrance sampling "
                     "range is undefined.")


def sample_rays(system: ThinSystem, n_rays: int = 9,
                image_gap: float | None = None) -> dict:
    """生成采样光线并统计光斑（单一光束）。

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


# ----------------------------------------------------------------------
# 采样：多物点光线扇
# ----------------------------------------------------------------------
def sample_fans(system: ThinSystem, fans: list[ObjectFan],
                interval_deg: float,
                image_gap: float | None = None) -> dict:
    """对每个物点采样它的光线扇，并按物点**分组**统计。

    分组是必须的：三个物点成像到像面上三个不同的位置，混在一起算
    RMS 得到的是"三个像点之间的距离"，不是光斑大小。

    每组独立判断：

      * ``n_reached == 0``   该组全部被挡，其他组照常显示
      * ``n_reached < 2``    采样不足，不给 RMS（标准差没有意义）
      * 其余                 正常给质心、RMS、PV

    物在无穷远时，``fan.height`` 是**入口高度**，各条光线是**不同入射
    方向**的平行光；有限物距时它是物面高度，光线从该点出发。
    """
    system.validate()
    if not fans:
        raise ValueError("At least one object fan is required.")
    if image_gap is None:
        image_gap = image_distance(system)
    finite = np.isfinite(image_gap)

    groups = []
    for index, fan in enumerate(fans):
        colour = fan.colour or FAN_COLOURS[index % len(FAN_COLOURS)]
        angles_deg = fan.angles_deg(interval_deg)

        rays = []
        for angle_deg in angles_deg:
            theta = math.radians(angle_deg)
            result = trace(system, float(fan.height), theta,
                           image_gap=image_gap)
            rays.append({
                "entry": float(fan.height),
                "theta": theta,
                "theta_deg": float(angle_deg),
                "path": result["path"],
                "blocked_at": result["blocked_at"],
                "y_image": result["y_image"],
            })

        reached = [r for r in rays if r["blocked_at"] is None]
        values = [r["y_image"] for r in reached
                  if r["y_image"] is not None]

        label = fan.label or f"object {index + 1}"
        common = {
            "fan": fan,
            "label": label,
            "colour": colour,
            "height": float(fan.height),
            "angles_deg": angles_deg,
            "rays": rays,
            "n_requested": len(rays),
            "n_reached": len(reached),
            "n_blocked": len(rays) - len(reached),
        }

        if not finite:
            groups.append({**common, "has_image": False,
                           "sufficient": False, "centroid": None,
                           "rms": None, "pv": None})
            continue

        sufficient = len(values) >= 2
        array = np.asarray(values, dtype=float) if values else np.array([])
        groups.append({
            **common,
            "has_image": True,
            "sufficient": sufficient,
            "centroid": float(array.mean()) if sufficient else None,
            "rms": float(array.std()) if sufficient else None,
            "pv": float(array.max() - array.min()) if sufficient else None,
        })

    return {
        "groups": groups,
        "image_gap": float(image_gap) if finite else float("inf"),
        "has_image": finite,
        "interval_deg": float(interval_deg),
    }


def object_heights(centre: float, d12: float, d23: float) -> tuple[float, ...]:
    """由中心高度和两侧间距算三个物点高度：y1 = y2-d12, y3 = y2+d23。"""
    centre = float(centre)
    return (centre - float(d12), centre, centre + float(d23))
