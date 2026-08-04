"""法枠断面・割付の標準仕様値。"""
from __future__ import annotations

from dataclasses import dataclass

NAKAZUME_KOSOKISOZAI = "厚層基材吹き付け工"
NAKAZUME_MORTAR = "モルタル吹き付け工"


class ScaleNotAllowedError(Exception):
    """許可されていない縮尺が指定されたときに送出する(DXFSCALE.MD §4.3)。"""


@dataclass
class FrameRule:
    """枠の断面は正方形(frame_width == frame_height)に固定。

    pitch は上限値であり、実際の間隔がこれを超えることはない
    (割付ロジックは常に pitch 以下になるよう組む)。
    """

    frame_width: float = 0.3  # 枠幅 = 枠成 (m) ※正方形
    frame_height: float = 0.3  # 枠幅と同じ値を入れる(正方形)
    pitch: float = 2.0  # 中心間距離の上限値 (m) ※300*300*2000規格の"2000"
    min_segment: float = 0.6  # 端部に許容する最小スパンの目安 (m)
    search_step: float = 0.3  # 縦柱の開始位置探索刻み (m)
    gradient_n: float | None = 1.0  # 法面勾配 1:n の n。None = 不明(現場のまま、未確定)

    @property
    def perimeter_offset(self) -> float:
        """真の境界線から周囲枠の中心線までのオフセット量。"""
        return self.frame_width / 2

    @property
    def mizukiri_cross_section_area(self) -> float | None:
        """水切りモルタルの三角断面積 = 0.3 * (0.3 * n) / 2。勾配不明ならNone。"""
        if self.gradient_n is None:
            return None
        return self.frame_width * (self.frame_width * self.gradient_n) / 2

    @property
    def cross_section_area(self) -> float:
        return self.frame_width * self.frame_height
