import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from houwaku.geometry import compute_grid_layout
from houwaku.methods import get_calculator
from houwaku.models import (
    CastInPlaceOptions,
    FrameSpec,
    MethodType,
    PrecastOptions,
    ProjectInput,
    ShotcreteOptions,
    SlopeParameters,
)


def make_project(method: MethodType) -> tuple[ProjectInput, "GridLayout"]:  # noqa: F821
    slope = SlopeParameters(slope_length=10.0, slope_width=8.0)
    frame = FrameSpec(
        target_vertical_pitch=2.0,
        target_horizontal_pitch=2.0,
        frame_width=0.3,
        frame_height=0.3,
    )
    project = ProjectInput(slope=slope, frame=frame, method=method)
    layout = compute_grid_layout(slope, frame)
    return project, layout


def test_cast_in_place_quantities_positive():
    project, layout = make_project(MethodType.CAST_IN_PLACE)
    calc = get_calculator(MethodType.CAST_IN_PLACE)
    result = calc.calculate(project, layout)

    values = {item.label: item.value for item in result.items}
    assert values["コンクリート体積"] > 0
    assert values["型枠面積"] > 0
    assert values["主筋質量"] > 0
    # concrete volume must be less than naive total_length * area (overlap subtracted)
    naive = layout.total_frame_length * project.frame.cross_section_area
    assert values["コンクリート体積"] < naive


def test_precast_quantities_positive():
    project, layout = make_project(MethodType.PRECAST)
    calc = get_calculator(MethodType.PRECAST)
    result = calc.calculate(project, layout)
    values = {item.label: item.value for item in result.items}
    assert values["プレキャスト部材数"] > 0
    assert values["部材質量"] > 0
    assert values["裏込め材体積"] > 0


def test_shotcrete_quantities_positive():
    project, layout = make_project(MethodType.SHOTCRETE)
    calc = get_calculator(MethodType.SHOTCRETE)
    result = calc.calculate(project, layout)
    values = {item.label: item.value for item in result.items}
    assert values["吹付面積"] > 0
    assert values["吹付材料数量(ロス込み)"] > values["吹付設計体積"]
