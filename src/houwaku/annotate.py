"""図面の寸法線・注記文字の配置(2026-07-30、ユーザー指定)。

文字高さ・オフセット量は、いずれも印刷スケールでのmm指定(縮尺1:Nの
Nを使って実寸mに換算する)。ユーザーが現場のCAD(V-nas)で作図する際に
使う縮尺は、DXF変換時に失われるため、割付図読込時に毎回入力してもらう
(interactive_gui.pyのSPEC_SCALE_DENOM)。

配置ルール:
1. 周囲枠: 中心線に平行に、中心線から4mm(換算後)離して、辺の中央を
   基準に配置。上下左右どの辺も同じルール。
2. 縦柱: 柱の中心線の傾きに合わせて、中心線から(左側に)4mm離して、
   中央に配置。
3. 横梁: 曲がっていても1本の連続線として扱う。段(tier)ごとに、左右
   どちらの端が実際に境界近くまで届いているか(=表示が消えていない方)
   を全ての梁の合計で1回だけ判定し、その段の梁は全てその同じ側に表示
   する(梁によって左右がばらばらにならないようにする)。真の外枠から
   図面の外側へ1cm(換算後)離して、水平な文字で長さ・交差本数
   (カッコ書き)を上下2段で表示する。

文字高さは全て統一で3.5mm(換算後)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import Point, Polygon

from .beams import Beam
from .drafting import _boundary_crossings_at_y
from .posts import Post

TEXT_HEIGHT_MM = 3.5
CENTERLINE_OFFSET_MM = 4.0  # 縦柱・周囲枠: 中心線からの離隔
BEAM_OFFSET_MM = 10.0  # 横梁: 真の外枠から図面の外側への離隔(約1cm)


def mm_to_world(mm: float, scale_denominator: float) -> float:
    """印刷スケールでのmm寸法を、縮尺1:Nを使って実寸(m)に変換する。"""
    return mm / 1000.0 * scale_denominator


@dataclass
class TextLabel:
    text: str
    position: tuple[float, float]
    rotation: float  # 度。0=水平
    height: float  # 実寸(m)


def _readable_angle(dx: float, dy: float) -> float:
    """文字が上下逆さまにならないよう、線の傾きから読みやすい回転角(度)を返す。"""
    angle = math.degrees(math.atan2(dy, dx))
    if angle > 90 or angle < -90:
        angle += 180
    return angle


def _outward_normal(polygon: Polygon, p1: tuple[float, float], p2: tuple[float, float]) -> tuple[float, float]:
    """辺(p1->p2)の中点における、多角形の外向き法線(単位ベクトル)。"""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return (0.0, 1.0)
    nx, ny = -dy / length, dx / length
    mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    cx, cy = polygon.centroid.x, polygon.centroid.y
    d_plus = math.hypot((mx + nx) - cx, (my + ny) - cy)
    d_minus = math.hypot((mx - nx) - cx, (my - ny) - cy)
    return (nx, ny) if d_plus > d_minus else (-nx, -ny)


def perimeter_labels(centerline_polygon: Polygon, scale_denominator: float) -> list[TextLabel]:
    """周囲枠の各辺の長さ(中心線の長さ)を、中心線に平行に、
    中心線から4mm(換算後)離して、辺の中央に配置する。
    """
    height = mm_to_world(TEXT_HEIGHT_MM, scale_denominator)
    clearance = mm_to_world(CENTERLINE_OFFSET_MM, scale_denominator)
    coords = list(centerline_polygon.exterior.coords)
    labels: list[TextLabel] = []
    for p1, p2 in zip(coords, coords[1:]):
        length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        if length < 1e-9:
            continue
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        nx, ny = _outward_normal(centerline_polygon, p1, p2)
        pos = (mx + nx * clearance, my + ny * clearance)
        angle = _readable_angle(p2[0] - p1[0], p2[1] - p1[1])
        labels.append(TextLabel(text=f"{length:.1f}", position=pos, rotation=angle, height=height))
    return labels


def post_label(post: Post, scale_denominator: float) -> TextLabel:
    """柱の長さを、柱の中心線の傾きに合わせて、中心線から左側に4mm
    (換算後)離した中央に配置する。
    """
    height = mm_to_world(TEXT_HEIGHT_MM, scale_denominator)
    offset = mm_to_world(CENTERLINE_OFFSET_MM, scale_denominator)

    p1, p2 = post.start, post.end
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        dx, dy, length = 0.0, 1.0, 1.0

    ox, oy = -dy / length, dx / length
    if ox > 0:  # 常に左側(x座標が減る方向)を向くようにする
        ox, oy = -ox, -oy

    mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    pos = (mx + ox * offset, my + oy * offset)
    angle = _readable_angle(dx, dy)
    return TextLabel(text=f"{post.length:.1f}", position=pos, rotation=angle, height=height)


def _beam_ends(beam: Beam) -> tuple[tuple[float, float], tuple[float, float]]:
    left_end, right_end = beam.points[0], beam.points[-1]
    if left_end[0] > right_end[0]:
        left_end, right_end = right_end, left_end
    return left_end, right_end


def tier_beam_labels(
    beams: list[Beam], counts: list[int], outer_polygon: Polygon | None, scale_denominator: float
) -> list[TextLabel]:
    """段(tier)内の梁をまとめて処理する。左右どちらの端が実際に境界近く
    まで届いているかを、その段の全ての梁の合計距離で1回だけ判定し、
    その段の梁は全て同じ側に表示する(梁ごとにばらばらにしない)。
    """
    if outer_polygon is None or not beams:
        return []
    height = mm_to_world(TEXT_HEIGHT_MM, scale_denominator)
    offset = mm_to_world(BEAM_OFFSET_MM, scale_denominator)

    total_left = 0.0
    total_right = 0.0
    ends: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for beam in beams:
        left_end, right_end = _beam_ends(beam)
        ends.append((left_end, right_end))
        total_left += outer_polygon.exterior.distance(Point(left_end))
        total_right += outer_polygon.exterior.distance(Point(right_end))
    use_left = total_left <= total_right

    labels: list[TextLabel] = []
    for beam, count, (left_end, right_end) in zip(beams, counts, ends):
        y = left_end[1] if use_left else right_end[1]
        xs = _boundary_crossings_at_y(outer_polygon, y)
        if not xs:
            continue
        x = (min(xs) - offset) if use_left else (max(xs) + offset)
        gap = height * 1.2
        labels.append(TextLabel(text=f"{beam.gross_length:.1f}", position=(x, y + gap / 2), rotation=0.0, height=height))
        labels.append(TextLabel(text=f"({count})", position=(x, y - gap / 2), rotation=0.0, height=height))
    return labels


def mizukiri_triangle(
    frame_height: float, gradient_n: float, apex_at_bottom: bool, origin: tuple[float, float] = (0.0, 0.0)
) -> list[tuple[float, float]]:
    """水切りモルタルの断面(直角三角形)の頂点列(閉じたポリライン)。

    作図手順(ユーザー指定): まず法面勾配(1:n)の向きに線を引き、その先端
    から法枠厚さ(frame_height)ぶん直角に線を引き、その先端から水平に
    戻って閉じる。水平な辺がこの直角三角形の斜辺にあたり、3辺の中で
    一番長い(frame_height*sqrt(n^2+1))。
    法肩(頂部)と法尻(最下部)はちょうど反対向き(180度回転の関係): 法肩は
    左の角が直角の頂点(枠厚の脚)・右の角が勾配の脚、法尻はその逆。
    apex_at_bottom=True: 法肩(頂部)用、頂点が下向き。
    apex_at_bottom=False: 法尻(最下部)用、頂点が上向き。
    """
    ox, oy = origin
    hyp = frame_height * math.hypot(gradient_n, 1.0)
    px = frame_height / math.hypot(gradient_n, 1.0)  # 直角の頂点(枠厚側の角)から見た、頂点までの水平距離
    h = frame_height * gradient_n / math.hypot(gradient_n, 1.0)  # 頂点までの高さ
    if apex_at_bottom:
        # 左角=枠厚の脚の角(直角)、右角=勾配の脚の角、頂点は下
        pts = [(0.0, h), (hyp, h), (px, 0.0), (0.0, h)]
    else:
        # 180度回転: 左角=勾配の脚の角、右角=枠厚の脚の角(直角)、頂点は上
        pts = [(0.0, 0.0), (hyp, 0.0), (hyp - px, h), (0.0, 0.0)]
    return [(ox + x, oy + y) for x, y in pts]


FRAME_STRUCTURE_NUM_BEAMS = 5  # 構造図に描く断面正方形(梁)の数


@dataclass
class FrameStructureGeometry:
    """法枠の構造図(縦断面)の主要な線・位置(2026-08、構造図01/02参照)。

    局所座標: 原点(0,0)は法尻側・内側の縁が地盤に接する点。
    dir_along(斜面に沿って法尻→法肩の向き)・dir_perp(内側→外側の向き)。
    """

    inner_edge: tuple[tuple[float, float], tuple[float, float]]
    outer_edge: tuple[tuple[float, float], tuple[float, float]]
    toe_wedge: list[tuple[float, float]]
    crest_wedge: list[tuple[float, float]]
    toe_ground: tuple[tuple[float, float], tuple[float, float]]
    crest_ground: tuple[tuple[float, float], tuple[float, float]]
    beam_squares: list[list[tuple[float, float]]]
    beam_centers: list[tuple[float, float]]
    mid_wedge_lines: list[tuple[tuple[float, float], tuple[float, float]]]
    dir_along: tuple[float, float]
    dir_perp: tuple[float, float]


def _add_vec(base: tuple[float, float], t: float, direction: tuple[float, float]) -> tuple[float, float]:
    return (base[0] + direction[0] * t, base[1] + direction[1] * t)


def frame_structure_geometry(
    frame_height: float,
    gradient_n: float,
    pitch: float,
    num_beams: int = FRAME_STRUCTURE_NUM_BEAMS,
    ground_extension: float | None = None,
) -> FrameStructureGeometry:
    """法枠1本の縦断面(法尻〜法肩、代表区間)の形状を計算する。

    実際の法長・段数には依存しない模式図: 断面正方形をnum_beams個、
    ピッチ間隔で並べた区間(法尻・法肩の水切りモルタル込み)を表す。
    """
    L = math.hypot(gradient_n, 1.0)
    dir_along = (gradient_n / L, 1.0 / L)
    dir_perp = (1.0 / L, -gradient_n / L)
    hyp = frame_height * L

    inner0 = (0.0, 0.0)
    outer0 = (hyp, 0.0)

    # 梁(断面正方形)は水切りモルタルの三角形の1辺(長さframe_height)を共用する
    # ように配置する(ユーザー指定・実測DXFで確認済み): 法尻側は梁の法尻側の面が
    # 水切りの「outer0-頂点」の辺に、法肩側は梁の法肩側の面が水切りの
    # 「inner1-頂点」の辺に、それぞれぴったり重なる(重複も隙間もない)。
    beam_start_t = frame_height * gradient_n + frame_height / 2
    total_length = frame_height * (gradient_n + 1.0) + pitch * (num_beams - 1)

    inner1 = _add_vec(inner0, total_length, dir_along)
    outer1 = _add_vec(outer0, total_length, dir_along)

    # 水切りモルタルの頂点(実測DXFで確認): 法尻側はinner0からdir_along方向に
    # frame_height*n進んだ点。法肩側はinner1からdir_perp方向にframe_height
    # 進んだ点(水平な辺はinner1-outer1そのもの、上の平地と同じ高さ)。
    toe_apex = _add_vec(inner0, frame_height * gradient_n, dir_along)
    crest_apex = _add_vec(inner1, frame_height, dir_perp)
    toe_wedge = [inner0, outer0, toe_apex, inner0]
    crest_wedge = [inner1, outer1, crest_apex, inner1]

    if ground_extension is None:
        ground_extension = hyp * 1.5
    toe_ground = ((outer0[0] - ground_extension, outer0[1]), outer0)
    crest_ground = (inner1, (inner1[0] + ground_extension, inner1[1]))

    half = frame_height / 2
    beam_start = _add_vec(_add_vec(inner0, beam_start_t, dir_along), half, dir_perp)
    beam_squares: list[list[tuple[float, float]]] = []
    beam_centers: list[tuple[float, float]] = []
    for i in range(num_beams):
        center = _add_vec(beam_start, i * pitch, dir_along)
        corners = []
        for sa, sp in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            corners.append(
                (
                    center[0] + sa * half * dir_along[0] + sp * half * dir_perp[0],
                    center[1] + sa * half * dir_along[1] + sp * half * dir_perp[1],
                )
            )
        corners.append(corners[0])
        beam_squares.append(corners)
        beam_centers.append(center)

    # 厚層基材のとき、最後の梁を除く各梁の法肩側の面(隣の梁との継ぎ目)にも
    # 水切りモルタルが必要(実測DXFで確認: 法尻・法肩の水切りと全く同じ構成
    # ―― 内側の縁の点から水平にhypだけ進んだ線 ―― を、梁の法肩側の面の
    # t座標を起点として描く)。最後の梁は法肩本体の水切りと重複するため不要。
    mid_wedge_lines: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for i in range(num_beams - 1):
        far_cap_t = beam_start_t + i * pitch + half
        a = _add_vec(inner0, far_cap_t, dir_along)
        b = (a[0] + hyp, a[1])
        mid_wedge_lines.append((a, b))

    return FrameStructureGeometry(
        inner_edge=(inner0, inner1),
        outer_edge=(outer0, outer1),
        toe_wedge=toe_wedge,
        crest_wedge=crest_wedge,
        toe_ground=toe_ground,
        crest_ground=crest_ground,
        beam_squares=beam_squares,
        beam_centers=beam_centers,
        mid_wedge_lines=mid_wedge_lines,
        dir_along=dir_along,
        dir_perp=dir_perp,
    )


@dataclass
class Leader:
    """引き出し線付きの注記(短い折れ線+文字)。"""

    line: tuple[tuple[float, float], tuple[float, float]]
    label: TextLabel


def _leader(anchor: tuple[float, float], direction: tuple[float, float], length: float, text: str, height: float) -> Leader:
    """anchorから斜め方向(direction、単位ベクトルでなくてもよい)に
    引き出し線を伸ばし、その先に水平な文字を置く。
    """
    dlen = math.hypot(*direction)
    ux, uy = (direction[0] / dlen, direction[1] / dlen) if dlen > 1e-9 else (1.0, 1.0)
    tip = (anchor[0] + ux * length, anchor[1] + uy * length)
    return Leader(line=(anchor, tip), label=TextLabel(text=text, position=tip, rotation=0.0, height=height))
