import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from houwaku.dxf_export import export_dxf
from houwaku.dxf_import import import_geometry
from houwaku.geometry import compute_grid_layout
from houwaku.methods import get_calculator
from houwaku.models import FrameSpec, MethodType, ProjectInput, SlopeParameters


def test_export_then_import_roundtrip(tmp_path):
    slope = SlopeParameters(slope_length=10.0, slope_width=8.0)
    frame = FrameSpec(
        target_vertical_pitch=2.0,
        target_horizontal_pitch=2.0,
        frame_width=0.3,
        frame_height=0.3,
    )
    layout = compute_grid_layout(slope, frame)
    project = ProjectInput(slope=slope, frame=frame, method=MethodType.CAST_IN_PLACE)
    calc = get_calculator(MethodType.CAST_IN_PLACE)
    quantity = calc.calculate(project, layout)

    out_path = tmp_path / "test.dxf"
    export_dxf(str(out_path), layout, quantity)
    assert out_path.exists()

    geom = import_geometry(str(out_path))
    assert abs(geom.width - slope.slope_width) < 0.5
    assert abs(geom.height - slope.slope_length) < 0.5
    assert geom.estimated_horizontal_pitch is not None
    assert abs(geom.estimated_horizontal_pitch - 2.0) < 0.2
    assert geom.estimated_vertical_pitch is not None
    assert abs(geom.estimated_vertical_pitch - 2.0) < 0.2
