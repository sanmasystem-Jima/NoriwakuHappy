"""割付の各ステージをDXFに書き出す(段階的な確認・現場への提示用)。

境界(枠の外側)から周囲枠は常に自動計算する。横梁・縦柱は、現場のCADで
手描きした中心線(manual_beams/manual_posts)をそのまま使う。自動割付
(パターンごとの自動計算)は廃止したので、中心線が渡されなければ
横梁・縦柱は何も書き出さない。
"""
from __future__ import annotations

import ezdxf

import math

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from .annotate import (
    TEXT_HEIGHT_MM,
    TextLabel,
    _leader,
    _readable_angle,
    frame_structure_geometry,
    mizukiri_triangle,
    mm_to_world,
    perimeter_labels,
    post_label,
    tier_beam_labels,
)
from .boundary import SlopeShape
from .beams import Beam
from .dxf_background import (
    SYSTEM_LAYERS,
    BackgroundGeometry,
    content_bounds,
    is_frame_chain,
    segments_excluding_frame,
)
from .drafting import group_beams_by_tier, perimeter_outer_inner, prepare_drawn_members
from .perimeter import compute_perimeter
from .posts import Post
from .spec import NAKAZUME_KOSOKISOZAI, NAKAZUME_MORTAR, FrameRule, ScaleNotAllowedError

LINETYPE_CENTERLINE = "CENTER"
CIRCLE_RADIUS_MM = 0.5
MIZUKIRI_SCALE_DENOMINATOR = 10.0  # 水切り詳細の縮尺1/10(決め打ち)。割付図・展開図はscale_denominator(通常1/100)
FRAME_STRUCTURE_SCALE_DENOMINATOR = 50.0  # 法枠の構造図(縦断面)の縮尺1/50(決め打ち)

LAYER_PERIMETER = "STAGE1_周囲枠"
LAYER_BEAMS = "STAGE2_横梁"
LAYER_POSTS = "STAGE3_縦柱"
LAYER_TRUE_BOUNDARY = "TRUE_境界"
LAYER_DRAW_FRAME = "図面_法枠外形"  # 周囲枠・縦柱・横梁の実際の縁を、交点で包絡処理(和集合)した外形線
LAYER_MIZUKIRI = "図面_水切り詳細"
LAYER_FRAME_STRUCTURE = "図面_構造図"
LAYER_MESH = "図面_ラス網展開図"
LAYER_SCALE_CHECK = "_SCALE_CHECK"
MESH_GAP = 2.0  # 割付図とラス網展開図の間の余白(m)
FIXED_SCALE_DENOMINATOR = 100.0  # ラス図・割付図は1/100固定(DXFSCALE.MD §4.1)


def _add_text(msp, label, layer) -> None:
    # レイヤーの既定線種が一点鎖線(CENTER)でも、文字は必ず実線で読めるように
    # linetypeを明示的にContinuousで上書きする。
    msp.add_text(
        label.text,
        dxfattribs={
            "layer": layer, "height": label.height, "rotation": label.rotation,
            "linetype": "Continuous",
        },
    ).set_placement(label.position, align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)


def _add_circle(msp, point, layer, scale_denominator: float) -> None:
    msp.add_circle(
        point, radius=mm_to_world(CIRCLE_RADIUS_MM, scale_denominator),
        dxfattribs={"layer": layer, "linetype": "Continuous"},
    )


def _extend_polyline_ends(
    points: list[tuple[float, float]], margin: float
) -> list[tuple[float, float]]:
    """折れ線の両端点を、それぞれの端の向きに沿ってmarginだけ延長する。"""
    if len(points) < 2 or margin <= 0:
        return points
    pts = list(points)
    x0, y0 = pts[0]
    x1, y1 = pts[1]
    dx, dy = x0 - x1, y0 - y1
    length = math.hypot(dx, dy)
    if length > 1e-9:
        pts[0] = (x0 + dx / length * margin, y0 + dy / length * margin)
    xn, yn = pts[-1]
    xn1, yn1 = pts[-2]
    dx, dy = xn - xn1, yn - yn1
    length = math.hypot(dx, dy)
    if length > 1e-9:
        pts[-1] = (xn + dx / length * margin, yn + dy / length * margin)
    return pts


def _member_polygon(
    points: list[tuple[float, float]], half_width: float, true_boundary: Polygon | None = None
) -> Polygon | None:
    """縦柱・横梁の中心線を、枠幅/2だけbufferして、その部材が占める図形を作る。

    以前はedge_a/edge_b(左右にオフセットした2本の線)を組み合わせて
    ポリゴンを作っていたが、中心線に山型の折れ点があると2本のオフセット線
    の点の対応が崩れ、自己交差するポリゴンができてしまうことがあった
    (buffer(0)による「修復」がスパイク状の切れ込みを作ってしまう)。
    中心線を直接bufferする方式なら、折れ点があってもshapely自身が正しく
    処理するので、そうした不具合が起きない。

    中心線は既に周囲枠の内側面ぴったりでクリップ済みなので、そのまま
    cap_style="flat"でbufferすると、周囲枠が斜めに走っている箇所では
    枠の外側の線が内側面まで届かず途中で切れて見える。周囲枠と部材が
    ほぼ平行に近い角度で交わる場合、延長量が枠幅ぶん程度では足りない
    こともある(延長方向がほぼ境界に沿ってしまい、境界を横切る方向には
    ほとんど進まないため)。そこで、true_boundary(段の真の境界=外形)の
    対角線ぶんという、どんな角度でも確実に届く長さだけ延長してから
    bufferし、そのあとtrue_boundaryと交差させることで、真の境界の外に
    はみ出す分だけを切り取る。延長量を正確に見積もる必要がなくなり、
    どんな角度で境界と交わっても対応できる。
    """
    if len(points) < 2:
        return None
    if true_boundary is not None:
        minx, miny, maxx, maxy = true_boundary.bounds
        margin = math.hypot(maxx - minx, maxy - miny)
    else:
        margin = half_width * 2
    extended = _extend_polyline_ends(points, margin)
    poly = LineString(extended).buffer(half_width, cap_style="flat")
    if poly.is_empty:
        return None
    if true_boundary is not None:
        poly = poly.intersection(true_boundary)
        if poly.is_empty:
            return None
    return poly


def _perimeter_ring_polygon(tier_polygon: Polygon, frame_width: float) -> Polygon | None:
    """周囲枠(境界の内側にframe_widthぶん入った輪=実際の枠の材料部分)の図形。"""
    outer, inner = perimeter_outer_inner(tier_polygon, frame_width)
    ring = Polygon(list(outer.exterior.coords), holes=[list(inner.exterior.coords)])
    if not ring.is_valid:
        ring = ring.buffer(0)
    if ring.is_empty:
        return None
    return ring


def _add_envelope_outline(msp, polygons: list[Polygon], layer: str) -> None:
    """周囲枠・縦柱・横梁の実際の縁をまとめて包絡処理(和集合)してから
    輪郭だけを描く。

    個々の部材の縁線をそのまま並べて描くと、枠どうしが交わる交点(縦柱・
    横梁の端点が周囲枠に取り付く箇所を含む)でお互いの縁が交差してしまい
    見づらい(実際の法枠は交点で連続したコンクリートなので、交差する縁は
    現れない)。和集合を取ってからその輪郭だけを描くことで、交点をきれいな
    外形線にする。
    """
    polys = [p for p in polygons if p is not None]
    if not polys:
        return
    merged = unary_union(polys)
    geoms = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    for geom in geoms:
        if geom.is_empty:
            continue
        msp.add_lwpolyline(list(geom.exterior.coords), dxfattribs={"layer": layer})
        for interior in geom.interiors:
            msp.add_lwpolyline(list(interior.coords), dxfattribs={"layer": layer})


def export_stages(
    path: str,
    shape: SlopeShape,
    rule: FrameRule,
    manual_beams: list[Beam] | None = None,
    manual_posts: list[Post] | None = None,
    scale_denominator: float = 100.0,
    nakazume_type: str = NAKAZUME_MORTAR,
    mesh_background: BackgroundGeometry | None = None,
    mesh_dxf_path: str | None = None,
    skip_trim_keys: set[tuple] | None = None,
) -> None:
    if scale_denominator != FIXED_SCALE_DENOMINATOR:
        raise ScaleNotAllowedError(
            f"ラス図・割付図は1/{FIXED_SCALE_DENOMINATOR:.0f}固定です"
            f"(指定値 1/{scale_denominator:.0f} は受け付けません)"
        )

    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()

    # DXFSCALE.MD §7.1/7.2: 縮尺の痕跡をヘッダーに明記する(defaultに頼らない)。
    doc.header["$INSUNITS"] = 6  # 6=メートル(内部座標は実装上ずっとメートル)
    doc.header["$MEASUREMENT"] = 1
    doc.header.custom_vars.append("SCALE_DENOM", str(int(scale_denominator)))
    doc.header.custom_vars.append("GEN_TOOL", "houwakuHappy")

    doc.layers.add(LAYER_TRUE_BOUNDARY, color=8)
    doc.layers.add(LAYER_PERIMETER, color=1, linetype=LINETYPE_CENTERLINE)
    doc.layers.add(LAYER_BEAMS, color=2, linetype=LINETYPE_CENTERLINE)
    doc.layers.add(LAYER_POSTS, color=4, linetype=LINETYPE_CENTERLINE)
    doc.layers.add(LAYER_DRAW_FRAME, color=7)
    doc.layers.add(LAYER_MIZUKIRI, color=6)
    doc.layers.add(LAYER_FRAME_STRUCTURE, color=3)
    doc.layers.add(LAYER_MESH, color=8)
    doc.layers.add(LAYER_SCALE_CHECK, color=1)

    tier_polys = [t.polygon for t in shape.tiers]
    beams, posts, crossing_counts, _beam_edges, _post_edges = prepare_drawn_members(
        tier_polys, rule.frame_width, manual_beams or [], manual_posts or [], skip_trim_keys
    )
    outer_polys = [perimeter_outer_inner(poly, rule.frame_width)[0] for poly in tier_polys]
    beam_counts = dict(zip((id(b) for b in beams), crossing_counts))
    beam_groups = group_beams_by_tier(tier_polys, rule.frame_width, beams)

    for post in posts:
        msp.add_line(post.start, post.end, dxfattribs={"layer": LAYER_POSTS})
        _add_circle(msp, post.start, LAYER_POSTS, scale_denominator)
        _add_circle(msp, post.end, LAYER_POSTS, scale_denominator)
        _add_text(msp, post_label(post, scale_denominator), LAYER_POSTS)

    for beam in beams:
        msp.add_lwpolyline(beam.points, dxfattribs={"layer": LAYER_BEAMS})
        _add_circle(msp, beam.points[0], LAYER_BEAMS, scale_denominator)
        _add_circle(msp, beam.points[-1], LAYER_BEAMS, scale_denominator)

    for tier_idx, tier_beams in enumerate(beam_groups):
        tier_counts = [beam_counts[id(b)] for b in tier_beams]
        for label in tier_beam_labels(tier_beams, tier_counts, outer_polys[tier_idx], scale_denominator):
            _add_text(msp, label, LAYER_BEAMS)

    # 周囲枠・縦柱・横梁の実際の縁は、交点(周囲枠に縦柱・横梁の端点が
    # 取り付く箇所を含む)で個別に描くと縁が交差して見づらいので、
    # まとめて包絡処理(和集合)してから外形線だけ描く。
    half_width = rule.frame_width / 2
    true_boundary = unary_union(tier_polys)
    envelope_polys = (
        [_member_polygon([p.start, p.end], half_width, true_boundary) for p in posts]
        + [_member_polygon(b.points, half_width, true_boundary) for b in beams]
    )

    for tier in shape.tiers:
        coords = list(tier.polygon.exterior.coords)
        msp.add_lwpolyline(coords, dxfattribs={"layer": LAYER_TRUE_BOUNDARY})

        perim = compute_perimeter(tier, rule)
        for seg in perim.segments:
            msp.add_line(seg.start, seg.end, dxfattribs={"layer": LAYER_PERIMETER})
            _add_circle(msp, seg.start, LAYER_PERIMETER, scale_denominator)
            _add_circle(msp, seg.end, LAYER_PERIMETER, scale_denominator)
        for label in perimeter_labels(perim.centerline, scale_denominator):
            _add_text(msp, label, LAYER_PERIMETER)

        envelope_polys.append(_perimeter_ring_polygon(tier.polygon, rule.frame_width))

    _add_envelope_outline(msp, envelope_polys, LAYER_DRAW_FRAME)

    if rule.gradient_n is not None:
        mizukiri_bottom_y = _add_mizukiri_detail(msp, shape, rule, scale_denominator)
        _add_frame_structure_drawing(
            msp, shape, rule, nakazume_type, scale_denominator, mizukiri_bottom_y
        )

    if mesh_background is not None:
        _add_mesh_diagram(msp, shape, mesh_background, mesh_dxf_path)

    # DXFSCALE.MD §7.4: 検算線。読込側で1.000mと測れれば単位・縮尺が壊れていない証拠。
    msp.add_line((0.0, 0.0), (1.0, 0.0), dxfattribs={"layer": LAYER_SCALE_CHECK})

    doc.saveas(path)
    print(f"[WARN] {LAYER_SCALE_CHECK} レイヤーを含んでいます。納品前に削除してください。")


def _copy_mesh_entities(
    msp,
    mesh_dxf_path: str,
    mesh_background: BackgroundGeometry,
    shift: tuple[float, float],
) -> None:
    """元のラス網展開図DXFを開き直し、システムレイヤー(SYSTEM_LAYERS。用紙枠等、
    レイヤー名で判定)と外枠(全体の外形と一致する閉じたポリライン、is_frame_chain
    で判定)を除く全エンティティ(線・円弧・表の罫線・文字など)を、忠実にコピーする。

    以前はレイヤー名を見ずis_frame_chain(図形の比率)だけで判定していたが、
    V-nas系ファイルに付き物の用紙枠の入れ子矩形など、全体範囲との比率が
    90%未満のものはすり抜けてしまっていた(load_background側は既にSYSTEM_LAYERSで
    除外済みなのに、こちらは別読み込み経路のため素通りしていた)。
    load_backgroundと同じSYSTEM_LAYERSを使うことで、判定経路を1本化する。

    割付図側は、GUIでの読込み時に2点指定でラス網展開図と同じ実寸の
    縮尺になるよう校正済みという前提なので、ここでは追加の拡大縮小は
    行わない(高さを無理に合わせようとすると、逆に校正済みの縮尺が
    ズレてしまう)。
    """
    base_scale = mesh_background.unit_scale
    scale = base_scale
    sx, sy = shift

    def tf(x: float, y: float) -> tuple[float, float]:
        return (x * scale + sx, y * scale + sy)

    src_doc = ezdxf.readfile(mesh_dxf_path)
    src_msp = src_doc.modelspace()

    for e in src_msp:
        if e.dxf.layer in SYSTEM_LAYERS:
            continue
        dxftype = e.dxftype()
        if dxftype == "LINE":
            base_pts = [
                (e.dxf.start.x * base_scale, e.dxf.start.y * base_scale),
                (e.dxf.end.x * base_scale, e.dxf.end.y * base_scale),
            ]
            if is_frame_chain(base_pts, mesh_background.bounds):
                continue
            msp.add_line(
                tf(e.dxf.start.x, e.dxf.start.y), tf(e.dxf.end.x, e.dxf.end.y),
                dxfattribs={"layer": LAYER_MESH},
            )
        elif dxftype in ("LWPOLYLINE", "POLYLINE"):
            if dxftype == "LWPOLYLINE":
                raw_pts = [(p[0], p[1]) for p in e.get_points()]
                closed = e.closed
            else:
                raw_pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                closed = e.is_closed
            if len(raw_pts) < 2:
                continue
            if closed:
                raw_pts = raw_pts + [raw_pts[0]]
            base_pts = [(x * base_scale, y * base_scale) for x, y in raw_pts]
            if is_frame_chain(base_pts, mesh_background.bounds):
                continue
            world_pts = [(x + sx, y + sy) for x, y in base_pts]
            msp.add_lwpolyline(world_pts, dxfattribs={"layer": LAYER_MESH})
        elif dxftype == "ARC":
            center = e.dxf.center
            msp.add_arc(
                center=tf(center.x, center.y), radius=e.dxf.radius * scale,
                start_angle=e.dxf.start_angle, end_angle=e.dxf.end_angle,
                dxfattribs={"layer": LAYER_MESH},
            )
        elif dxftype == "CIRCLE":
            center = e.dxf.center
            msp.add_circle(tf(center.x, center.y), e.dxf.radius * scale, dxfattribs={"layer": LAYER_MESH})
        elif dxftype == "TEXT":
            insert = e.dxf.insert
            msp.add_text(
                e.dxf.text,
                dxfattribs={
                    "layer": LAYER_MESH, "height": e.dxf.height * scale,
                    "rotation": e.dxf.rotation, "linetype": "Continuous",
                },
            ).set_placement(tf(insert.x, insert.y))
        elif dxftype == "MTEXT":
            insert = e.dxf.insert
            msp.add_text(
                e.plain_text(),
                dxfattribs={
                    "layer": LAYER_MESH, "height": e.dxf.char_height * scale,
                    "rotation": getattr(e.dxf, "rotation", 0.0), "linetype": "Continuous",
                },
            ).set_placement(tf(insert.x, insert.y))


def _add_mesh_diagram(
    msp, shape: SlopeShape, mesh_background: BackgroundGeometry, mesh_dxf_path: str | None = None
) -> None:
    """ラス網展開図を、割付図の右側に元図面に忠実に並べて表示する(外枠は除く)。

    割付図は、GUIでの読込み時に2点指定でラス網展開図と同じ実寸の縮尺に
    なるよう校正済みという前提なので、ここでは平行移動だけ行い、追加の
    拡大縮小はしない(していまうと、校正済みの縮尺が逆にズレる)。
    """
    _minx, miny, maxx, _maxy = shape.overall_bounds
    mesh_minx, mesh_miny, _mesh_maxx, _mesh_maxy = content_bounds(mesh_background)
    shift = (maxx + MESH_GAP - mesh_minx, miny - mesh_miny)

    if mesh_dxf_path:
        _copy_mesh_entities(msp, mesh_dxf_path, mesh_background, shift)
        return

    shift_x, shift_y = shift
    for (ax, ay), (bx, by) in segments_excluding_frame(mesh_background):
        msp.add_line(
            (ax + shift_x, ay + shift_y),
            (bx + shift_x, by + shift_y),
            dxfattribs={"layer": LAYER_MESH},
        )


def _add_mizukiri_detail(msp, shape: SlopeShape, rule: FrameRule, scale_denominator: float) -> float:
    """水切りモルタルの断面詳細(法肩・法尻)を、割付図の下に数量根拠として描く。

    割付図・展開図は縮尺1/scale_denominator(例: 1/100)、構造図(この詳細)は
    1/MIZUKIRI_SCALE_DENOMINATOR(決め打ちで1/10)を想定。実寸のまま描くと
    割付図に対して小さすぎて見えなくなるため、両者の縮尺比
    (scale_denominator / MIZUKIRI_SCALE_DENOMINATOR)だけ図形を拡大して
    描く。数量(断面積)は実寸のまま(拡大しない)計算する。
    """
    area = rule.mizukiri_cross_section_area
    exaggeration = scale_denominator / MIZUKIRI_SCALE_DENOMINATOR
    # 文字も、構造図自身の縮尺(1/MIZUKIRI_SCALE_DENOMINATOR)で求めた実寸を、
    # 図形と同じ比率で拡大する(結果として他の文字と同じ最終印刷サイズになる)。
    text_height = mm_to_world(TEXT_HEIGHT_MM, MIZUKIRI_SCALE_DENOMINATOR) * exaggeration
    # 直角三角形: 水平な辺(斜辺、一番長い)、勾配の脚(frame_height*n)、
    # 枠厚の脚(frame_height)が直角に交わる。法肩・法尻は180度回転の関係。
    drawn_height = rule.frame_height * exaggeration
    drawn_hyp = drawn_height * math.hypot(rule.gradient_n, 1.0)
    drawn_vertical = drawn_height * rule.gradient_n / math.hypot(rule.gradient_n, 1.0)

    minx, miny, _maxx, _maxy = shape.overall_bounds
    margin = drawn_vertical * 2
    base_y = miny - margin - drawn_vertical
    gap_between = drawn_hyp * 1.3

    apex_origin = (minx, base_y)
    toe_origin = (minx + gap_between, base_y)

    # 寸法(実寸、拡大しない)。
    real_hyp = rule.frame_height * math.hypot(rule.gradient_n, 1.0)
    real_slope_leg = rule.frame_height * rule.gradient_n
    real_perp_leg = rule.frame_height

    for origin, apex_at_bottom, label in ((apex_origin, True, "法肩"), (toe_origin, False, "法尻")):
        pts = mizukiri_triangle(drawn_height, rule.gradient_n, apex_at_bottom, origin)
        msp.add_lwpolyline(pts, dxfattribs={"layer": LAYER_MIZUKIRI})
        center_x = origin[0] + drawn_hyp / 2
        top_y = base_y + drawn_vertical

        _add_text(
            msp,
            TextLabel(label, (center_x, top_y + text_height), 0.0, text_height),
            LAYER_MIZUKIRI,
        )
        _add_text(
            msp,
            TextLabel(f"勾配 1:{rule.gradient_n:.2f}", (center_x, base_y - text_height * 2), 0.0, text_height),
            LAYER_MIZUKIRI,
        )
        _add_text(
            msp,
            TextLabel(
                f"断面積 {area:.3f}m2 / 体積(1mあたり) {area:.3f}m3",
                (center_x, base_y - text_height * 3.5), 0.0, text_height,
            ),
            LAYER_MIZUKIRI,
        )

        # 水平な辺(斜辺)の寸法(三角形の内側、辺のすぐ下/上に)
        flat_y = top_y - text_height if apex_at_bottom else base_y + text_height
        _add_text(
            msp,
            TextLabel(f"{real_hyp:.3f}", (center_x, flat_y), 0.0, text_height),
            LAYER_MIZUKIRI,
        )

        # 勾配の脚・枠厚の脚それぞれの寸法(辺の傾きに合わせて、中点に)。
        # 法肩は左角=枠厚、右角=勾配。法尻はその逆(180度回転)。
        left, right, apex_pt = pts[0], pts[1], pts[2]
        leg_pairs = (
            (left, real_perp_leg if apex_at_bottom else real_slope_leg),
            (right, real_slope_leg if apex_at_bottom else real_perp_leg),
        )
        for corner, real_length in leg_pairs:
            mid = ((corner[0] + apex_pt[0]) / 2, (corner[1] + apex_pt[1]) / 2)
            dx, dy = apex_pt[0] - corner[0], apex_pt[1] - corner[1]
            angle = _readable_angle(dx, dy)
            _add_text(
                msp,
                TextLabel(f"{real_length:.3f}", mid, angle, text_height),
                LAYER_MIZUKIRI,
            )

    scale_label_x = minx + gap_between / 2
    _add_text(
        msp,
        TextLabel(f"縮尺 1:{MIZUKIRI_SCALE_DENOMINATOR:.0f}", (scale_label_x, base_y - text_height * 5), 0.0, text_height),
        LAYER_MIZUKIRI,
    )

    return base_y - text_height * 6


def _transform_points(points, factor: float, shift: tuple[float, float]):
    return [(x * factor + shift[0], y * factor + shift[1]) for x, y in points]


def _add_frame_structure_drawing(
    msp,
    shape: SlopeShape,
    rule: FrameRule,
    nakazume_type: str,
    scale_denominator: float,
    top_y: float,
) -> None:
    """法枠の構造図(縦断面、縮尺1/50固定)を、水切り詳細のさらに下に描く。

    割付図の高さ・法長は考慮しない模式図(構造を示すもの)。格子間隔は
    仕様のpitchをそのまま使い、梁の断面(正方形)を5個並べる。中詰めが
    厚層基材吹き付け工の場合のみ、中段の格子にも水切りモルタルの簡易線を描く。
    """
    geom = frame_structure_geometry(rule.frame_height, rule.gradient_n, rule.pitch)
    exaggeration = scale_denominator / FRAME_STRUCTURE_SCALE_DENOMINATOR
    text_height = mm_to_world(TEXT_HEIGHT_MM, FRAME_STRUCTURE_SCALE_DENOMINATOR) * exaggeration

    all_points = [
        *geom.inner_edge, *geom.outer_edge, *geom.toe_wedge, *geom.crest_wedge,
        *geom.toe_ground, *geom.crest_ground, *geom.beam_centers,
    ]
    for square in geom.beam_squares:
        all_points.extend(square)
    for a, b in geom.mid_wedge_lines:
        all_points.extend((a, b))

    min_x = min(p[0] for p in all_points)
    max_y = max(p[1] for p in all_points)
    min_y = min(p[1] for p in all_points)
    margin = text_height * 4
    shift = (
        shape.overall_bounds[0] - min_x * exaggeration,
        (top_y - margin) - max_y * exaggeration,
    )

    def tf(points):
        return _transform_points(points, exaggeration, shift)

    def tf1(p):
        return tf([p])[0]

    msp.add_line(*tf(geom.inner_edge), dxfattribs={"layer": LAYER_FRAME_STRUCTURE})
    msp.add_line(*tf(geom.outer_edge), dxfattribs={"layer": LAYER_FRAME_STRUCTURE})
    msp.add_line(*tf(geom.toe_ground), dxfattribs={"layer": LAYER_FRAME_STRUCTURE})
    msp.add_line(*tf(geom.crest_ground), dxfattribs={"layer": LAYER_FRAME_STRUCTURE})
    msp.add_lwpolyline(tf(geom.toe_wedge), dxfattribs={"layer": LAYER_FRAME_STRUCTURE})
    msp.add_lwpolyline(tf(geom.crest_wedge), dxfattribs={"layer": LAYER_FRAME_STRUCTURE})
    for square in geom.beam_squares:
        msp.add_lwpolyline(tf(square), dxfattribs={"layer": LAYER_FRAME_STRUCTURE})

    is_koso = nakazume_type == NAKAZUME_KOSOKISOZAI
    if is_koso:
        for a, b in geom.mid_wedge_lines:
            msp.add_line(tf1(a), tf1(b), dxfattribs={"layer": LAYER_FRAME_STRUCTURE})

    dir_perp = geom.dir_perp
    leader_len = text_height * 6
    for i, center in enumerate(geom.beam_centers):
        anchor = tf1((
            center[0] + rule.frame_height / 2 * dir_perp[0],
            center[1] + rule.frame_height / 2 * dir_perp[1],
        ))
        leader = _leader(anchor, dir_perp, leader_len, "梁", text_height)
        msp.add_line(*leader.line, dxfattribs={"layer": LAYER_FRAME_STRUCTURE})
        _add_text(msp, leader.label, LAYER_FRAME_STRUCTURE)

    mizukiri_anchors = (
        (((geom.toe_wedge[0][0] + geom.toe_wedge[1][0]) / 2, geom.toe_wedge[0][1]), (-1.0, 0.4)),
        (((geom.crest_wedge[0][0] + geom.crest_wedge[1][0]) / 2, geom.crest_wedge[0][1]), (1.0, 1.0)),
    )
    for point, direction in mizukiri_anchors:
        leader = _leader(tf1(point), direction, leader_len * 1.5, "水切りモルタル", text_height)
        msp.add_line(*leader.line, dxfattribs={"layer": LAYER_FRAME_STRUCTURE})
        _add_text(msp, leader.label, LAYER_FRAME_STRUCTURE)

    centerline0 = geom.beam_centers[0]
    for t_ratio in (0.5, 2.5):
        t = t_ratio * rule.pitch
        anchor = tf1((
            centerline0[0] + t * geom.dir_along[0] + rule.frame_height / 2 * dir_perp[0],
            centerline0[1] + t * geom.dir_along[1] + rule.frame_height / 2 * dir_perp[1],
        ))
        leader = _leader(anchor, dir_perp, leader_len, nakazume_type, text_height)
        msp.add_line(*leader.line, dxfattribs={"layer": LAYER_FRAME_STRUCTURE})
        _add_text(msp, leader.label, LAYER_FRAME_STRUCTURE)

    mid_beam = geom.beam_centers[len(geom.beam_centers) // 2]
    slope_offset = rule.frame_height * 1.5
    slope_point = tf1((
        mid_beam[0] + slope_offset * dir_perp[0], mid_beam[1] + slope_offset * dir_perp[1],
    ))
    slope_angle = _readable_angle(geom.dir_along[0], geom.dir_along[1])
    _add_text(
        msp,
        TextLabel(f"▽1:{rule.gradient_n:.2f}", slope_point, slope_angle, text_height),
        LAYER_FRAME_STRUCTURE,
    )
    label_y = min_y * exaggeration + shift[1] - text_height * 5
    _add_text(
        msp,
        TextLabel(
            f"格子間隔 {rule.pitch:.3f}m / 縮尺 1:{FRAME_STRUCTURE_SCALE_DENOMINATOR:.0f}",
            (shape.overall_bounds[0], label_y), 0.0, text_height,
        ),
        LAYER_FRAME_STRUCTURE,
    )
