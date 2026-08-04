"""法枠展開図の格子割付(ワリツケ)計算。

目標ピッチに対し、法長・展開幅にちょうど収まるよう実ピッチを
均等割付する(端部に半端な間隔が残らないようにする)。
実務の法枠設計における一般的な考え方に合わせている。
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import FrameSpec, SlopeParameters


@dataclass
class AxisLayout:
    """1方向(縦 or 横)の割付結果。"""

    span_count: int  # 分割数(スパン数)
    actual_pitch: float  # 実ピッチ (m)
    line_positions: list[float]  # 各枠中心線の位置 (0 始点から)

    @property
    def line_count(self) -> int:
        return len(self.line_positions)


@dataclass
class GridLayout:
    """展開図全体の格子割付結果。"""

    slope: SlopeParameters
    frame: FrameSpec
    vertical_axis: AxisLayout  # 法長方向(横枠の並ぶ位置)の割付
    horizontal_axis: AxisLayout  # 展開幅方向(縦枠の並ぶ位置)の割付

    @property
    def horizontal_member_count(self) -> int:
        """横枠(等高線方向、法長方向に並ぶ)の本数。"""
        return self.vertical_axis.line_count

    @property
    def vertical_member_count(self) -> int:
        """縦枠(斜面方向、展開幅方向に並ぶ)の本数。"""
        return self.horizontal_axis.line_count

    @property
    def horizontal_member_total_length(self) -> float:
        return self.horizontal_member_count * self.slope.slope_width

    @property
    def vertical_member_total_length(self) -> float:
        return self.vertical_member_count * self.slope.slope_length

    @property
    def total_frame_length(self) -> float:
        return self.horizontal_member_total_length + self.vertical_member_total_length

    @property
    def intersection_count(self) -> int:
        return self.horizontal_member_count * self.vertical_member_count

    def horizontal_lines(self) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        """横枠(法長方向位置 y の各線、x=0..W)の始点終点リスト。"""
        w = self.slope.slope_width
        return [((0.0, y), (w, y)) for y in self.vertical_axis.line_positions]

    def vertical_lines(self) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        """縦枠(展開幅方向位置 x の各線、y=0..L)の始点終点リスト。"""
        l = self.slope.slope_length
        return [((x, 0.0), (x, l)) for x in self.horizontal_axis.line_positions]


def _layout_axis(length: float, target_pitch: float) -> AxisLayout:
    if target_pitch >= length:
        span_count = 1
    else:
        span_count = max(1, round(length / target_pitch))
    actual_pitch = length / span_count
    positions = [i * actual_pitch for i in range(span_count + 1)]
    return AxisLayout(span_count=span_count, actual_pitch=actual_pitch, line_positions=positions)


def compute_grid_layout(slope: SlopeParameters, frame: FrameSpec) -> GridLayout:
    """法長・展開幅・目標ピッチから格子割付を計算する。"""
    vertical_axis = _layout_axis(slope.slope_length, frame.target_vertical_pitch)
    horizontal_axis = _layout_axis(slope.slope_width, frame.target_horizontal_pitch)
    return GridLayout(
        slope=slope,
        frame=frame,
        vertical_axis=vertical_axis,
        horizontal_axis=horizontal_axis,
    )
