"""ステージ1: 周囲枠(perimeter)の算出。

真の境界線を枠幅の半分だけ内側にオフセットした中心線を求め、
その各区間(辺)の実長を周囲枠の部材長とする。
"""
from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import Polygon

from .boundary import Tier
from .spec import FrameRule


@dataclass
class PerimeterSegment:
    start: tuple[float, float]
    end: tuple[float, float]
    length: float


@dataclass
class PerimeterResult:
    centerline: Polygon
    segments: list[PerimeterSegment]

    @property
    def total_length(self) -> float:
        return sum(s.length for s in self.segments)


def offset_centerline(polygon: Polygon, distance: float) -> Polygon:
    result = polygon.buffer(-distance, join_style="mitre", mitre_limit=10)
    if result.is_empty:
        raise ValueError("オフセット量が大きすぎて形状が消失しました")
    if result.geom_type != "Polygon":
        result = max(result.geoms, key=lambda g: g.area)
    return result


def compute_perimeter(tier: Tier, rule: FrameRule) -> PerimeterResult:
    centerline = offset_centerline(tier.polygon, rule.perimeter_offset)
    coords = list(centerline.exterior.coords)
    segments = []
    for a, b in zip(coords, coords[1:]):
        length = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
        segments.append(PerimeterSegment(start=a, end=b, length=length))
    return PerimeterResult(centerline=centerline, segments=segments)
