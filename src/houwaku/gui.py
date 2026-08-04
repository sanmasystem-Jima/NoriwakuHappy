"""法枠展開図/数量算出ツールの GUI (DearPyGui)。"""
from __future__ import annotations

import traceback

import dearpygui.dearpygui as dpg

from .dxf_export import export_dxf
from .dxf_import import import_geometry
from .geometry import GridLayout, compute_grid_layout
from .methods import get_calculator
from .models import (
    CastInPlaceOptions,
    FrameSpec,
    MethodType,
    PrecastOptions,
    ProjectInput,
    ShotcreteOptions,
    SlopeParameters,
)

METHOD_LABELS = {
    MethodType.CAST_IN_PLACE: "現場打ちコンクリート法枠工",
    MethodType.PRECAST: "プレキャスト法枠工",
    MethodType.SHOTCRETE: "吹付法枠工",
}
LABEL_TO_METHOD = {v: k for k, v in METHOD_LABELS.items()}

PREVIEW_DRAWLIST_TAG = "preview_drawlist"
QUANTITY_TABLE_TAG = "quantity_table"
STATUS_TEXT_TAG = "status_text"

# アプリの状態(直近の計算結果を保持し、DXF書き出し等から参照する)
_state: dict = {"layout": None, "project": None, "quantity": None}


def _read_float(tag: str) -> float:
    return float(dpg.get_value(tag))


def _read_int(tag: str) -> int:
    return int(dpg.get_value(tag))


def _build_project() -> ProjectInput:
    slope = SlopeParameters(
        slope_length=_read_float("in_slope_length"),
        slope_width=_read_float("in_slope_width"),
    )
    frame = FrameSpec(
        target_vertical_pitch=_read_float("in_vertical_pitch"),
        target_horizontal_pitch=_read_float("in_horizontal_pitch"),
        frame_width=_read_float("in_frame_width"),
        frame_height=_read_float("in_frame_height"),
    )
    method_label = dpg.get_value("in_method")
    method = LABEL_TO_METHOD[method_label]

    cast_in_place = CastInPlaceOptions(
        bar_count_per_frame=_read_int("in_bar_count"),
        bar_diameter=dpg.get_value("in_bar_diameter"),
        stirrup_pitch=_read_float("in_stirrup_pitch"),
        anchor_per_intersection=_read_float("in_anchor_per_intersection"),
    )
    precast = PrecastOptions(
        segment_length=_read_float("in_segment_length"),
        unit_weight_per_m=_read_float("in_unit_weight_per_m"),
        backfill_thickness=_read_float("in_backfill_thickness"),
    )
    shotcrete = ShotcreteOptions(
        thickness=_read_float("in_shotcrete_thickness"),
        loss_factor=_read_float("in_loss_factor"),
        mesh_overlap_factor=_read_float("in_mesh_overlap_factor"),
    )

    return ProjectInput(
        slope=slope,
        frame=frame,
        method=method,
        cast_in_place=cast_in_place,
        precast=precast,
        shotcrete=shotcrete,
    )


def _set_status(message: str, error: bool = False) -> None:
    color = (220, 60, 60) if error else (200, 200, 200)
    dpg.configure_item(STATUS_TEXT_TAG, color=color)
    dpg.set_value(STATUS_TEXT_TAG, message)


def _draw_preview(layout: GridLayout) -> None:
    dpg.delete_item(PREVIEW_DRAWLIST_TAG, children_only=True)

    canvas_w, canvas_h = 480, 480
    slope = layout.slope
    margin = 30
    scale = min(
        (canvas_w - 2 * margin) / slope.slope_width,
        (canvas_h - 2 * margin) / slope.slope_length,
    )

    def to_canvas(x: float, y: float) -> tuple[float, float]:
        # DXF座標(左下原点, y=法長方向)を画面座標(左上原点)に変換
        cx = margin + x * scale
        cy = canvas_h - margin - y * scale
        return cx, cy

    outline = [(0, 0), (slope.slope_width, 0), (slope.slope_width, slope.slope_length), (0, slope.slope_length), (0, 0)]
    dpg.draw_polyline(
        [to_canvas(x, y) for x, y in outline],
        color=(180, 180, 180, 255),
        thickness=1.5,
        parent=PREVIEW_DRAWLIST_TAG,
    )

    for start, end in layout.horizontal_lines():
        dpg.draw_line(to_canvas(*start), to_canvas(*end), color=(230, 90, 90, 255), thickness=1.5, parent=PREVIEW_DRAWLIST_TAG)

    for start, end in layout.vertical_lines():
        dpg.draw_line(to_canvas(*start), to_canvas(*end), color=(90, 140, 230, 255), thickness=1.5, parent=PREVIEW_DRAWLIST_TAG)


def _update_quantity_table(quantity) -> None:
    dpg.delete_item(QUANTITY_TABLE_TAG, children_only=True)
    dpg.add_table_column(label="項目", parent=QUANTITY_TABLE_TAG)
    dpg.add_table_column(label="数量", parent=QUANTITY_TABLE_TAG)
    dpg.add_table_column(label="単位", parent=QUANTITY_TABLE_TAG)
    dpg.add_table_column(label="算出根拠", parent=QUANTITY_TABLE_TAG)

    for item in quantity.items:
        with dpg.table_row(parent=QUANTITY_TABLE_TAG):
            dpg.add_text(item.label)
            dpg.add_text(f"{item.value}")
            dpg.add_text(item.unit)
            dpg.add_text(item.note)


def on_calculate(sender=None, app_data=None) -> None:
    try:
        project = _build_project()
        layout = compute_grid_layout(project.slope, project.frame)
        calculator = get_calculator(project.method)
        quantity = calculator.calculate(project, layout)

        _state["project"] = project
        _state["layout"] = layout
        _state["quantity"] = quantity

        _draw_preview(layout)
        _update_quantity_table(quantity)
        _set_status(
            f"計算完了: 縦枠{layout.vertical_member_count}本 / 横枠{layout.horizontal_member_count}本 / "
            f"総延長{layout.total_frame_length:.2f}m"
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _set_status(f"エラー: {exc}", error=True)


def on_method_change(sender=None, app_data=None) -> None:
    method_label = dpg.get_value("in_method")
    method = LABEL_TO_METHOD[method_label]
    for group, active in (
        ("group_cast_in_place", method == MethodType.CAST_IN_PLACE),
        ("group_precast", method == MethodType.PRECAST),
        ("group_shotcrete", method == MethodType.SHOTCRETE),
    ):
        dpg.configure_item(group, show=active)


def _do_export_dxf(path: str) -> None:
    if _state["layout"] is None:
        on_calculate()
    if _state["layout"] is None:
        return
    if not path.lower().endswith(".dxf"):
        path += ".dxf"
    export_dxf(path, _state["layout"], _state["quantity"])
    _set_status(f"DXFを書き出しました: {path}")


def on_export_dxf_dialog(sender, app_data) -> None:
    file_path = app_data.get("file_path_name") if app_data else None
    if not file_path:
        return
    try:
        _do_export_dxf(file_path)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _set_status(f"DXF書き出しエラー: {exc}", error=True)


def on_import_dxf_dialog(sender, app_data) -> None:
    file_path = app_data.get("file_path_name") if app_data else None
    if not file_path:
        return
    try:
        geom = import_geometry(file_path)
        dpg.set_value("in_slope_width", round(geom.width, 3))
        dpg.set_value("in_slope_length", round(geom.height, 3))
        msg = f"DXF読込: 展開幅≈{geom.width:.2f}m 法長≈{geom.height:.2f}m"
        if geom.estimated_horizontal_pitch:
            dpg.set_value("in_horizontal_pitch", round(geom.estimated_horizontal_pitch, 3))
            msg += f" 横ピッチ推定{geom.estimated_horizontal_pitch:.2f}m"
        if geom.estimated_vertical_pitch:
            dpg.set_value("in_vertical_pitch", round(geom.estimated_vertical_pitch, 3))
            msg += f" 縦ピッチ推定{geom.estimated_vertical_pitch:.2f}m"
        msg += "  ※推定値のため必ず確認してください"
        _set_status(msg)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _set_status(f"DXF読込エラー: {exc}", error=True)


JAPANESE_FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def _setup_japanese_font() -> None:
    with dpg.font_registry():
        with dpg.font(JAPANESE_FONT_PATH, 18) as font:
            dpg.add_font_range_hint(dpg.mvFontRangeHint_Japanese)
        dpg.bind_font(font)


def build_gui() -> None:
    dpg.create_context()
    _setup_japanese_font()

    with dpg.file_dialog(
        directory_selector=False,
        show=False,
        callback=on_export_dxf_dialog,
        tag="export_dxf_dialog",
        default_filename="houwaku_plan.dxf",
        width=600,
        height=400,
    ):
        dpg.add_file_extension(".dxf")
        dpg.add_file_extension(".*")

    with dpg.file_dialog(
        directory_selector=False,
        show=False,
        callback=on_import_dxf_dialog,
        tag="import_dxf_dialog",
        width=600,
        height=400,
    ):
        dpg.add_file_extension(".dxf")
        dpg.add_file_extension(".*")

    with dpg.window(tag="main_window"):
        with dpg.group(horizontal=True):
            # ---- 左側: 入力パネル ----
            with dpg.child_window(width=420, autosize_y=True):
                dpg.add_text("法面・展開図の寸法")
                dpg.add_input_float(label="法長 L (m)", tag="in_slope_length", default_value=10.0, min_value=0.01)
                dpg.add_input_float(label="展開幅 W (m)", tag="in_slope_width", default_value=8.0, min_value=0.01)

                dpg.add_separator()
                dpg.add_text("法枠の割付・断面")
                dpg.add_input_float(label="縦ピッチ目標 (m)", tag="in_vertical_pitch", default_value=2.0, min_value=0.01)
                dpg.add_input_float(label="横ピッチ目標 (m)", tag="in_horizontal_pitch", default_value=2.0, min_value=0.01)
                dpg.add_input_float(label="枠幅 (m)", tag="in_frame_width", default_value=0.3, min_value=0.01)
                dpg.add_input_float(label="枠高さ (m)", tag="in_frame_height", default_value=0.3, min_value=0.01)

                dpg.add_separator()
                dpg.add_text("工法")
                dpg.add_combo(
                    list(METHOD_LABELS.values()),
                    tag="in_method",
                    default_value=METHOD_LABELS[MethodType.CAST_IN_PLACE],
                    callback=on_method_change,
                )

                with dpg.group(tag="group_cast_in_place"):
                    dpg.add_text("現場打ちコンクリート法枠 固有パラメータ")
                    dpg.add_input_int(label="主筋本数/枠", tag="in_bar_count", default_value=4, min_value=1)
                    dpg.add_combo(
                        ["D10", "D13", "D16", "D19", "D22", "D25", "D29", "D32", "D35", "D38"],
                        label="主筋径",
                        tag="in_bar_diameter",
                        default_value="D13",
                    )
                    dpg.add_input_float(label="スターラップピッチ (m)", tag="in_stirrup_pitch", default_value=0.3, min_value=0.01)
                    dpg.add_input_float(label="アンカー本数/交点(0=無し)", tag="in_anchor_per_intersection", default_value=0.0, min_value=0.0)

                with dpg.group(tag="group_precast", show=False):
                    dpg.add_text("プレキャスト法枠 固有パラメータ")
                    dpg.add_input_float(label="部材長 (m)", tag="in_segment_length", default_value=2.0, min_value=0.1)
                    dpg.add_input_float(label="単位質量 (kg/m)", tag="in_unit_weight_per_m", default_value=150.0, min_value=0.1)
                    dpg.add_input_float(label="裏込め厚さ (m)", tag="in_backfill_thickness", default_value=0.1, min_value=0.0)

                with dpg.group(tag="group_shotcrete", show=False):
                    dpg.add_text("吹付法枠 固有パラメータ")
                    dpg.add_input_float(label="吹付厚 (m)", tag="in_shotcrete_thickness", default_value=0.2, min_value=0.01)
                    dpg.add_input_float(label="ロス係数", tag="in_loss_factor", default_value=1.15, min_value=1.0)
                    dpg.add_input_float(label="金網重ね代係数", tag="in_mesh_overlap_factor", default_value=1.05, min_value=1.0)

                dpg.add_separator()
                with dpg.group(horizontal=True):
                    dpg.add_button(label="計算 / プレビュー更新", callback=on_calculate)
                    dpg.add_button(label="DXF読込...", callback=lambda: dpg.show_item("import_dxf_dialog"))
                    dpg.add_button(label="DXF書き出し...", callback=lambda: dpg.show_item("export_dxf_dialog"))

                dpg.add_separator()
                dpg.add_text("", tag=STATUS_TEXT_TAG, wrap=400)

            # ---- 右側: プレビュー + 数量表 ----
            with dpg.child_window(autosize_y=True):
                dpg.add_text("展開図プレビュー(赤=横枠/等高線方向, 青=縦枠/斜面方向)")
                with dpg.drawlist(width=480, height=480, tag=PREVIEW_DRAWLIST_TAG):
                    pass
                dpg.add_separator()
                dpg.add_text("数量集計表")
                with dpg.table(
                    tag=QUANTITY_TABLE_TAG,
                    header_row=True,
                    borders_innerH=True,
                    borders_outerH=True,
                    borders_innerV=True,
                    borders_outerV=True,
                    resizable=True,
                ):
                    dpg.add_table_column(label="項目")
                    dpg.add_table_column(label="数量")
                    dpg.add_table_column(label="単位")
                    dpg.add_table_column(label="算出根拠")

    dpg.create_viewport(title="法枠展開図・数量算出ツール", width=980, height=760)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("main_window", True)

    on_calculate()

    dpg.start_dearpygui()
    dpg.destroy_context()


def main() -> None:
    build_gui()


if __name__ == "__main__":
    main()
