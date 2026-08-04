"""割付を表示し、境界をクリックで指定・修正できるGUI(検証用プロトタイプ)。

- 割付図(DXF)を読み込み、背景として薄く表示
- 割付図に境界(枠の外側)・横梁・縦柱を手描きしてある場合は、
  「中心線を読み込む」タブでレイヤー名を指定して読み込める
- レイヤーが無い/使わない場合は、「境界を描く」モードでクリックして
  境界の頂点を指定することもできる
- 自動割付(パターンごとの自動計算)は方針転換により廃止。周囲枠だけは
  境界から常に自動計算し、横梁・縦柱は手描き中心線をそのまま使う。
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import dearpygui.dearpygui as dpg
from shapely.geometry import LineString, Point, Polygon

from .boundary import SlopeShape, Tier
from .centerline_import import beams_from_layer, posts_from_layer, tiers_from_layer
from .dxf_background import (
    BOUNDARY_LAYER_NAME,
    BackgroundGeometry,
    content_bounds,
    load_background,
    segments_excluding_frame,
)
from .drafting import (
    CollapsedMemberWarning,
    LargeSpacingWarning,
    SmallPanelWarning,
    find_collapsed_member_warnings,
    find_large_spacing_warnings,
    find_small_panel_warnings,
    member_key,
    perimeter_outer_inner,
    prepare_drawn_members,
)
from .perimeter import compute_perimeter
from .quantities import build_quantity_report
from .spec import NAKAZUME_KOSOKISOZAI, NAKAZUME_MORTAR, FrameRule
from .stage_export import export_stages

AUTO_CLOSE_DELAY_S = 2.0  # DXF書き出し成功後、自動でウィンドウを閉じるまでの秒数

CANVAS_TAG = "ig_canvas"
CANVAS_W, CANVAS_H = 1200, 850
MARGIN = 40

MESH_CANVAS_TAG = "ig_mesh_canvas"
MESH_CANVAS_WINDOW_TAG = "ig_mesh_canvas_window"
LAYOUT_CANVAS_WINDOW_TAG = "ig_layout_canvas_window"
# ラス網展開図・割付図のキャンバスは同時に1枚しか表示しないので、
# レイアウトの幅がずれないよう同じ大きさにしておく。
MESH_CANVAS_W, MESH_CANVAS_H = CANVAS_W, CANVAS_H

STATUS_TAG = "ig_status"
DRAW_SUBMODE_RADIO = "ig_draw_submode_radio"
TIER_LIST_TAG = "ig_tier_list"
BEAM_LAYER_COMBO = "ig_beam_layer_combo"
POST_LAYER_COMBO = "ig_post_layer_combo"
BOUNDARY_LAYER_COMBO = "ig_boundary_layer_combo"
SUGGESTION_LIST_TAG = "ig_suggestion_list"

# 警告(SmallPanelWarning)ごとに色を変え、キャンバス上の丸と一覧のテキストで
# どれがどれか対応が分かるようにする。
WARNING_COLORS = [
    (255, 60, 60, 255),    # 赤
    (60, 180, 255, 255),   # 水色
    (255, 210, 60, 255),   # 黄
    (190, 110, 255, 255),  # 紫
    (60, 230, 140, 255),   # 緑
    (255, 150, 60, 255),   # 橙
]


def _warning_color(index: int) -> tuple[int, int, int, int]:
    return WARNING_COLORS[index % len(WARNING_COLORS)]


def _warning_key(w) -> tuple:
    """警告の同一性を判定するためのキー(段・種類・位置)。

    警告は毎回find_*_warningsで新しく作り直されるオブジェクトなので、
    選択中/消去済みをまたいで同じ警告だと分かるように、値ベースの
    キーで比較する(座標は丸めて浮動小数点の誤差を吸収する)。
    """
    return (
        w.tier_index,
        w.label,
        (round(w.point_a[0], 3), round(w.point_a[1], 3)),
        (round(w.point_b[0], 3), round(w.point_b[1], 3)),
    )


DISMISSED_WARNINGS_SUFFIX = ".dismissed_warnings.json"


def _dismissed_warnings_path(layout_dxf_path: str) -> Path:
    """消去した警告を記憶するファイル(割付図DXFの隣に置くサイドカー)。"""
    p = Path(layout_dxf_path)
    return p.with_name(p.name + DISMISSED_WARNINGS_SUFFIX)


def _load_dismissed_warnings(layout_dxf_path: str) -> set:
    """前回までに消去した警告を、割付図DXFごとに引き継ぐ(学習機能)。"""
    path = _dismissed_warnings_path(layout_dxf_path)
    if not path.exists():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {tuple([item[0], item[1], tuple(item[2]), tuple(item[3])]) for item in raw}
    except Exception:  # noqa: BLE001
        return set()


def _save_dismissed_warnings(layout_dxf_path: str, dismissed: set) -> None:
    path = _dismissed_warnings_path(layout_dxf_path)
    try:
        path.write_text(json.dumps(list(dismissed), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        dpg.set_value(STATUS_TAG, f"警告の記憶を保存できませんでした: {exc}")


WARNING_TEXT_THEMES: list[int] = []
WARNING_SELECTED_THEME: int | None = None


def _build_warning_themes() -> None:
    """警告一覧の色(通常時の色分け、選択中の白)を、テーマとして事前に作る。"""
    global WARNING_SELECTED_THEME
    for color in WARNING_COLORS:
        with dpg.theme() as theme:
            with dpg.theme_component(dpg.mvSelectable):
                dpg.add_theme_color(dpg.mvThemeCol_Text, color)
        WARNING_TEXT_THEMES.append(theme)
    with dpg.theme() as selected_theme:
        with dpg.theme_component(dpg.mvSelectable):
            dpg.add_theme_color(dpg.mvThemeCol_Text, (255, 255, 255, 255))
    WARNING_SELECTED_THEME = selected_theme

PAGE_RADIO = "ig_page_radio"
TAB_CENTERLINE_IMPORT = "ig_tab_centerline_import"
TAB_DRAW = "ig_tab_draw"

SPEC_WINDOW = "ig_spec_window"
SPEC_WIDTH_MM = "ig_spec_width_mm"  # 正方形の一辺
SPEC_PITCH_MM = "ig_spec_pitch_mm"
SPEC_GRADIENT_N = "ig_spec_gradient_n"
SPEC_GRADIENT_UNKNOWN = "ig_spec_gradient_unknown"
SPEC_MIN_SEGMENT_MM = "ig_spec_min_segment_mm"
SPEC_SCALE_DENOM = "ig_spec_scale_denom"  # 縮尺の分母N(1:N)。DXF変換で失われるため毎回入力してもらう
SPEC_NAKAZUME_TYPE = "ig_spec_nakazume_type"  # 中詰め工の種類(厚層基材吹き付け工 or モルタル吹き付け工)
SPEC_NAKAZUME_THICKNESS_CM = "ig_spec_nakazume_thickness_cm"  # 中詰めの厚さ(cm、土木では慣例的にcm表記)

LATH_AREA_WINDOW = "ig_lath_area_window"
LATH_AREA_INPUT = "ig_lath_area_input"  # ラス面積(m2)。ラス網展開図に記載の求積結果を、読込直後にユーザーへ確認・入力してもらう
LATH_AREA_STATUS = "ig_lath_area_status"

EXPORT_FOLDER_WINDOW = "ig_export_folder_window"
EXPORT_FOLDER_INPUT = "ig_export_folder_input"  # デスクトップ出力フォルダ名。書き出すたびに新規作成する前提で、提案名をユーザーが承認・修正する
EXPORT_FOLDER_STATUS = "ig_export_folder_status"

EXPORT_DONE_WINDOW = "ig_export_done_window"
EXPORT_DONE_TEXT = "ig_export_done_text"

INTRO_WINDOW = "ig_intro_window"

MODE_DRAW = "境界を描く(頂点)"
MODE_EDGE = "境界を描く(辺をクリック)"


def _empty_shape() -> SlopeShape:
    shape = SlopeShape(tiers=[Tier(polygon=Polygon([(0, 0), (1, 0), (1, 1)]))])
    shape.tiers.clear()
    return shape


_state = {
    "shape": _empty_shape(),
    "rule": FrameRule(pitch=2.0),
    "world_bounds": (-2.0, -2.0, 10.0, 12.0),
    "mesh_world_bounds": (-2.0, -2.0, 10.0, 12.0),
    "mesh_background": None,  # BackgroundGeometry | None (ラス網展開図。数量計算のラス網面積などに使う)
    "mesh_dxf_path": None,  # 元のラス網展開図DXFのパス(書き出し時に忠実コピーするため保持)
    "close_at": None,  # DXF書き出し後の自動終了予定時刻(time.monotonic())
    "layout_background": None,  # BackgroundGeometry | None (割付図。境界・横梁・縦柱のレイヤー読込元)
    "layout_dxf_path": None,  # 割付図DXFのパス(消去した警告をファイルごとに記憶するため保持)
    "mode": MODE_DRAW,
    "draft_vertices": [],  # list[(x,y)] 境界を描いている途中の頂点
    "suppress_next_click": False,
    "beam_layer_name": None,  # 手描き横梁中心線のレイヤー名
    "post_layer_name": None,  # 手描き縦柱中心線のレイヤー名
    "boundary_layer_name": None,  # 手描き境界(枠の外側)のレイヤー名
    "scale_denominator": 100.0,  # 縮尺1:Nの分母N。mm指定(文字高さ・オフセット等)を実寸(m)に換算する際に使う
    "nakazume_type": NAKAZUME_MORTAR,  # 中詰め工の種類(水切り・水抜きパイプの配置判定、数量に使う)
    "nakazume_thickness_m": 0.1,  # 中詰めの厚さ(m)
    "selected_warning_key": None,  # クリックして選択中の修正提案(キャンバス上の丸を白くする)
    "dismissed_warnings": set(),  # 不要と判断してユーザーが消去した修正提案のキー
    "select_member_mode": False,  # 部材(横梁・縦柱)をクリックで選ぶモードか
    "selected_member_key": None,  # クリックして選択中の部材(member_key)。(種別, キー)のタプル
    "recovered_member_keys": set(),  # 「元の中心線のまま復帰する」を選んだ部材のキー(クリップをスキップする)
    "lath_area": None,  # float | None ラス面積(m2)。ラス網展開図読込直後にユーザーへ確認・入力してもらう
    "last_export_dir": None,  # Path | None 直近で書き出したフォルダ(出力後に開くか確認するために保持)
}


def _compute_world_bounds() -> tuple[float, float, float, float]:
    pad = 2.0
    minxs, minys, maxxs, maxys = [], [], [], []

    if _state["layout_background"] is not None:
        lx0, ly0, lx1, ly1 = content_bounds(_state["layout_background"])
        minxs.append(lx0); minys.append(ly0); maxxs.append(lx1); maxys.append(ly1)

    if _state["shape"].tiers:
        sx0, sy0, sx1, sy1 = _state["shape"].overall_bounds
        minxs.append(sx0); minys.append(sy0); maxxs.append(sx1); maxys.append(sy1)

    if not minxs:
        return (-2.0, -2.0, 10.0, 12.0)

    return (min(minxs) - pad, min(minys) - pad, max(maxxs) + pad, max(maxys) + pad)


def _compute_mesh_world_bounds() -> tuple[float, float, float, float]:
    pad = 2.0
    bg = _state["mesh_background"]
    if bg is None:
        return (-2.0, -2.0, 10.0, 12.0)
    x0, y0, x1, y1 = content_bounds(bg)
    return (x0 - pad, y0 - pad, x1 + pad, y1 + pad)


def _fit_scale(bounds: tuple[float, float, float, float], canvas_w: float, canvas_h: float) -> float:
    minx, miny, maxx, maxy = bounds
    w, h = maxx - minx, maxy - miny
    if w <= 0 or h <= 0:
        return 1.0
    return min((canvas_w - 2 * MARGIN) / w, (canvas_h - 2 * MARGIN) / h)


def _shared_scale() -> float:
    """ラス網展開図・割付図を同じ縮尺(ピクセル/メートル)で表示するための
    共通スケール。双方とも実寸(メートル)で正しく読み込めている前提なので、
    それぞれ独立してキャンバスにフィットさせると見た目の大きさがズレてしまう
    ため、両方がキャンバスに収まる方(小さい方)のスケールを採用する。
    """
    scales = []
    if _state["mesh_background"] is not None:
        scales.append(_fit_scale(_state["mesh_world_bounds"], MESH_CANVAS_W, MESH_CANVAS_H))
    if _state["layout_background"] is not None:
        scales.append(_fit_scale(_state["world_bounds"], CANVAS_W, CANVAS_H))
    if not scales:
        return 1.0
    return min(scales)


def _mesh_to_canvas(x: float, y: float) -> tuple[float, float]:
    minx, miny, maxx, maxy = _state["mesh_world_bounds"]
    scale = _shared_scale()
    ox = (MESH_CANVAS_W - (maxx - minx) * scale) / 2
    oy = (MESH_CANVAS_H - (maxy - miny) * scale) / 2
    cx = ox + (x - minx) * scale
    cy = MESH_CANVAS_H - oy - (y - miny) * scale
    return cx, cy


def _to_canvas(x: float, y: float) -> tuple[float, float]:
    minx, miny, maxx, maxy = _state["world_bounds"]
    scale = _shared_scale()
    ox = (CANVAS_W - (maxx - minx) * scale) / 2
    oy = (CANVAS_H - (maxy - miny) * scale) / 2
    cx = ox + (x - minx) * scale
    cy = CANVAS_H - oy - (y - miny) * scale
    return cx, cy


def _to_world(canvas_x: float, canvas_y: float) -> tuple[float, float]:
    minx, miny, maxx, maxy = _state["world_bounds"]
    scale = _shared_scale()
    ox = (CANVAS_W - (maxx - minx) * scale) / 2
    oy = (CANVAS_H - (maxy - miny) * scale) / 2
    x = minx + (canvas_x - ox) / scale
    y = miny + (CANVAS_H - oy - canvas_y) / scale
    return x, y


def _current_scale() -> float:
    return _shared_scale()


SNAP_PIXEL_TOLERANCE = 12.0


def _snap_to_background(wx: float, wy: float) -> tuple[float, float, bool]:
    """割付図の背景の線分の端点のうち最も近いものにスナップする。

    戻り値: (x, y, スナップしたかどうか)
    """
    bg: BackgroundGeometry | None = _state["layout_background"]
    if bg is None:
        return wx, wy, False

    scale = _current_scale()
    tolerance_world = SNAP_PIXEL_TOLERANCE / scale

    best_point = None
    best_dist = tolerance_world
    for a, b in bg.segments:
        for px, py in (a, b):
            d = ((px - wx) ** 2 + (py - wy) ** 2) ** 0.5
            if d < best_dist:
                best_dist = d
                best_point = (px, py)

    if best_point is not None:
        return best_point[0], best_point[1], True
    return wx, wy, False


def _point_to_segment_distance(p, a, b) -> float:
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-12:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def _nearest_background_segment(wx: float, wy: float):
    bg: BackgroundGeometry | None = _state["layout_background"]
    if bg is None:
        return None

    scale = _current_scale()
    tolerance_world = SNAP_PIXEL_TOLERANCE / scale

    best_seg = None
    best_dist = tolerance_world
    for a, b in bg.segments:
        d = _point_to_segment_distance((wx, wy), a, b)
        if d < best_dist:
            best_dist = d
            best_seg = (a, b)
    return best_seg


def _manual_members() -> tuple[list, list]:
    """割付図のレイヤーから読み込んだ横梁・縦柱をそのまま返す(自動修正はしない)。"""
    bg: BackgroundGeometry | None = _state["layout_background"]
    if bg is None:
        return [], []
    posts_raw = posts_from_layer(bg, _state["post_layer_name"]) if _state["post_layer_name"] else []
    beams_raw = beams_from_layer(bg, _state["beam_layer_name"]) if _state["beam_layer_name"] else []
    return beams_raw, posts_raw


def _redraw_mesh() -> None:
    """ラス網展開図は参照表示のみ(クリック操作等はしない)。"""
    if not dpg.does_item_exist(MESH_CANVAS_TAG):
        return
    dpg.delete_item(MESH_CANVAS_TAG, children_only=True)
    bg: BackgroundGeometry | None = _state["mesh_background"]
    if bg is None:
        dpg.draw_text(
            (MESH_CANVAS_W / 2 - 140, MESH_CANVAS_H / 2 - 10),
            "ラス網展開図(未読込)",
            size=18, color=(140, 140, 140, 255), parent=MESH_CANVAS_TAG,
        )
        return
    for a, b in segments_excluding_frame(bg):
        dpg.draw_line(_mesh_to_canvas(*a), _mesh_to_canvas(*b), color=(150, 150, 160, 255), thickness=0.8, parent=MESH_CANVAS_TAG)


def _redraw() -> None:
    dpg.delete_item(CANVAS_TAG, children_only=True)
    _redraw_mesh()
    shape: SlopeShape = _state["shape"]
    rule: FrameRule = _state["rule"]

    if _state["mesh_background"] is None:
        dpg.draw_text(
            (CANVAS_W / 2 - 300, CANVAS_H / 2 - 20),
            "まずラス網展開図(DXFファイル)を開いてください",
            size=28, color=(230, 200, 120, 255), parent=CANVAS_TAG,
        )
        return

    layout_bg: BackgroundGeometry | None = _state["layout_background"]
    if layout_bg is None:
        dpg.draw_text(
            (CANVAS_W / 2 - 380, CANVAS_H / 2 - 20),
            "それをもとに割付図(中心線だけで構いません)を作成し、入力してください",
            size=28, color=(230, 200, 120, 255), parent=CANVAS_TAG,
        )
        return

    for a, b in segments_excluding_frame(layout_bg):
        dpg.draw_line(_to_canvas(*a), _to_canvas(*b), color=(90, 96, 110, 255), thickness=0.8, parent=CANVAS_TAG)

    for tier in shape.tiers:
        coords = list(tier.polygon.exterior.coords)
        dpg.draw_polyline(
            [_to_canvas(x, y) for x, y in coords],
            color=(190, 190, 190, 255), thickness=1.3, parent=CANVAS_TAG,
        )
        perim = compute_perimeter(tier, rule)
        for seg in perim.segments:
            dpg.draw_line(_to_canvas(*seg.start), _to_canvas(*seg.end), color=(230, 60, 60, 255), thickness=1.6, parent=CANVAS_TAG)

        outer, inner = perimeter_outer_inner(tier.polygon, rule.frame_width)
        dpg.draw_polygon([_to_canvas(x, y) for x, y in inner.exterior.coords], color=(230, 60, 60, 160), thickness=1.0, parent=CANVAS_TAG)

    manual_beams, manual_posts = _manual_members()
    manual_beams = manual_beams or None
    manual_posts = manual_posts or None

    beams: list = []
    posts: list = []
    crossing_counts: list = []
    beam_edges: list = []
    post_edges: list = []
    if shape.tiers and (manual_beams or manual_posts):
        beams, posts, crossing_counts, beam_edges, post_edges = prepare_drawn_members(
            [t.polygon for t in shape.tiers], rule.frame_width, manual_beams or [], manual_posts or [],
            _state["recovered_member_keys"],
        )

    for post, edges in zip(posts, post_edges):
        p0 = _to_canvas(*post.start)
        p1 = _to_canvas(*post.end)
        dpg.draw_line(p0, p1, color=(90, 150, 230, 255), thickness=1.4, parent=CANVAS_TAG)
        dpg.draw_polyline([_to_canvas(*p) for p in edges.edge_a], color=(90, 150, 230, 160), thickness=1.0, parent=CANVAS_TAG)
        dpg.draw_polyline([_to_canvas(*p) for p in edges.edge_b], color=(90, 150, 230, 160), thickness=1.0, parent=CANVAS_TAG)

    for beam, count, edges in zip(beams, crossing_counts, beam_edges):
        pts = [_to_canvas(*p) for p in beam.points]
        dpg.draw_polyline(pts, color=(230, 200, 60, 255), thickness=1.4, parent=CANVAS_TAG)
        dpg.draw_text((pts[0][0] - 34, pts[0][1] - 7), f"{beam.gross_length:.2f} ({count})", size=11, color=(230, 200, 60, 255), parent=CANVAS_TAG)
        dpg.draw_polyline([_to_canvas(*p) for p in edges.edge_a], color=(230, 200, 60, 160), thickness=1.0, parent=CANVAS_TAG)
        dpg.draw_polyline([_to_canvas(*p) for p in edges.edge_b], color=(230, 200, 60, 160), thickness=1.0, parent=CANVAS_TAG)

    # 選択中の部材(復帰対象)は、元の中心線(クリップ前)を白い太線で強調する。
    # クリップで消失している部材は、上のループではほぼ点にしか見えないため、
    # 「消えている場所」自体を示すのにも使える。
    selected = _state["selected_member_key"]
    if selected is not None:
        kind, key = selected
        candidates = manual_beams if kind == "横梁" else manual_posts
        for m in candidates or []:
            pts = m.points if kind == "横梁" else [m.start, m.end]
            if member_key(pts[0], pts[-1]) == key:
                dpg.draw_polyline(
                    [_to_canvas(*p) for p in pts], color=(255, 255, 255, 255), thickness=2.5, parent=CANVAS_TAG,
                )
                break

    draft = _state["draft_vertices"]
    if draft:
        pts = [_to_canvas(x, y) for x, y in draft]
        if len(pts) > 1:
            dpg.draw_polyline(pts, color=(255, 160, 60, 255), thickness=2.0, parent=CANVAS_TAG)
        for p in pts:
            dpg.draw_circle(p, 4, color=(255, 160, 60, 255), fill=(255, 160, 60, 255), parent=CANVAS_TAG)

    warnings: list[SmallPanelWarning | LargeSpacingWarning | CollapsedMemberWarning] = []
    if shape.tiers and (manual_beams or manual_posts):
        tier_polys = [t.polygon for t in shape.tiers]
        warnings = find_small_panel_warnings(
            tier_polys, rule.frame_width, rule.pitch, manual_beams or [], manual_posts or []
        ) + find_large_spacing_warnings(
            tier_polys, rule.frame_width, rule.pitch, manual_beams or [], manual_posts or []
        ) + find_collapsed_member_warnings(
            tier_polys, rule.frame_width, manual_beams or [], manual_posts or [],
            _state["recovered_member_keys"],
        )
        warnings = [w for w in warnings if _warning_key(w) not in _state["dismissed_warnings"]]
    WARNING_MARK_RADIUS = 14.0
    # 選択中の警告の丸は、他の警告と同じ場所(角など)に重なることがあるため、
    # 最後に(一番上に)描いて、他の丸に隠れて見えなくならないようにする。
    selected_pts: list[tuple[float, float]] = []
    for i, w in enumerate(warnings):
        is_selected = _warning_key(w) == _state["selected_warning_key"]
        if is_selected:
            selected_pts.extend((w.point_a, w.point_b))
            continue
        color = _warning_color(i)
        for pt in (w.point_a, w.point_b):
            dpg.draw_circle(
                _to_canvas(*pt), WARNING_MARK_RADIUS,
                color=color, thickness=2.5, parent=CANVAS_TAG,
            )
    for pt in selected_pts:
        dpg.draw_circle(
            _to_canvas(*pt), WARNING_MARK_RADIUS + 2.0,
            color=(255, 255, 255, 255), thickness=3.0, parent=CANVAS_TAG,
        )

    _refresh_tier_list()
    _update_suggestions(warnings)


def _on_suggestion_select(sender, app_data, user_data) -> None:
    key = user_data
    _state["selected_warning_key"] = None if _state["selected_warning_key"] == key else key
    _redraw()


def _on_dismiss_selected_warning(sender, app_data) -> None:
    key = _state["selected_warning_key"]
    if key is None:
        dpg.set_value(STATUS_TAG, "消去する警告を、一覧から選択(クリック)してください。")
        return
    _state["dismissed_warnings"].add(key)
    _state["selected_warning_key"] = None
    if _state["layout_dxf_path"]:
        _save_dismissed_warnings(_state["layout_dxf_path"], _state["dismissed_warnings"])
    _redraw()


def _on_restore_dismissed_warnings(sender, app_data) -> None:
    _state["dismissed_warnings"] = set()
    if _state["layout_dxf_path"]:
        _save_dismissed_warnings(_state["layout_dxf_path"], _state["dismissed_warnings"])
    _redraw()


def _on_toggle_select_member_mode(sender, app_data) -> None:
    _state["select_member_mode"] = app_data
    if app_data:
        dpg.set_value(STATUS_TAG, "部材選択モード: 枠が付いていない・おかしい横梁/縦柱の中心線をクリックしてください。")
    else:
        _state["selected_member_key"] = None
        _redraw()


def _on_recover_selected_member(sender, app_data) -> None:
    selected = _state["selected_member_key"]
    if selected is None:
        dpg.set_value(STATUS_TAG, "復帰する部材を、部材選択モードでクリックして選んでください。")
        return
    _kind, key = selected
    _state["recovered_member_keys"].add(key)
    _state["selected_member_key"] = None
    dpg.set_value(STATUS_TAG, "選択した部材を、元の中心線のまま復帰しました(境界へのクリップをスキップします)。")
    _redraw()


def _update_suggestions(warnings: list[SmallPanelWarning | LargeSpacingWarning | CollapsedMemberWarning]) -> None:
    """段の隅に小さい半端枠がないか調べた結果を表示する(自動修正はしない、
    割付図を手動で直すよう促すだけ)。警告位置は_redraw()でキャンバスに
    丸として描画済み。一覧の文字をクリックすると、対応する丸が白くなり、
    どれがどれか分かりやすくなる(もう一度クリックで選択解除)。不要な
    警告は「選択した警告を消去」で一覧・丸ごと消せる。"""
    if not dpg.does_item_exist(SUGGESTION_LIST_TAG):
        return
    dpg.delete_item(SUGGESTION_LIST_TAG, children_only=True)

    if not warnings:
        dpg.add_text("(警告はありません)", parent=SUGGESTION_LIST_TAG, color=(150, 150, 150, 255))
        return

    for i, w in enumerate(warnings):
        key = _warning_key(w)
        item = dpg.add_selectable(
            label=w.message(), parent=SUGGESTION_LIST_TAG,
            default_value=(key == _state["selected_warning_key"]),
            callback=_on_suggestion_select, user_data=key,
        )
        theme = WARNING_SELECTED_THEME if key == _state["selected_warning_key"] else WARNING_TEXT_THEMES[i % len(WARNING_TEXT_THEMES)]
        dpg.bind_item_theme(item, theme)


def _refresh_tier_list() -> None:
    dpg.delete_item(TIER_LIST_TAG, children_only=True)
    for i, tier in enumerate(_state["shape"].tiers):
        n = len(tier.polygon.exterior.coords) - 1
        dpg.add_text(f"段{i + 1}: 頂点{n}個", parent=TIER_LIST_TAG)


def _nearest_manual_member(wx: float, wy: float, tolerance_world: float) -> tuple[str, tuple] | None:
    """クリック位置に一番近い横梁・縦柱を(種別, member_key)で返す。近くに無ければNone。"""
    manual_beams, manual_posts = _manual_members()
    best: tuple[str, tuple] | None = None
    best_dist = tolerance_world
    for b in manual_beams:
        d = LineString(b.points).distance(Point(wx, wy))
        if d < best_dist:
            best_dist = d
            best = ("横梁", member_key(b.points[0], b.points[-1]))
    for p in manual_posts:
        d = LineString([p.start, p.end]).distance(Point(wx, wy))
        if d < best_dist:
            best_dist = d
            best = ("縦柱", member_key(p.start, p.end))
    return best


def _on_canvas_click(sender, app_data) -> None:
    if _state["suppress_next_click"]:
        _state["suppress_next_click"] = False
        return
    if not dpg.is_item_hovered(CANVAS_TAG):
        return
    mx, my = dpg.get_drawing_mouse_pos()
    raw_wx, raw_wy = _to_world(mx, my)

    if _state["select_member_mode"]:
        tolerance_world = SNAP_PIXEL_TOLERANCE / _current_scale()
        found = _nearest_manual_member(raw_wx, raw_wy, tolerance_world)
        if found is None:
            dpg.set_value(STATUS_TAG, "近くに部材(横梁・縦柱)が見つかりません。中心線に近づけてクリックしてください。")
        else:
            _state["selected_member_key"] = found
            kind, _key = found
            dpg.set_value(STATUS_TAG, f"{kind}を選択しました。「選択した部材を元の中心線のまま復帰する」で復帰できます。")
        _redraw()
        return

    if _state["mode"] == MODE_EDGE:
        seg = _nearest_background_segment(raw_wx, raw_wy)
        if seg is None:
            dpg.set_value(STATUS_TAG, "近くに背景の辺が見つかりません。境界線に近づけてクリックしてください。")
            _redraw()
            return
        a, b = seg
        draft = _state["draft_vertices"]
        if draft:
            last = draft[-1]
            da = ((last[0] - a[0]) ** 2 + (last[1] - a[1]) ** 2) ** 0.5
            db = ((last[0] - b[0]) ** 2 + (last[1] - b[1]) ** 2) ** 0.5
            ordered = [a, b] if da <= db else [b, a]
            if ((last[0] - ordered[0][0]) ** 2 + (last[1] - ordered[0][1]) ** 2) ** 0.5 < 1e-6:
                new_pts = [ordered[1]]
            else:
                new_pts = ordered
        else:
            new_pts = [a, b]
        draft.extend(new_pts)
        dpg.set_value(STATUS_TAG, f"辺をクリックして頂点を追加(計{len(draft)}個)")
        _redraw()
        return

    wx, wy, snapped = _snap_to_background(raw_wx, raw_wy)
    snap_note = "(背景の頂点にスナップ)" if snapped else "(スナップ先なし、クリック位置そのまま)"
    _state["draft_vertices"].append((wx, wy))
    dpg.set_value(STATUS_TAG, f"境界の頂点を追加 ({len(_state['draft_vertices'])}個目): ({wx:.2f},{wy:.2f}) {snap_note}")
    _redraw()


def _on_mode_change(sender, app_data) -> None:
    _state["mode"] = app_data
    dpg.set_value(STATUS_TAG, f"モード切替: {app_data}")


PAGE_TAGS = [TAB_CENTERLINE_IMPORT, TAB_DRAW]
PAGE_LABEL_TO_TAG = {
    "① 中心線を読み込む": TAB_CENTERLINE_IMPORT,
    "② 境界入力": TAB_DRAW,
}


def _switch_page(tag: str) -> None:
    """縦型の項目リストで選ばれたページだけを表示し、対応するクリックモードに切り替える。"""
    for t in PAGE_TAGS:
        dpg.configure_item(t, show=(t == tag))

    if tag == TAB_DRAW:
        mode = dpg.get_value(DRAW_SUBMODE_RADIO) if dpg.does_item_exist(DRAW_SUBMODE_RADIO) else MODE_DRAW
        _state["mode"] = mode
        dpg.set_value(STATUS_TAG, f"モード切替: {mode}")


def _on_page_radio_change(sender, app_data) -> None:
    _switch_page(PAGE_LABEL_TO_TAG[app_data])


def _undo_vertex() -> None:
    if _state["draft_vertices"]:
        _state["draft_vertices"].pop()
        _redraw()


def _finish_tier() -> None:
    if len(_state["draft_vertices"]) < 3:
        dpg.set_value(STATUS_TAG, "頂点が3個未満です。境界を閉じるには最低3点必要です。")
        return
    try:
        tier = Tier(polygon=Polygon(_state["draft_vertices"]))
    except Exception as exc:  # noqa: BLE001
        dpg.set_value(STATUS_TAG, f"境界の作成に失敗: {exc}")
        return
    _state["shape"].tiers.append(tier)
    _state["draft_vertices"] = []
    _state["world_bounds"] = _compute_world_bounds()
    dpg.set_value(STATUS_TAG, f"段{len(_state['shape'].tiers)}を確定しました。続けて他の段を入力するか、全て終わったら「境界入力を完了」を押してください。")
    _redraw()


def _finish_all_boundary_input() -> None:
    if not _state["shape"].tiers:
        dpg.set_value(STATUS_TAG, "境界がまだ1段も確定していません。")
        return
    _state["world_bounds"] = _compute_world_bounds()
    dpg.set_value(STATUS_TAG, "境界入力を完了しました。")
    _redraw()


def _clear_shape() -> None:
    _state["shape"].tiers.clear()
    _state["draft_vertices"] = []
    _state["world_bounds"] = _compute_world_bounds()
    dpg.set_value(STATUS_TAG, "境界をすべてクリアしました。")
    _redraw()


NO_LAYER_OPTION = "(使わない)"


def _refresh_layer_combos() -> None:
    bg = _state["layout_background"]
    layer_names = bg.layer_names if bg is not None else []
    items = [NO_LAYER_OPTION] + layer_names
    for tag, layer_key in (
        (BEAM_LAYER_COMBO, "beam_layer_name"),
        (POST_LAYER_COMBO, "post_layer_name"),
        (BOUNDARY_LAYER_COMBO, "boundary_layer_name"),
    ):
        if not dpg.does_item_exist(tag):
            continue
        dpg.configure_item(tag, items=items)
        current = _state[layer_key]
        if current not in layer_names:
            current = None
            _state[layer_key] = None
        dpg.set_value(tag, current if current is not None else NO_LAYER_OPTION)


def _on_beam_layer_change(sender, app_data) -> None:
    _state["beam_layer_name"] = None if app_data == NO_LAYER_OPTION else app_data
    _redraw()


def _on_post_layer_change(sender, app_data) -> None:
    _state["post_layer_name"] = None if app_data == NO_LAYER_OPTION else app_data
    _redraw()


def _apply_boundary_layer(layer_name: str) -> bool:
    """指定レイヤーから境界を読み込み、shapeに反映する。成功したらTrue。"""
    bg = _state["layout_background"]
    if bg is None:
        return False

    result = tiers_from_layer(bg, layer_name)
    if not result.tiers:
        dpg.set_value(STATUS_TAG, f"レイヤー「{layer_name}」から閉じた境界が見つかりませんでした。")
        return False

    _state["shape"].tiers.clear()
    _state["shape"].tiers.extend(result.tiers)
    _state["draft_vertices"] = []
    _state["world_bounds"] = _compute_world_bounds()
    note = f"(注記など{result.stray_segment_count}本は無視)" if result.stray_segment_count else ""
    dpg.set_value(STATUS_TAG, f"境界を{len(result.tiers)}段、レイヤー「{layer_name}」から読み込みました{note}。")
    return True


def _on_boundary_layer_change(sender, app_data) -> None:
    _state["boundary_layer_name"] = None if app_data == NO_LAYER_OPTION else app_data
    if _state["boundary_layer_name"] is not None:
        _apply_boundary_layer(_state["boundary_layer_name"])
    _redraw()


# テンプレート(割付テンプレート.dxf)の標準レイヤー名。読み込んだ割付図が
# このレイヤー名をそのまま使っていれば、選び直す手間なく自動で採用する。
TEMPLATE_BOUNDARY_LAYER = BOUNDARY_LAYER_NAME
TEMPLATE_BEAM_LAYER = "横"
TEMPLATE_POST_LAYER = "縦"


def _auto_detect_template_layers(bg: BackgroundGeometry) -> bool:
    """テンプレ通りのレイヤー名(外周・横・縦)があれば自動で採用する。1つでも見つかればTrue。"""
    found_any = False
    if TEMPLATE_BOUNDARY_LAYER in bg.layers:
        _state["boundary_layer_name"] = TEMPLATE_BOUNDARY_LAYER
        _apply_boundary_layer(TEMPLATE_BOUNDARY_LAYER)
        found_any = True
    if TEMPLATE_BEAM_LAYER in bg.layers:
        _state["beam_layer_name"] = TEMPLATE_BEAM_LAYER
        found_any = True
    if TEMPLATE_POST_LAYER in bg.layers:
        _state["post_layer_name"] = TEMPLATE_POST_LAYER
        found_any = True
    return found_any


def _on_load_mesh_dxf(sender, app_data) -> None:
    file_path = app_data.get("file_path_name") if app_data else None
    if not file_path:
        return
    try:
        bg = load_background(file_path)
        _state["mesh_background"] = bg
        _state["mesh_dxf_path"] = file_path
        _state["mesh_world_bounds"] = _compute_mesh_world_bounds()
        _redraw()
        dpg.set_value(LATH_AREA_INPUT, 0.0)
        dpg.set_value(LATH_AREA_STATUS, "")
        dpg.show_item(LATH_AREA_WINDOW)
    except Exception as exc:  # noqa: BLE001
        dpg.set_value(STATUS_TAG, f"ラス網展開図の読込エラー: {exc}")


def _on_lath_area_confirmed() -> None:
    area = dpg.get_value(LATH_AREA_INPUT)
    if area <= 0:
        dpg.set_value(LATH_AREA_STATUS, "ラス面積(0より大きい数値)を入力してください。")
        return
    _state["lath_area"] = area
    dpg.hide_item(LATH_AREA_WINDOW)
    bg = _state["mesh_background"]
    dpg.set_value(
        STATUS_TAG,
        f"ラス網展開図読込: {_state['mesh_dxf_path']} (線分{len(bg.segments)}本)。"
        f"ラス面積 {area:.3f}m2 を記録しました。次に割付図(DXF)を選択してください。",
    )
    dpg.configure_item("layout_step_group", show=True)


BOUNDARY_COMPARE_TOLERANCE_RATIO = 0.03  # 外周の辺長どうしを比較する際の許容誤差(相対)


def _boundary_edge_lengths(bg: BackgroundGeometry) -> list[float] | None:
    """境界レイヤー("外周")の辺長リストを返す。レイヤーが無ければNoneを返す。"""
    chains = bg.layers.get(BOUNDARY_LAYER_NAME)
    if not chains:
        return None
    chain = max(chains, key=len)  # 複数チェーンがあれば点数最大のものを外周とみなす
    lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(chain, chain[1:])]
    return [d for d in lengths if d > 1e-6]


def _compare_boundaries(mesh_bg: BackgroundGeometry, layout_bg: BackgroundGeometry) -> tuple[bool, str]:
    """ラス網展開図・割付図、双方の"外周"レイヤーの辺を全数突き合わせて、
    同じ現場を同じ縮尺で描けているか確認する。

    2点だけをクリックして距離を比べるより、外周を構成する辺すべてを
    比較するほうがずっと確実(一致すれば偶然の一致である可能性がほぼ無い)。
    どちらかに"外周"レイヤーが無ければ比較できないので、警告なしで素通りする。
    """
    mesh_lengths = _boundary_edge_lengths(mesh_bg)
    layout_lengths = _boundary_edge_lengths(layout_bg)
    if mesh_lengths is None or layout_lengths is None:
        return True, ""
    if len(mesh_lengths) != len(layout_lengths):
        return False, (
            f"外周の辺の数が一致しません(ラス網展開図{len(mesh_lengths)}辺 / "
            f"割付図{len(layout_lengths)}辺)。同じ現場の外周のはずです。"
        )
    ms, ls = sorted(mesh_lengths), sorted(layout_lengths)
    worst_ratio, worst_i = 0.0, 0
    for i, (m, l) in enumerate(zip(ms, ls)):
        base = max(m, l)
        if base < 1e-9:
            continue
        ratio = abs(m - l) / base
        if ratio > worst_ratio:
            worst_ratio, worst_i = ratio, i
    if worst_ratio > BOUNDARY_COMPARE_TOLERANCE_RATIO:
        return False, (
            f"外周の辺長がラス網展開図と割付図で一致しません(最大差 {worst_ratio * 100:.1f}%、"
            f"ラス{ms[worst_i]:.3f}m / 割付{ls[worst_i]:.3f}m)。"
            "同じ現場・同じ縮尺で描かれているはずなので、"
            "どちらかの単位(mm/m)読み取りが誤っている可能性があります。"
        )
    return True, f"外周の辺長(全{len(ms)}辺)を比較し、同縮尺であることを確認しました。"


def _finish_layout_load(bg: BackgroundGeometry, file_path: str, note: str) -> None:
    _state["layout_background"] = bg
    _state["layout_dxf_path"] = file_path
    # 消去した警告は割付図DXFごとに記憶している(学習機能) — 同じファイルを
    # 開き直したときは前回の判断を引き継ぎ、別のファイルなら空から始める。
    _state["dismissed_warnings"] = _load_dismissed_warnings(file_path)
    _state["selected_warning_key"] = None
    # 別のファイルに差し替えたとき、同名レイヤーが別の意味で残っているかもしれないので、
    # 選択は必ずクリアして選び直してもらう(黙って前のファイルのレイヤー名を使い回さない)。
    _state["beam_layer_name"] = None
    _state["post_layer_name"] = None
    _state["boundary_layer_name"] = None

    auto_detected = _auto_detect_template_layers(bg)
    _state["world_bounds"] = _compute_world_bounds()
    template_note = "テンプレ通りのレイヤー(外周・横・縦)を自動で採用しました。" if auto_detected else ""
    dpg.set_value(
        STATUS_TAG,
        f"割付図読込: {file_path} (線分{len(bg.segments)}本)。{note}{template_note}次に法枠の規格を確認してください。",
    )
    dpg.configure_item(MESH_CANVAS_WINDOW_TAG, show=False)
    dpg.configure_item(LAYOUT_CANVAS_WINDOW_TAG, show=True)
    _redraw()
    dpg.show_item(SPEC_WINDOW)


def _on_load_layout_dxf(sender, app_data) -> None:
    """割付図DXFを読み込む。

    ラス網展開図が読込済みなら、双方の"外周"レイヤーの辺長を全数突き合わせて
    (_compare_boundaries)、同じ現場・同じ縮尺で描けていることを確認してから
    読み込む。手でクリックして2点を指定する方式は、位置の選び間違いに弱く、
    比較できる情報も2点分しかなかったため廃止した。外周の辺すべてを比較する
    ほうがずっと確実で、ユーザーの操作も不要になる。
    """
    file_path = app_data.get("file_path_name") if app_data else None
    if not file_path:
        return
    try:
        bg = load_background(file_path)
        mesh_bg = _state["mesh_background"]
        note = ""
        if mesh_bg is not None:
            ok, message = _compare_boundaries(mesh_bg, bg)
            if not ok:
                dpg.set_value(STATUS_TAG, f"割付図読込を中止しました。{message}")
                return
            note = f"{message} " if message else ""
        _finish_layout_load(bg, file_path, note)
    except Exception as exc:  # noqa: BLE001
        dpg.set_value(STATUS_TAG, f"割付図の読込エラー: {exc}")


def _on_spec_confirmed() -> None:
    size_m = dpg.get_value(SPEC_WIDTH_MM) / 1000.0  # 正方形なので幅=高さ
    pitch_m = dpg.get_value(SPEC_PITCH_MM) / 1000.0  # 上限値
    gradient_unknown = dpg.get_value(SPEC_GRADIENT_UNKNOWN)
    gradient_n = None if gradient_unknown else dpg.get_value(SPEC_GRADIENT_N)
    min_segment_m = dpg.get_value(SPEC_MIN_SEGMENT_MM) / 1000.0
    _state["scale_denominator"] = dpg.get_value(SPEC_SCALE_DENOM)
    _state["nakazume_type"] = dpg.get_value(SPEC_NAKAZUME_TYPE)
    _state["nakazume_thickness_m"] = dpg.get_value(SPEC_NAKAZUME_THICKNESS_CM) / 100.0

    _state["rule"] = FrameRule(
        frame_width=size_m,
        frame_height=size_m,
        pitch=pitch_m,
        min_segment=min_segment_m,
        search_step=size_m,
        gradient_n=gradient_n,
    )
    dpg.hide_item(SPEC_WINDOW)
    dpg.configure_item("main_controls", show=True)
    dpg.set_value(PAGE_RADIO, "① 中心線を読み込む")
    _switch_page(TAB_CENTERLINE_IMPORT)
    _refresh_layer_combos()
    dpg.set_value(
        STATUS_TAG,
        f"法枠規格: {size_m*1000:.0f}mm角, ピッチ上限{pitch_m*1000:.0f}mm。"
        "境界・横梁・縦柱のレイヤーを指定してください。",
    )
    _redraw()


def _on_folder_selected(sender, app_data) -> None:
    folder = app_data.get("file_path_name") if app_data else None
    if not folder:
        return
    dpg.configure_item("open_layout_dxf_dialog", default_path=folder)
    dpg.set_value(STATUS_TAG, f"フォルダを指定しました: {folder}")
    dpg.show_item("open_layout_dxf_dialog")


DESKTOP_OUTPUT_DIR_NAME = "法枠Happy出力"


def _desktop_output_base() -> Path:
    return Path.home() / "Desktop" / DESKTOP_OUTPUT_DIR_NAME


def _suggest_output_folder_name() -> str:
    """書き出すたびに新規フォルダを作る前提の提案名。

    既に同名フォルダがあれば(同じ割付図で複数回書き出した等)、末尾に
    連番を振って重複を避ける — ユーザーはこの提案をそのまま承認しても
    良いし、自分で書き換えても良い。
    """
    layout_path = _state.get("layout_dxf_path")
    base_name = Path(layout_path).stem if layout_path else "出力"
    base_dir = _desktop_output_base()
    name = base_name
    i = 2
    while (base_dir / name).exists():
        name = f"{base_name}_{i}"
        i += 1
    return name


def _open_export_dialog() -> None:
    dpg.set_value(EXPORT_FOLDER_INPUT, _suggest_output_folder_name())
    dpg.set_value(EXPORT_FOLDER_STATUS, "")
    dpg.show_item(EXPORT_FOLDER_WINDOW)


def _on_export_folder_confirmed() -> None:
    folder_name = dpg.get_value(EXPORT_FOLDER_INPUT).strip()
    if not folder_name:
        dpg.set_value(EXPORT_FOLDER_STATUS, "フォルダ名を入力してください。")
        return
    output_dir = _desktop_output_base() / folder_name
    output_dir.mkdir(parents=True, exist_ok=True)
    dpg.hide_item(EXPORT_FOLDER_WINDOW)
    layout_path = _state.get("layout_dxf_path")
    default_filename = Path(layout_path).stem if layout_path else "割付図"
    dpg.configure_item(
        "export_dxf_dialog", default_path=str(output_dir), default_filename=default_filename,
    )
    dpg.show_item("export_dxf_dialog")


def _on_export_dxf(sender, app_data) -> None:
    file_path = app_data.get("file_path_name") if app_data else None
    if not file_path:
        return
    if not file_path.lower().endswith(".dxf"):
        file_path += ".dxf"
    if not _state["shape"].tiers:
        dpg.set_value(STATUS_TAG, "境界が1段も確定していません。先に境界を入力してください。")
        return
    manual_beams, manual_posts = _manual_members()
    manual_beams = manual_beams or None
    manual_posts = manual_posts or None
    try:
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        export_stages(
            file_path,
            _state["shape"],
            _state["rule"],
            manual_beams=manual_beams,
            manual_posts=manual_posts,
            scale_denominator=_state["scale_denominator"],
            nakazume_type=_state["nakazume_type"],
            mesh_background=_state["mesh_background"],
            mesh_dxf_path=_state["mesh_dxf_path"],
            skip_trim_keys=_state["recovered_member_keys"],
        )

        tier_polys = [t.polygon for t in _state["shape"].tiers]
        beams, posts, _crossing_counts, _beam_edges, _post_edges = prepare_drawn_members(
            tier_polys, _state["rule"].frame_width, manual_beams or [], manual_posts or [],
            _state["recovered_member_keys"],
        )
        report = build_quantity_report(
            _state["shape"], _state["rule"], beams, posts,
            nakazume_type=_state["nakazume_type"], lath_area=_state["lath_area"],
        )
        report_path = str(Path(file_path).with_suffix(".txt"))
        Path(report_path).write_text(report.text, encoding="utf-8")

        dpg.set_value(
            STATUS_TAG,
            f"DXFと計算書を書き出しました: {file_path} / {report_path}",
        )
        _state["last_export_dir"] = Path(file_path).parent
        dpg.set_value(EXPORT_DONE_TEXT, f"出力フォルダを開きますか?\n{_state['last_export_dir']}")
        dpg.show_item(EXPORT_DONE_WINDOW)
    except Exception as exc:  # noqa: BLE001
        dpg.set_value(STATUS_TAG, f"DXF書き出しエラー: {exc}")


def _open_folder(folder: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception:  # noqa: BLE001
        pass


def _on_export_done_open_folder() -> None:
    folder = _state.get("last_export_dir")
    if folder is not None:
        _open_folder(folder)
    dpg.hide_item(EXPORT_DONE_WINDOW)
    _state["close_at"] = time.monotonic() + AUTO_CLOSE_DELAY_S


def _on_export_done_skip() -> None:
    dpg.hide_item(EXPORT_DONE_WINDOW)
    _state["close_at"] = time.monotonic() + AUTO_CLOSE_DELAY_S


def _on_intro_confirmed() -> None:
    dpg.hide_item(INTRO_WINDOW)
    dpg.show_item("open_mesh_dxf_dialog")


def _get_viewport_size(
    default: tuple[int, int] = (1600, 1000),
    margin: int = 80,
    minimum: tuple[int, int] = (800, 600),
) -> tuple[int, int]:
    """実行PCの画面解像度より少しだけ小さいウィンドウサイズを返す。

    画面解像度が取得できない環境では既定サイズにフォールバックする。
    """
    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        root.destroy()
    except Exception:  # noqa: BLE001
        return default

    width = max(minimum[0], screen_w - margin)
    height = max(minimum[1], screen_h - margin)
    return width, height


def build_gui() -> None:
    dpg.create_context()
    _build_warning_themes()

    with dpg.font_registry():
        with dpg.font("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 18) as font:
            pass
        dpg.bind_font(font)
        with dpg.font("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 28) as large_font:
            pass

    with dpg.file_dialog(
        directory_selector=False, show=False, callback=_on_load_mesh_dxf, tag="open_mesh_dxf_dialog",
        width=700, height=450, label="ラス網展開図(DXFファイル)を選択してください",
        default_path=str(Path(__file__).resolve().parents[2]),
    ):
        dpg.add_file_extension(".dxf", color=(140, 200, 255, 255))
        dpg.add_file_extension(".*")

    with dpg.file_dialog(
        directory_selector=False, show=False, callback=_on_load_layout_dxf, tag="open_layout_dxf_dialog",
        width=700, height=450, label="割付図(DXFファイル)を選択してください",
        default_path=str(Path(__file__).resolve().parents[2]),
    ):
        dpg.add_file_extension(".dxf", color=(140, 200, 255, 255))
        dpg.add_file_extension(".*")

    with dpg.file_dialog(
        directory_selector=True, show=False, callback=_on_folder_selected, tag="choose_folder_dialog",
        width=700, height=450, label="DXFファイルがあるフォルダを指定してください",
    ):
        pass

    with dpg.file_dialog(
        directory_selector=False, show=False, callback=_on_export_dxf, tag="export_dxf_dialog",
        width=700, height=450, label="書き出し先のDXFファイル名を指定してください",
        default_path=str(Path(__file__).resolve().parents[2]), default_filename="割付図",
    ):
        dpg.add_file_extension(".dxf", color=(140, 200, 255, 255))
        dpg.add_file_extension(".*")

    with dpg.window(
        tag=INTRO_WINDOW, label="用意するものリスト", show=False, modal=True,
        width=520, height=300, no_close=True,
    ):
        intro_title = dpg.add_text("用意するものリスト")
        dpg.bind_item_font(intro_title, large_font)
        dpg.add_separator()
        intro_body = dpg.add_text(
            "1. ラス展開図・面積\n"
            "2. 割付図(中心線のみ)\n"
            "3. 法枠の規格(サイズ・ピッチ)と中詰め仕様(種類・厚さ)"
        )
        dpg.bind_item_font(intro_body, large_font)
        dpg.add_separator()
        dpg.add_button(label="確認しました。始める", callback=_on_intro_confirmed)

    with dpg.window(
        tag=EXPORT_FOLDER_WINDOW, label="保存フォルダ名を確認してください", show=False, modal=True,
        width=420, height=180, no_close=True,
    ):
        dpg.add_text("デスクトップに新規フォルダを作って、その中にDXFと計算書を保存します。")
        dpg.add_text(f"(場所: ~/Desktop/{DESKTOP_OUTPUT_DIR_NAME}/)", color=(160, 160, 160, 255))
        dpg.add_separator()
        dpg.add_input_text(label="フォルダ名", tag=EXPORT_FOLDER_INPUT)
        dpg.add_text("", tag=EXPORT_FOLDER_STATUS, color=(255, 120, 120, 255))
        dpg.add_button(label="このフォルダ名で書き出す", callback=_on_export_folder_confirmed)

    with dpg.window(
        tag=EXPORT_DONE_WINDOW, label="書き出し完了", show=False, modal=True,
        width=420, height=160, no_close=True,
    ):
        dpg.add_text("", tag=EXPORT_DONE_TEXT, wrap=380)
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_button(label="フォルダを開く", callback=_on_export_done_open_folder)
            dpg.add_button(label="開かない", callback=_on_export_done_skip)

    with dpg.window(
        tag=LATH_AREA_WINDOW, label="ラス面積を確認してください", show=False, modal=True,
        width=420, height=180, no_close=True,
    ):
        dpg.add_text("今読み込んだラス網展開図(求積図)に記載されているラス面積を入力してください。")
        dpg.add_text("(数量計算(計算書)の中詰め面積の算出に使います)", color=(160, 160, 160, 255))
        dpg.add_separator()
        dpg.add_input_float(label="ラス面積 (m2)", tag=LATH_AREA_INPUT, default_value=0.0, step=1.0)
        dpg.add_text("", tag=LATH_AREA_STATUS, color=(255, 120, 120, 255))
        dpg.add_button(label="この面積で確定", callback=_on_lath_area_confirmed)

    with dpg.window(
        tag=SPEC_WINDOW, label="法枠の規格を確認してください", show=False, modal=True,
        width=420, height=470, no_close=True,
    ):
        dpg.add_text("この現場で使う法枠の規格を入力してください。")
        dpg.add_text("(枠の断面は正方形。標準は300mm角、中心間距離2000mm)", color=(160, 160, 160, 255))
        dpg.add_separator()
        dpg.add_input_float(label="枠の一辺 (mm) ※正方形", tag=SPEC_WIDTH_MM, default_value=300.0, step=10)
        dpg.add_input_float(label="中心間距離 ピッチ (mm) ※これが上限値", tag=SPEC_PITCH_MM, default_value=2000.0, step=100)
        dpg.add_input_float(label="法面勾配 1:n の n", tag=SPEC_GRADIENT_N, default_value=1.0, step=0.1)
        dpg.add_checkbox(
            label="勾配不明(現場のまま・未確定) — 水切りモルタルの断面はあとで決める",
            tag=SPEC_GRADIENT_UNKNOWN,
            callback=lambda s, a: dpg.configure_item(SPEC_GRADIENT_N, enabled=not a),
        )
        dpg.add_input_float(label="端部の最小スパン目安 (mm)", tag=SPEC_MIN_SEGMENT_MM, default_value=600.0, step=50)
        dpg.add_separator()
        dpg.add_input_float(
            label="縮尺 1:100 固定(ラス図・割付図はこれ以外を受け付けません)",
            tag=SPEC_SCALE_DENOM, default_value=100.0, step=10, enabled=False,
        )
        dpg.add_separator()
        dpg.add_text("中詰め工の種類(水切り・水抜きパイプの配置、数量に使用)")
        dpg.add_radio_button(
            [NAKAZUME_KOSOKISOZAI, NAKAZUME_MORTAR],
            tag=SPEC_NAKAZUME_TYPE, default_value=NAKAZUME_MORTAR,
        )
        dpg.add_input_float(label="中詰めの厚さ (cm)", tag=SPEC_NAKAZUME_THICKNESS_CM, default_value=10.0, step=1)
        dpg.add_separator()
        dpg.add_button(label="この規格で入力へ進む", callback=_on_spec_confirmed)

    _state["world_bounds"] = _compute_world_bounds()

    with dpg.window(tag="main_window"):
        with dpg.group(horizontal=True):
            with dpg.child_window(width=300, autosize_y=True):
                dpg.add_text("仮割付プロトタイプ")
                dpg.add_separator()
                dpg.add_text("① まずラス網展開図(DXFファイル)を開いてください", wrap=280, color=(230, 200, 120, 255))
                dpg.add_button(label="ラス網展開図(DXF)を選択...", callback=lambda: dpg.show_item("open_mesh_dxf_dialog"))
                dpg.add_separator()
                with dpg.group(tag="layout_step_group", show=False):
                    dpg.add_text(
                        "② それをもとに割付図(中心線だけで構いません)を作成し、入力してください",
                        wrap=280, color=(230, 200, 120, 255),
                    )
                    dpg.add_button(label="割付図(DXF)を選択...", callback=lambda: dpg.show_item("open_layout_dxf_dialog"))
                    dpg.add_button(label="フォルダを指定してから開く...", callback=lambda: dpg.show_item("choose_folder_dialog"))
                dpg.add_separator()
                with dpg.group(tag="main_controls", show=False):
                    dpg.add_radio_button(
                        list(PAGE_LABEL_TO_TAG.keys()),
                        tag=PAGE_RADIO, default_value="① 中心線を読み込む",
                        callback=_on_page_radio_change,
                    )
                    dpg.add_separator()

                    with dpg.group(tag=TAB_CENTERLINE_IMPORT, show=True):
                        dpg.add_text(
                            "現場のCADで割付図に描いた境界(枠の外側)・法枠中心線を、レイヤー指定で読み込みます。"
                            "指定すると、境界入力タブでの手クリックより優先されます(製図・数量はこれらの中心線を使います)。",
                            wrap=280,
                        )
                        dpg.add_button(label="レイヤー一覧を更新", callback=_refresh_layer_combos)
                        dpg.add_combo([], label="境界(枠の外側)レイヤー", tag=BOUNDARY_LAYER_COMBO, callback=_on_boundary_layer_change)
                        dpg.add_combo([], label="横梁中心線レイヤー", tag=BEAM_LAYER_COMBO, callback=_on_beam_layer_change)
                        dpg.add_combo([], label="縦柱中心線レイヤー", tag=POST_LAYER_COMBO, callback=_on_post_layer_change)
                        dpg.add_separator()
                        dpg.add_text("割付図の修正提案:", color=(230, 170, 90, 255))
                        dpg.add_text(
                            "クリックすると対応する丸が白くなります。不要な警告は選んで消去できます。",
                            wrap=280, color=(150, 150, 150, 255),
                        )
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="選択した警告を消去", callback=_on_dismiss_selected_warning)
                            dpg.add_button(label="消去した警告を復活", callback=_on_restore_dismissed_warnings)
                        with dpg.group(tag=SUGGESTION_LIST_TAG):
                            pass
                        dpg.add_separator()
                        dpg.add_text(
                            "枠が付いていない・消えている等おかしい部材があれば、"
                            "下のモードをONにして中心線をクリックで選択できます。",
                            wrap=280, color=(150, 150, 150, 255),
                        )
                        dpg.add_checkbox(label="部材選択モード", callback=_on_toggle_select_member_mode)
                        dpg.add_button(label="選択した部材を元の中心線のまま復帰する", callback=_on_recover_selected_member)

                    with dpg.group(tag=TAB_DRAW, show=False):
                        dpg.add_radio_button(
                            [MODE_DRAW, MODE_EDGE], tag=DRAW_SUBMODE_RADIO, default_value=MODE_DRAW,
                            callback=_on_mode_change,
                        )
                        dpg.add_text("頂点: クリックした位置がそのまま頂点になります", wrap=280)
                        dpg.add_text("辺をクリック: 背景の線をクリックすると、その辺の両端が頂点として追加されます", wrap=280)
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="1点戻す", callback=_undo_vertex)
                            dpg.add_button(label="この段を確定", callback=_finish_tier)
                        dpg.add_button(label="境界を全てクリア", callback=_clear_shape)
                        dpg.add_separator()
                        dpg.add_button(label="境界入力を完了(拡大表示)", callback=_finish_all_boundary_input)
                        dpg.add_separator()
                        dpg.add_text("段一覧:")
                        with dpg.group(tag=TIER_LIST_TAG):
                            pass

                    dpg.add_separator()
                    dpg.add_button(
                        label="この状態でDXFに書き出す...",
                        callback=lambda: _open_export_dialog(),
                    )
                dpg.add_separator()
                dpg.add_text("", tag=STATUS_TAG, wrap=280)
            with dpg.child_window(width=MESH_CANVAS_W + 20, autosize_y=True, tag=MESH_CANVAS_WINDOW_TAG):
                dpg.add_text("ラス網展開図(参照表示)")
                with dpg.drawlist(width=MESH_CANVAS_W, height=MESH_CANVAS_H, tag=MESH_CANVAS_TAG):
                    pass
            with dpg.child_window(autosize_y=True, tag=LAYOUT_CANVAS_WINDOW_TAG, show=False):
                dpg.add_text("プレビュー(灰=割付図, 白=境界, 赤=周囲枠, 黄=横梁, 青=縦柱, 橙=描画中の頂点)")
                with dpg.drawlist(width=CANVAS_W, height=CANVAS_H, tag=CANVAS_TAG):
                    pass

    with dpg.item_handler_registry(tag="canvas_click_handler"):
        dpg.add_item_clicked_handler(callback=_on_canvas_click)
    dpg.bind_item_handler_registry(CANVAS_TAG, "canvas_click_handler")

    # キャンバスは同時に1枚しか表示しないので、以前(2枚並べていた頃)より
    # 必要な幅はずっと小さい。画面より大きくならないよう、内容に合わせた
    # 控えめな既定サイズにする(resizable=Trueなので、必要ならユーザーが
    # 手動で広げられる)。
    viewport_w, viewport_h = _get_viewport_size()
    dpg.create_viewport(title="法枠 仮割付プロトタイプ", width=viewport_w, height=viewport_h, resizable=True)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("main_window", True)

    _redraw()
    dpg.show_item(INTRO_WINDOW)

    while dpg.is_dearpygui_running():
        dpg.render_dearpygui_frame()
        if _state["close_at"] is not None and time.monotonic() >= _state["close_at"]:
            break
    dpg.destroy_context()


def main() -> None:
    build_gui()


if __name__ == "__main__":
    main()
