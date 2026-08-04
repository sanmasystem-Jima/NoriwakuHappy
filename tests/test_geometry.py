import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from houwaku.geometry import compute_grid_layout
from houwaku.models import FrameSpec, SlopeParameters


def test_exact_division():
    slope = SlopeParameters(slope_length=10.0, slope_width=8.0)
    frame = FrameSpec(
        target_vertical_pitch=2.0,
        target_horizontal_pitch=2.0,
        frame_width=0.3,
        frame_height=0.3,
    )
    layout = compute_grid_layout(slope, frame)

    assert layout.vertical_axis.span_count == 5
    assert layout.vertical_axis.actual_pitch == 2.0
    assert layout.horizontal_member_count == 6  # 横枠(法長方向に並ぶ)本数

    assert layout.horizontal_axis.span_count == 4
    assert layout.horizontal_axis.actual_pitch == 2.0
    assert layout.vertical_member_count == 5  # 縦枠(展開幅方向に並ぶ)本数

    assert layout.horizontal_member_total_length == 6 * 8.0
    assert layout.vertical_member_total_length == 5 * 10.0
    assert layout.intersection_count == 6 * 5


def test_uneven_division_adjusts_pitch_evenly():
    slope = SlopeParameters(slope_length=11.0, slope_width=8.0)
    frame = FrameSpec(
        target_vertical_pitch=2.0,
        target_horizontal_pitch=2.0,
        frame_width=0.3,
        frame_height=0.3,
    )
    layout = compute_grid_layout(slope, frame)

    # 11.0 / 2.0 = 5.5 -> round to 6 spans, actual pitch 11/6
    assert layout.vertical_axis.span_count == 6
    assert abs(layout.vertical_axis.actual_pitch - 11.0 / 6) < 1e-9
    # positions should span exactly from 0 to slope_length
    assert layout.vertical_axis.line_positions[0] == 0.0
    assert abs(layout.vertical_axis.line_positions[-1] - 11.0) < 1e-9


def test_pitch_larger_than_length_gives_single_span():
    slope = SlopeParameters(slope_length=1.0, slope_width=1.0)
    frame = FrameSpec(
        target_vertical_pitch=5.0,
        target_horizontal_pitch=5.0,
        frame_width=0.3,
        frame_height=0.3,
    )
    layout = compute_grid_layout(slope, frame)
    assert layout.vertical_axis.span_count == 1
    assert layout.horizontal_axis.span_count == 1
    assert layout.horizontal_member_count == 2
    assert layout.vertical_member_count == 2
