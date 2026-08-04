"""既存DXF図面から法面展開図の寸法・割付を推定して取り込む(ベストエフォート)。

任意のCAD図面を完全自動解釈することは困難なため、本モジュールは
以下の簡易的な方針で「取っ掛かりの値」を推定する:

1. 全エンティティの外形(バウンディングボックス)から法長・展開幅の候補を得る
2. LINE/LWPOLYLINEのうち水平線・垂直線をそれぞれ集め、
   その座標をクラスタリングして間隔(ピッチ)の中央値を推定する

推定結果はあくまで初期値であり、ユーザーが画面上で確認・修正する
ことを前提とする。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import ezdxf

_ANGLE_TOLERANCE = 1e-6


@dataclass
class ImportedGeometry:
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    horizontal_positions: list[float] = field(default_factory=list)  # 水平線のy座標(クラスタ後)
    vertical_positions: list[float] = field(default_factory=list)  # 垂直線のx座標(クラスタ後)

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    @property
    def estimated_horizontal_pitch(self) -> float | None:
        return _median_spacing(sorted(self.horizontal_positions))

    @property
    def estimated_vertical_pitch(self) -> float | None:
        return _median_spacing(sorted(self.vertical_positions))


def _median_spacing(sorted_values: list[float], min_gap: float = 1e-3) -> float | None:
    if len(sorted_values) < 2:
        return None
    gaps = [
        b - a
        for a, b in zip(sorted_values, sorted_values[1:])
        if (b - a) > min_gap
    ]
    if not gaps:
        return None
    gaps.sort()
    mid = len(gaps) // 2
    if len(gaps) % 2 == 0:
        return (gaps[mid - 1] + gaps[mid]) / 2
    return gaps[mid]


def _cluster(values: list[float], tolerance: float = 1e-2) -> list[float]:
    if not values:
        return []
    values = sorted(values)
    clusters: list[list[float]] = [[values[0]]]
    for v in values[1:]:
        if v - clusters[-1][-1] <= tolerance:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [sum(c) / len(c) for c in clusters]


def _iter_segments(msp):
    """LINE / LWPOLYLINE を (start, end) の線分列に正規化して返す。"""
    for entity in msp:
        dxftype = entity.dxftype()
        if dxftype == "LINE":
            yield (entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y)
        elif dxftype == "LWPOLYLINE":
            points = [(p[0], p[1]) for p in entity.get_points()]
            if entity.closed and len(points) > 1:
                points.append(points[0])
            for a, b in zip(points, points[1:]):
                yield a, b


def import_geometry(path: str) -> ImportedGeometry:
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()

    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    horizontal_ys: list[float] = []
    vertical_xs: list[float] = []

    found_any = False
    for (x1, y1), (x2, y2) in _iter_segments(msp):
        found_any = True
        min_x = min(min_x, x1, x2)
        max_x = max(max_x, x1, x2)
        min_y = min(min_y, y1, y2)
        max_y = max(max_y, y1, y2)

        if abs(y1 - y2) <= _ANGLE_TOLERANCE and abs(x1 - x2) > _ANGLE_TOLERANCE:
            horizontal_ys.append(y1)
        elif abs(x1 - x2) <= _ANGLE_TOLERANCE and abs(y1 - y2) > _ANGLE_TOLERANCE:
            vertical_xs.append(x1)

    if not found_any:
        raise ValueError("DXFファイルから図形(LINE/LWPOLYLINE)が見つかりませんでした")

    return ImportedGeometry(
        min_x=min_x,
        min_y=min_y,
        max_x=max_x,
        max_y=max_y,
        horizontal_positions=_cluster(horizontal_ys),
        vertical_positions=_cluster(vertical_xs),
    )
