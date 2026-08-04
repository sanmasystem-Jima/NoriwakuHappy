"""法面形状(展開図)の境界モデル。

展開図の座標系は x=展開幅方向(横), y=法長方向(縦、法尻から法肩に向かう)。
1段(小段で区切られた1つの面)を Tier として表し、複数段は
下から上へ順に並べた SlopeShape として扱う。
"""
from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import Polygon


@dataclass
class Tier:
    """法面1段分の境界(実測点を結んだ多角形)。"""

    polygon: Polygon

    def __post_init__(self) -> None:
        if not self.polygon.is_valid:
            raise ValueError("Tier の polygon が不正な形状です(自己交差など)")

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.polygon.bounds  # (minx, miny, maxx, maxy)


@dataclass
class SlopeShape:
    """複数段(小段区切り)をまとめた法面全体。下から上の順。"""

    tiers: list[Tier]

    def __post_init__(self) -> None:
        if not self.tiers:
            raise ValueError("SlopeShape には最低1段必要です")

    @property
    def overall_bounds(self) -> tuple[float, float, float, float]:
        minx = min(t.bounds[0] for t in self.tiers)
        miny = min(t.bounds[1] for t in self.tiers)
        maxx = max(t.bounds[2] for t in self.tiers)
        maxy = max(t.bounds[3] for t in self.tiers)
        return (minx, miny, maxx, maxy)


def rectangular_tier(slope_length: float, slope_width: float) -> Tier:
    """矩形(パターンA)の Tier を作るヘルパー。原点(0,0)を法尻左端とする。"""
    poly = Polygon([(0, 0), (slope_width, 0), (slope_width, slope_length), (0, slope_length)])
    return Tier(polygon=poly)


def trapezoid_tier(slope_length: float, width_bottom: float, width_top: float) -> Tier:
    """先すぼまり/末広がり(パターンC)の Tier を作るヘルパー。中心線をそろえる。"""
    center = width_bottom / 2
    poly = Polygon(
        [
            (0, 0),
            (width_bottom, 0),
            (center + width_top / 2, slope_length),
            (center - width_top / 2, slope_length),
        ]
    )
    return Tier(polygon=poly)
