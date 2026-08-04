"""縦柱の部材データ。

自動割付(パターンごとの自動計算)は方針転換により廃止し、現場のCADで
手描きした中心線をレイヤー指定で読み込む方式(centerline_import.py)に
一本化した。ここには、その中心線を表す最小限のデータ構造だけを残す。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import Polygon


@dataclass
class Post:
    start: tuple[float, float]
    end: tuple[float, float]

    @property
    def x(self) -> float:
        """基準X位置(垂直配置との後方互換用。角度がある場合はstartのX)。"""
        return self.start[0]

    @property
    def y_start(self) -> float:
        return self.start[1]

    @property
    def y_end(self) -> float:
        return self.end[1]

    @property
    def length(self) -> float:
        return math.hypot(self.end[0] - self.start[0], self.end[1] - self.start[1])


@dataclass
class PostStageResult:
    centerlines: list[Polygon]
    posts: list[Post] = field(default_factory=list)
    offset: float = 0.0
    total_length: float = 0.0
