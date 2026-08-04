"""横梁(内部、周囲枠を除く)の部材データ。

自動割付(パターンごとの自動計算)は方針転換により廃止し、現場のCADで
手描きした中心線をレイヤー指定で読み込む方式(centerline_import.py)に
一本化した。ここには、その中心線を表す最小限のデータ構造だけを残す。
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Beam:
    points: list[tuple[float, float]]  # 折れ線(2点以上)。始点→終点の順

    @property
    def gross_length(self) -> float:
        return sum(
            math.hypot(b[0] - a[0], b[1] - a[1])
            for a, b in zip(self.points, self.points[1:])
        )

    @property
    def x_start(self) -> float:
        return self.points[0][0]

    @property
    def x_end(self) -> float:
        return self.points[-1][0]

    @property
    def y(self) -> float:
        """代表y座標(始点の高さ)。"""
        return self.points[0][1]
