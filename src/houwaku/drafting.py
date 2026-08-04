"""自動製図: 部材の中心線から、実際の枠幅を持つ図形(左右/上下2本の線)を作る。

描画順序(2026-07-29、ユーザー指定):
1. 縦柱の中心線の両端を、上枠(すぐ上の部材)の下線・下枠(すぐ下の部材)の
   上線まで伸縮させる。ここでの「上枠・下枠」は周囲枠(内側面)のみを指す
   (横梁は縦柱をまたぐだけで、縦柱の長さには影響しない — 継続する縦の
   目地なので、途中の横梁との交差では分割しない)。
2. その中心線から左右に枠幅/2でオフセットした2本の線を作り、それぞれも
   同じ周囲枠(内側面)まで伸縮させる。
3. 横梁の中心線(曲がっていても1本の連続線として扱う)の両端を、左右の
   周囲枠(内側面)まで伸縮させる。
4. その中心線から上下に枠幅/2でオフセットした2本の線を作り、それぞれも
   同じ周囲枠(内側面)まで伸縮させる(曲がっている場合もオフセットは
   連続線として一括で行い、屈曲点での交差や隙間を防ぐ)。
5. 柱・梁とも、端部を閉じる線(キャップ)は描かない — 中心線+左右(上下)の
   2本、合計3本の独立した線分のまま。

伸縮の求め方: 部材の端点に一番近い周囲枠(内側面)の辺を選び、部材自身の
向き(実際の2点)と、その辺を含む無限直線との交点を新しい端点にする
(単純な最近点への投影だと、短い区間で不自然に折れ曲がることがあった)。

例外(2026-07-29追加): 測点(折れ点)がすでに周囲枠の内側面より内側に
入り込んでいる場合、その内側の短い区間をそのまま伸縮させようとすると
向きが破綻して折り返った形になる。この場合はその短い区間ごと無視し、
1つ内側の点を新しい自由端としてやり直す(_trim_end_dropping_short)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from .beams import Beam
from .perimeter import offset_centerline
from .posts import Post

Edge = tuple[tuple[float, float], tuple[float, float]]


@dataclass
class DrawnEdges:
    edge_a: list[tuple[float, float]]
    edge_b: list[tuple[float, float]]


def perimeter_outer_inner(polygon: Polygon, frame_width: float) -> tuple[Polygon, Polygon]:
    """周囲枠の外側(=真の境界線そのもの)・内側(=枠幅ぶん内側)を返す。"""
    inner = offset_centerline(polygon, frame_width)
    return polygon, inner


def count_post_crossings(beams: list[Beam], posts: list[Post]) -> list[int]:
    """各横梁について、交差する縦柱の本数を数える。"""
    post_lines = [LineString([p.start, p.end]) for p in posts]
    counts = []
    for b in beams:
        beam_line = LineString(b.points)
        counts.append(sum(1 for pl in post_lines if beam_line.intersects(pl)))
    return counts


def _polygon_edges(poly: Polygon) -> list[Edge]:
    coords = list(poly.exterior.coords)
    return list(zip(coords, coords[1:]))


def _point_to_segment_distance(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def _nearest_edge(point: tuple[float, float], edges: list[Edge]) -> Edge:
    return min(edges, key=lambda e: _point_to_segment_distance(point, e[0], e[1]))


def _line_intersection(
    p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float], p4: tuple[float, float]
) -> tuple[float, float] | None:
    """無限直線(p1-p2)と(p3-p4)の交点。平行ならNone。"""
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-9:
        return None
    a = x1 * y2 - y1 * x2
    b = x3 * y4 - y3 * x4
    px = (a * (x3 - x4) - (x1 - x2) * b) / denom
    py = (a * (y3 - y4) - (y1 - y2) * b) / denom
    return (px, py)


def _project_to_edge(point: tuple[float, float], edge: Edge) -> tuple[float, float]:
    """点を線分edge(の無限直線ではなく、線分そのもの)上の最寄り点に投影する。"""
    ax, ay = edge[0]
    bx, by = edge[1]
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-12:
        return edge[0]
    t = max(0.0, min(1.0, ((point[0] - ax) * dx + (point[1] - ay) * dy) / length_sq))
    return (ax + t * dx, ay + t * dy)


def _on_segment(point: tuple[float, float], edge: Edge, tol: float = 1e-6) -> bool:
    """point(無限直線との交点)が、edgeの無限直線上ではなく実際の線分の
    範囲内にあるかどうか。鋭角な角の近くでは、一番近い辺の無限直線を
    延長した先で交点が求まってしまうことがあり、その場合は辺の実際の
    端点(=角)を通り越した、境界の外側の点になってしまう。
    """
    ex, ey = edge[1][0] - edge[0][0], edge[1][1] - edge[0][1]
    length_sq = ex * ex + ey * ey
    if length_sq < 1e-12:
        return False
    s = ((point[0] - edge[0][0]) * ex + (point[1] - edge[0][1]) * ey) / length_sq
    return -tol <= s <= 1 + tol


def _trim_end_dropping_short(
    points: list[tuple[float, float]], edges: list[Edge], from_start: bool
) -> list[tuple[float, float]]:
    """部材の片端を、一番近い周囲枠の辺までの直線交点に合わせる。

    aが自由端、bがその内側の隣接点。無限直線同士の交点は、まれにb側
    (内側)を通り越して反対側に出てしまうことがある — 典型的には測点
    (折れ点)がすでに周囲枠の内側面より内側に入り込んでいて、そちら側の
    短い区間がそもそも成立しないケース。そのときはこの短い区間ごと
    無視し(内側の次の点を新しい自由端として)やり直す。1区間しか
    残っていない場合だけ、より安全な最近点への投影にフォールバックする。

    もう一つの落とし穴(鋭角な角付近、一番近い辺が必ずしも正しい辺とは
    限らない): 自由端が境界の角(2辺の共有頂点)のすぐそばにあると、
    「距離が一番近い辺」がその角を挟んだ2辺のうち間違った方になることが
    ある——真に交わるべき辺は、角のせいでほぼ同じ距離だが、無限直線の
    交点が辺の範囲外(角を通り越した位置)に出てしまい_on_segmentがFalse
    になる。この場合、一番近い辺だけで諦めず(それだと区間ごと丸ごと
    無視してしまい、部材の大部分を失いかねない)、全辺を近い順に試して、
    実際に有効な交点(辺の範囲内、かつb側を通り越さない)を返す最初の
    辺を採用する。全辺を試しても見つからない場合だけ、この区間を無視して
    次の点からやり直す(1区間しか残っていなければ最近点への投影に
    フォールバックする)。
    """
    pts = list(points)
    while len(pts) >= 2:
        if not edges:
            return pts
        a, b = (pts[0], pts[1]) if from_start else (pts[-1], pts[-2])
        abx, aby = a[0] - b[0], a[1] - b[1]
        denom = abx * abx + aby * aby
        for edge in sorted(edges, key=lambda e: _point_to_segment_distance(a, e[0], e[1])):
            inter = _line_intersection(a, b, edge[0], edge[1])
            if inter is None or not _on_segment(inter, edge) or denom <= 1e-12:
                continue
            t = ((inter[0] - b[0]) * abx + (inter[1] - b[1]) * aby) / denom
            if t >= -1e-6:
                return [inter, *pts[1:]] if from_start else [*pts[:-1], inter]
        if len(pts) > 2:
            pts = pts[1:] if from_start else pts[:-1]
            continue
        edge = _nearest_edge(a, edges)
        proj = _project_to_edge(a, edge)
        return [proj, pts[1]] if from_start else [pts[0], proj]
    return pts


def _trim_polyline_ends(points: list[tuple[float, float]], edges: list[Edge]) -> list[tuple[float, float]]:
    points = _trim_end_dropping_short(points, edges, from_start=True)
    points = _trim_end_dropping_short(points, edges, from_start=False)
    return points


def _clip_to_polygon(
    points: list[tuple[float, float]], polygon: Polygon
) -> list[tuple[float, float]] | None:
    """折れ線をポリゴンとの幾何交差(shapelyのintersection)でクリップする。

    _trim_end_dropping_short(自由端に一番近い辺を探す方式)は、境界に短い
    辺が連続する区間と部材がほぼ並行に走る場合、有効な交点を見つけられず
    部材のほとんどを切り捨ててしまうことがある(CollapsedMemberWarning)。
    shapelyの交差判定は個々の辺への近さではなく線分同士の実際の交わりを
    総当たりで計算するため、この種の壊れ方をしない。ユーザーが復帰させた
    部材(skip_trim_keys)にはこちらを使う。
    """
    line = LineString(points)
    clipped = line.intersection(polygon)
    if clipped.is_empty:
        return None
    if clipped.geom_type == "LineString":
        return list(clipped.coords)
    if clipped.geom_type == "MultiLineString":
        longest = max(clipped.geoms, key=lambda g: g.length)
        return list(longest.coords)
    return None


def _offset_polyline(points: list[tuple[float, float]], distance: float) -> list[tuple[float, float]] | None:
    """折れ線を1本の連続線として距離distanceだけ平行移動する(屈曲点も正しく処理)。"""
    line = LineString(points)
    offset = line.offset_curve(distance, join_style="mitre", mitre_limit=10)
    if offset.is_empty:
        return None
    if offset.geom_type != "LineString":
        candidates = list(getattr(offset, "geoms", []))
        if not candidates:
            return None
        offset = max(candidates, key=lambda g: g.length)
    coords = list(offset.coords)
    if len(coords) < 2:
        return None
    d_start = math.hypot(coords[0][0] - points[0][0], coords[0][1] - points[0][1])
    d_end = math.hypot(coords[-1][0] - points[0][0], coords[-1][1] - points[0][1])
    if d_end < d_start:
        coords = list(reversed(coords))
    return coords


def _shared_endpoint_counts(beams: list[Beam], ndigits: int = 4) -> dict[tuple[float, float], int]:
    """全横梁の端点(始点・終点)をまとめて数える。

    通常、測点(折れ点)で分かれて描かれた左右の横梁は
    centerline_import.beams_from_layer側で既に1本の折れ線として結合済み
    のはずだが、3本以上が同じ点に集まっていて結合できなかった場合に備え、
    ここでも念のため共有端点(内部の繋ぎ目)を区別してクリップ対象から
    外す(念のための保険)。
    """
    counts: dict[tuple[float, float], int] = {}
    for b in beams:
        for p in (b.points[0], b.points[-1]):
            key = (round(p[0], ndigits), round(p[1], ndigits))
            counts[key] = counts.get(key, 0) + 1
    return counts


@dataclass
class SmallPanelWarning:
    """段の隅に生じた小さい半端枠についての警告。自動修正はせず、
    割付図(CAD)側での手動修正を促すだけ。"""

    tier_index: int  # 下から数えて1始まり(tiersは既にcentroid.y昇順=下から順)
    label: str  # 何と何の組み合わせの半端か
    total: float
    point_a: tuple[float, float]  # 半端の原因になった縁の代表点(1つめ)
    point_b: tuple[float, float]  # 半端の原因になった縁の代表点(2つめ)

    def message(self) -> str:
        return (
            f"下から{self.tier_index}段目: {self.label}の半端が合計{self.total:.2f}mです。"
            "小さい枠になっています。割付図を確認し、手動で調整してください。"
        )


def _midpoint(points: list[tuple[float, float]]) -> tuple[float, float]:
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def _post_point(p: Post) -> tuple[float, float]:
    return _midpoint([p.start, p.end])


def _post_edge_points(p: Post, half: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """柱の中心線を実際に枠幅/2でオフセットした2本の縁の、代表点(左の縁, 右の縁)。

    製図(prepare_drawn_members)と同じ_offset_polylineでオフセットしてから
    判定することで、中心線からの単純な引き算(半幅を差し引くだけ)では
    ずれてしまう、柱がわずかに斜めなケースでも正しく判定できる。
    """
    pts = [p.start, p.end]
    edge_plus = _offset_polyline(pts, half) or pts
    edge_minus = _offset_polyline(pts, -half) or pts
    point_plus, point_minus = _midpoint(edge_plus), _midpoint(edge_minus)
    return (point_minus, point_plus) if point_plus[0] >= point_minus[0] else (point_plus, point_minus)


def _beam_edge_points(b: Beam, half: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """梁の中心線を実際に枠幅/2でオフセットした2本の縁の、代表点(下の縁, 上の縁)。"""
    pts = b.points
    edge_plus = _offset_polyline(pts, half) or pts
    edge_minus = _offset_polyline(pts, -half) or pts
    point_plus, point_minus = _midpoint(edge_plus), _midpoint(edge_minus)
    return (point_minus, point_plus) if point_plus[1] >= point_minus[1] else (point_plus, point_minus)


def _boundary_crossings_at_y(poly: Polygon, y: float) -> list[float]:
    """polygonの境界を、高さyの水平線で切ったときの交点のx座標一覧(昇順)。

    三角形(段の頂点)や台形など、辺の向きが縦横どちらとも言い切れない
    多角形でも常に正しく動くよう、辺の傾きで縦/横に分類する方式はやめて
    「その高さでの実際の交点」を直接求める(水平線レイキャスト)。
    """
    xs: list[float] = []
    coords = list(poly.exterior.coords)
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        dy = y2 - y1
        if abs(dy) < 1e-9:
            continue
        lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
        if lo <= y <= hi:
            t = (y - y1) / dy
            xs.append(x1 + t * (x2 - x1))
    return sorted(xs)


def _boundary_crossings_at_x(poly: Polygon, x: float) -> list[float]:
    """polygonの境界を、x座標xの鉛直線で切ったときの交点のy座標一覧(昇順)。"""
    ys: list[float] = []
    coords = list(poly.exterior.coords)
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        dx = x2 - x1
        if abs(dx) < 1e-9:
            continue
        lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
        if lo <= x <= hi:
            t = (x - x1) / dx
            ys.append(y1 + t * (y2 - y1))
    return sorted(ys)


def _horizontal_gaps_to_boundary(poly: Polygon, point: tuple[float, float]) -> tuple[float, float]:
    """pointから、polyの境界までの左右それぞれの水平距離(gap_left, gap_right)。

    その高さでの水平方向の交点が片側に見つからない場合(鋭角な頂点付近で、
    オフセットした縁の点がその高さの多角形の外にはみ出してしまうケース)は、
    最寄りの境界点までのユークリッド距離で代用する。ここで単純にNoneを返して
    その部材を判定から除外すると、まさに一番隅(=一番厳しい)の部材が
    抜け落ちてしまう。
    """
    px, py = point
    xs = _boundary_crossings_at_y(poly, py)
    left_xs = [x for x in xs if x <= px]
    right_xs = [x for x in xs if x >= px]
    fallback = None
    if not left_xs or not right_xs:
        fallback = poly.exterior.distance(Point(point))
    gap_left = (px - max(left_xs)) if left_xs else fallback
    gap_right = (min(right_xs) - px) if right_xs else fallback
    return gap_left, gap_right


def _vertical_gaps_to_boundary(poly: Polygon, point: tuple[float, float]) -> tuple[float, float]:
    """pointから、polyの境界までの上下それぞれの鉛直距離(gap_bottom, gap_top)。

    _horizontal_gaps_to_boundaryと同様、鋭角な頂点付近で片側の交点が
    見つからない場合は最寄りの境界点までのユークリッド距離で代用する。
    """
    px, py = point
    ys = _boundary_crossings_at_x(poly, px)
    below_ys = [y for y in ys if y <= py]
    above_ys = [y for y in ys if y >= py]
    fallback = None
    if not below_ys or not above_ys:
        fallback = poly.exterior.distance(Point(point))
    gap_bottom = (py - max(below_ys)) if below_ys else fallback
    gap_top = (min(above_ys) - py) if above_ys else fallback
    return gap_bottom, gap_top


def _nearest_tier_by_bbox(
    point: tuple[float, float], bounds: list[tuple[float, float, float, float]]
) -> int | None:
    """pointの(x,y)が両方とも収まっている段の中で、中心に一番近いものを選ぶ。

    段のbounding boxは斜めの境界を持つ多角形にとっては実際の形より広いので、
    x方向・y方向どちらか片方だけの包含では、別の段の部材を誤って拾って
    しまうことがある(段同士の見た目のx範囲・y範囲が重なる場合)。両方の
    包含を要求することでこれを防ぐ。
    """
    px, py = point
    candidates = [i for i, b in enumerate(bounds) if b[0] <= px <= b[2] and b[1] <= py <= b[3]]
    if not candidates:
        return None
    def _center_dist(i: int) -> float:
        b = bounds[i]
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        return math.hypot(px - cx, py - cy)
    return min(candidates, key=_center_dist)


def group_posts_by_tier(tiers: list[Polygon], frame_width: float, posts: list[Post]) -> list[list[Post]]:
    """各縦柱を、中点の(x,y)が収まる段に割り当てる。"""
    inner_polys = [perimeter_outer_inner(t, frame_width)[1] for t in tiers]
    bounds = [inner.bounds for inner in inner_polys]
    groups: list[list[Post]] = [[] for _ in tiers]
    for p in posts:
        best = _nearest_tier_by_bbox(_post_point(p), bounds)
        if best is not None:
            groups[best].append(p)
    return groups


def group_beams_by_tier(tiers: list[Polygon], frame_width: float, beams: list[Beam]) -> list[list[Beam]]:
    """各横梁を、中点の(x,y)が収まる段に割り当てる。"""
    inner_polys = [perimeter_outer_inner(t, frame_width)[1] for t in tiers]
    bounds = [inner.bounds for inner in inner_polys]
    groups: list[list[Beam]] = [[] for _ in tiers]
    for b in beams:
        best = _nearest_tier_by_bbox(_midpoint(b.points), bounds)
        if best is not None:
            groups[best].append(b)
    return groups


def find_small_panel_warnings(
    tiers: list[Polygon],
    frame_width: float,
    pitch: float,
    beams: list[Beam],
    posts: list[Post],
) -> list[SmallPanelWarning]:
    """段ごとに、隅にできる小さい半端枠を検出し、警告を作る(自動修正はしない)。

    半端の合計(=正味の有効スパンの下限、中心間距離-枠幅)以下になった
    場合に警告する。判定は以下の4通り:
    - 縦柱の左右(左マージン+右マージン)
    - 横梁の上下(上マージン+下マージン)
    - 対角(縦柱の右マージン+横梁の下マージン)
    - 対角(縦柱の左マージン+横梁の上マージン)
    どちらか片方が既にほぼ0(=既にフラッシュで、単に1つの正常なバイ)の
    場合は警告しない(2つとも小さい半端があるときだけ問題にする)。
    """
    threshold = pitch - frame_width
    already_flush_tolerance = 0.01
    half = frame_width / 2
    warnings: list[SmallPanelWarning] = []
    if threshold <= 0:
        return warnings

    inner_polys = [perimeter_outer_inner(t, frame_width)[1] for t in tiers]
    post_groups = group_posts_by_tier(tiers, frame_width, posts)
    beam_groups = group_beams_by_tier(tiers, frame_width, beams)

    def _check(
        i: int,
        a: tuple[float, tuple[float, float]] | None,
        b: tuple[float, tuple[float, float]] | None,
        label: str,
    ) -> None:
        if a is None or b is None:
            return
        margin_a, point_a = a
        margin_b, point_b = b
        if margin_a < 0 or margin_b < 0:
            return
        if margin_a <= already_flush_tolerance or margin_b <= already_flush_tolerance:
            return
        total = margin_a + margin_b
        if total <= threshold:
            warnings.append(SmallPanelWarning(i + 1, label, total, point_a, point_b))

    last_index = len(inner_polys) - 1
    for i, inner in enumerate(inner_polys):
        # 小さい半端枠は、一番下(法尻)と一番上(法肩)の段だけ提案する
        # (途中の段まで全部出すと数が多すぎるため、ユーザー指定)。
        if i != 0 and i != last_index:
            continue
        left = right = top = bottom = None  # 各(margin, 代表点)のうち最小のもの

        # 境界が斜めの場合(三角形の頂上段など)、「x座標が一番小さい柱」が
        # 必ずしも境界に一番近い柱とは限らない。全ての柱それぞれについて
        # その高さでの実際の左右の境界までの距離を求め、最小値を使う。
        #
        # 判定は中心線からの単純な引き算(半幅を差し引くだけ)ではなく、
        # 実際に枠幅/2でオフセットした縁(_post_edge_points、製図と同じ
        # _offset_polylineを使用)を求めてから、その縁から境界までの距離
        # をレイキャストする。柱がわずかに斜めのときも正しく判定できる。
        tier_posts = post_groups[i]
        if tier_posts:
            left_candidates: list[tuple[float, tuple[float, float]]] = []
            right_candidates: list[tuple[float, tuple[float, float]]] = []
            for p in tier_posts:
                left_point, right_point = _post_edge_points(p, half)
                gl, _ = _horizontal_gaps_to_boundary(inner, left_point)
                _, gr = _horizontal_gaps_to_boundary(inner, right_point)
                if gl is not None:
                    left_candidates.append((gl, left_point))
                if gr is not None:
                    right_candidates.append((gr, right_point))
            if left_candidates:
                left = min(left_candidates, key=lambda t: t[0])
            if right_candidates:
                right = min(right_candidates, key=lambda t: t[0])

        tier_beams = beam_groups[i]
        if tier_beams:
            bottom_candidates: list[tuple[float, tuple[float, float]]] = []
            top_candidates: list[tuple[float, tuple[float, float]]] = []
            for b in tier_beams:
                bottom_point, top_point = _beam_edge_points(b, half)
                gb, _ = _vertical_gaps_to_boundary(inner, bottom_point)
                _, gt = _vertical_gaps_to_boundary(inner, top_point)
                if gb is not None:
                    bottom_candidates.append((gb, bottom_point))
                if gt is not None:
                    top_candidates.append((gt, top_point))
            if bottom_candidates:
                bottom = min(bottom_candidates, key=lambda t: t[0])
            if top_candidates:
                top = min(top_candidates, key=lambda t: t[0])

        _check(i, left, right, "縦柱の左右")
        _check(i, top, bottom, "横梁の上下")
        _check(i, right, bottom, "縦柱右+横梁下(対角)")
        _check(i, left, top, "縦柱左+横梁上(対角)")

    return warnings


@dataclass
class LargeSpacingWarning:
    """隣り合う部材(縦柱どうし・横梁どうし)の間の型枠区間(部材の縁どうしの
    距離)が、規定の型枠長(pitch - frame_width。300*300*2000規格なら
    2000-300=1700mm)を超えている場合の警告。型枠は固定長で作られるため、
    これより長い区間は1本の型枠では対応できない。自動修正はせず、割付図
    (CAD)側での手動修正を促すだけ(SmallPanelWarningと同じ方針)。"""

    tier_index: int  # 下から数えて1始まり
    label: str  # "縦柱の間隔" / "横梁の間隔"
    net_span: float  # 実際の型枠区間長(部材の縁どうしの距離 = 中心間距離-frame_width)
    form_limit: float  # 規定の型枠長(pitch - frame_width)
    point_a: tuple[float, float]
    point_b: tuple[float, float]

    def message(self) -> str:
        return (
            f"下から{self.tier_index}段目: {self.label}の型枠区間が{self.net_span:.2f}mあり、"
            f"規定の型枠長{self.form_limit:.2f}mを超えています。"
            "1本の型枠では対応できません。割付図を確認し、手動で調整してください。"
        )


def _intersection_point(line_a: LineString, line_b: LineString) -> tuple[float, float] | None:
    """2本の線の交点(複数交点や重なりの場合は代表点)を返す。交わらなければNone。"""
    inter = line_a.intersection(line_b)
    if inter.is_empty:
        return None
    c = inter.centroid
    return (c.x, c.y)


def _beam_x_range(b: Beam) -> tuple[float, float]:
    xs = [x for x, _y in b.points]
    return (min(xs), max(xs))


def _member_edge_crossings(
    member_points: list[tuple[float, float]], other_line: LineString, half: float
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """member(縦柱or横梁)の中心線をhalfだけ両側にオフセットした2本の縁が、
    other_line(交差する相手の中心線)とそれぞれどこで交わるかを求める。

    中心線どうしの距離からframe_widthを引く単純な近似は、部材が傾いて
    交わる場合に実際の型枠の縁どうしの間隔とズレる(実際より長く出ること
    がある)。縁を実際にオフセットしてから交点を求めることで、傾きの
    影響を正しく反映する。戻り値は(x座標が小さい方の縁の交点, 大きい方)。
    """
    points = []
    for sign in (1, -1):
        offset = _offset_polyline(member_points, sign * half)
        if offset is None:
            continue
        pt = _intersection_point(LineString(offset), other_line)
        if pt is not None:
            points.append(pt)
    if len(points) < 2:
        return None
    points.sort(key=lambda p: p[0])
    return points[0], points[1]


def _range_overlap_groups(ranges: list[tuple[float, float]]) -> list[list[int]]:
    """区間(lo, hi)どうしが半分以上重なるもの同士でグループ化し、各グループの
    元のインデックス一覧を返す(横梁の"同じ列"の判定に使う)。
    """
    order = sorted(range(len(ranges)), key=lambda i: ranges[i][0])
    groups: list[list[int]] = []
    group_ranges: list[tuple[float, float]] = []
    for idx in order:
        lo, hi = ranges[idx]
        placed = False
        for gi, (glo, ghi) in enumerate(group_ranges):
            overlap = min(hi, ghi) - max(lo, glo)
            span = min(hi - lo, ghi - glo)
            if span <= 1e-9:
                continue
            if overlap / span > 0.5:
                groups[gi].append(idx)
                group_ranges[gi] = (min(glo, lo), max(ghi, hi))
                placed = True
                break
        if not placed:
            groups.append([idx])
            group_ranges.append((lo, hi))
    return groups


def find_large_spacing_warnings(
    tiers: list[Polygon],
    frame_width: float,
    pitch: float,
    beams: list[Beam],
    posts: list[Post],
    tolerance: float = 0.01,
) -> list[LargeSpacingWarning]:
    """段ごとに、隣り合う部材どうしの型枠区間(縁どうしの距離)が、規定の
    型枠長(pitch - frame_width)を超えていないか調べる(find_small_panel_
    warningsの逆: 半端が小さすぎる場合ではなく、型枠が対応できないほど
    区間が広すぎる場合を検出する)。

    縦柱の間隔は、格子の交点(縦柱の中心線と横梁の中心線が実際に交差する
    点)どうしを比較する。縦柱自身の中心点どうしを比べるのではなく、必ず
    「同じ横梁を実際に横切る縦柱どうし」の交点だけを比較する(横切らない=
    そもそも同じ並びの部材ではないので、無関係なもの同士を比較してしまう
    のを防ぐ)。

    横梁の間隔は、横梁自身の中心点どうしを直接比較する(縦柱の交点は経由
    しない — 縦柱がわずかに傾いていると、その傾いた縦柱に沿って測った
    交点間の直線距離が実際の梁間隔より長くなってしまうため)。x方向の
    範囲が重なるもの同士(同じ列)でグループ化してから、その中だけで隣
    同士を比較する。
    """
    form_limit = pitch - frame_width
    warnings: list[LargeSpacingWarning] = []
    if form_limit <= 0:
        return warnings

    post_groups = group_posts_by_tier(tiers, frame_width, posts)
    beam_groups = group_beams_by_tier(tiers, frame_width, beams)

    half = frame_width / 2
    for i, (tier_beams, tier_posts) in enumerate(zip(beam_groups, post_groups)):
        post_lines = [LineString([p.start, p.end]) for p in tier_posts]

        # 縦柱の間隔: 各横梁を実際に横切る縦柱との交点を、その横梁上でx順に
        # 並べて隣同士を比較する。縦柱が傾いていても正しく判定できるよう、
        # 中心線の交点ではなく、縦柱を実際にオフセットした縁どうしの交点
        # (=型枠同士の交点)を使う。
        for beam in tier_beams:
            beam_line = LineString(beam.points)
            entries: list[tuple[float, tuple[tuple[float, float], tuple[float, float]]]] = []
            for post, post_line in zip(tier_posts, post_lines):
                c = _intersection_point(beam_line, post_line)
                if c is None:
                    continue
                edges = _member_edge_crossings([post.start, post.end], beam_line, half)
                if edges is None:
                    continue
                entries.append((c[0], edges))
            entries.sort(key=lambda e: e[0])
            for (_cxa, edges_a), (_cxb, edges_b) in zip(entries, entries[1:]):
                right_edge_a = edges_a[1]
                left_edge_b = edges_b[0]
                net_span = left_edge_b[0] - right_edge_a[0]
                if net_span > form_limit + tolerance:
                    warnings.append(
                        LargeSpacingWarning(i + 1, "縦柱の間隔", net_span, form_limit, right_edge_a, left_edge_b)
                    )

        # 横梁の間隔: 横梁自身の高さ(y座標)を直接比較する(縦柱の交点は経由
        # しない — 縦柱がわずかに傾いていると、その傾いた縦柱に沿って測った
        # 交点間の直線距離が実際の梁間隔より長くなってしまうため)。また、
        # 中心点どうしのユークリッド距離も使わない(梁ごとに長さが違うと、
        # 中心点のx座標がずれて、無関係な横方向のズレまで距離に乗ってしまう
        # ため)。純粋にy座標の差だけを型枠区間の長さとする。x方向の範囲が
        # 重なるもの同士(同じ列)でグループ化してから、その中だけで隣同士
        # を比較する。
        for col_idx in _range_overlap_groups([_beam_x_range(b) for b in tier_beams]):
            col = sorted((tier_beams[j] for j in col_idx), key=lambda b: _midpoint(b.points)[1])
            for a, b in zip(col, col[1:]):
                pa, pb = _midpoint(a.points), _midpoint(b.points)
                net_span = abs(pb[1] - pa[1]) - frame_width
                if net_span > form_limit + tolerance:
                    warnings.append(LargeSpacingWarning(i + 1, "横梁の間隔", net_span, form_limit, pa, pb))

    return warnings


def member_key(a: tuple[float, float], b: tuple[float, float], ndigits: int = 4) -> tuple:
    """部材(横梁・縦柱)の元の中心線の両端点から、再現可能な同一性キーを作る。

    Beam/Postオブジェクトは読み込むたびに新しく作り直されるので、
    「クリップをスキップして復帰させたい部材」をセッションをまたいで
    覚えておくには、オブジェクトのidではなく値ベースのキーが要る。
    """
    return (round(a[0], ndigits), round(a[1], ndigits), round(b[0], ndigits), round(b[1], ndigits))


def prepare_drawn_members(
    tiers: list[Polygon],
    frame_width: float,
    beams: list[Beam],
    posts: list[Post],
    skip_trim_keys: set[tuple] | None = None,
) -> tuple[list[Beam], list[Post], list[int], list[DrawnEdges], list[DrawnEdges]]:
    """横梁・縦柱の中心線の端部を周囲枠の内側面に合わせ、左右(上下)の
    実寸2本線も同じ面まで伸縮させる。閉じるキャップ線は作らない。

    skip_trim_keys: member_key()が一致する部材は、_trim_end_dropping_short
    (自由端に一番近い辺を探す方式)を使わず、代わりに_clip_to_polygon
    (shapelyの幾何交差)でクリップする。境界に短い辺が連続する区間と
    部材がほぼ並行に走る場合、前者は有効な交点を見つけられず部材の
    ほとんどを切り捨ててしまう(CollapsedMemberWarning)。ユーザーがGUI上で
    その部材を選び「元の中心線のまま復帰する」を選んだ場合に使う
    ——「クリップしない」のではなく、より頑健な方式でクリップし直す。

    戻り値: (端部補正後の横梁, 端部補正後の縦柱, 各横梁と交差する縦柱の本数,
             横梁の上下2本線, 縦柱の左右2本線)。
    """
    skip_trim_keys = skip_trim_keys or set()
    inner_polys = [perimeter_outer_inner(t, frame_width)[1] for t in tiers]
    perim_edges = [e for poly in inner_polys for e in _polygon_edges(poly)]
    inner_union = unary_union(inner_polys) if inner_polys else None
    shared_counts = _shared_endpoint_counts(beams)

    def _is_shared(p: tuple[float, float]) -> bool:
        return shared_counts.get((round(p[0], 4), round(p[1], 4)), 0) > 1

    def _clip_or_keep(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if inner_union is None:
            return points
        clipped = _clip_to_polygon(points, inner_union)
        return clipped if clipped is not None else points

    half = frame_width / 2

    clipped_beams: list[Beam] = []
    beam_edges: list[DrawnEdges] = []
    for b in beams:
        pts = b.points
        skip = member_key(pts[0], pts[-1]) in skip_trim_keys
        if skip:
            pts = _clip_or_keep(pts)
        elif perim_edges:
            if not _is_shared(pts[0]):
                pts = _trim_end_dropping_short(pts, perim_edges, from_start=True)
            if not _is_shared(pts[-1]):
                pts = _trim_end_dropping_short(pts, perim_edges, from_start=False)
        clipped_beams.append(Beam(points=pts))

        top = _offset_polyline(pts, half)
        bottom = _offset_polyline(pts, -half)
        if skip:
            top = _clip_or_keep(top) if top is not None else top
            bottom = _clip_or_keep(bottom) if bottom is not None else bottom
        else:
            if top is not None and perim_edges:
                top = _trim_polyline_ends(top, perim_edges)
            if bottom is not None and perim_edges:
                bottom = _trim_polyline_ends(bottom, perim_edges)
        beam_edges.append(DrawnEdges(edge_a=top or pts, edge_b=bottom or pts))

    clipped_posts: list[Post] = []
    post_edges: list[DrawnEdges] = []
    for p in posts:
        pts = [p.start, p.end]
        skip = member_key(p.start, p.end) in skip_trim_keys
        if skip:
            pts = _clip_or_keep(pts)
        elif perim_edges:
            pts = _trim_end_dropping_short(pts, perim_edges, from_start=True)
            pts = _trim_end_dropping_short(pts, perim_edges, from_start=False)
        clipped_posts.append(Post(start=pts[0], end=pts[1]))

        left = _offset_polyline(pts, half)
        right = _offset_polyline(pts, -half)
        if skip:
            left = _clip_or_keep(left) if left is not None else left
            right = _clip_or_keep(right) if right is not None else right
        elif perim_edges:
            if left is not None:
                left = _trim_polyline_ends(left, perim_edges)
            if right is not None:
                right = _trim_polyline_ends(right, perim_edges)
        post_edges.append(DrawnEdges(edge_a=left or pts, edge_b=right or pts))

    crossing_counts = count_post_crossings(clipped_beams, clipped_posts)

    return clipped_beams, clipped_posts, crossing_counts, beam_edges, post_edges


@dataclass
class CollapsedMemberWarning:
    """横梁・縦柱の中心線が、周囲枠へのクリップ処理で不自然に短くなり、
    実質的に消失していないかの警告。

    境界に短い辺が連続する区間とほぼ並行に走る部材では、
    `_trim_end_dropping_short`の「自由端に一番近い辺」という探索が
    有効な交点を見つけられず、中心線のほとんどを切り捨ててしまうことが
    ある(トリム前は数十m単位でも、トリム後は数cmに潰れる)。自動修正は
    せず、割付図側での確認を促すだけ(SmallPanelWarning等と同じ方針)。
    """

    tier_index: int  # 下から数えて1始まり(所属する段が特定できなければ0)
    label: str  # "横梁" / "縦柱"
    original_length: float  # クリップ前の中心線の長さ(m)
    clipped_length: float  # クリップ後の長さ(m)
    point_a: tuple[float, float]  # クリップ前の中心線の一方の端点
    point_b: tuple[float, float]  # クリップ前の中心線のもう一方の端点

    def message(self) -> str:
        return (
            f"下から{self.tier_index}段目: {self.label}の中心線が、境界へのクリップ処理で"
            f"{self.original_length:.2f}mから{self.clipped_length:.2f}mに縮んでいます。"
            "境界との交点探索に失敗し、実質的に消失している可能性があります。"
            "割付図を確認してください。"
        )


COLLAPSE_LENGTH_RATIO = 0.1  # トリム後の長さが元の長さのこの割合未満なら「消失」とみなす
COLLAPSE_MIN_ORIGINAL_LENGTH = 1.0  # 元の長さがこれ未満の部材はそもそも短いので対象外


def find_collapsed_member_warnings(
    tiers: list[Polygon],
    frame_width: float,
    beams: list[Beam],
    posts: list[Post],
    skip_trim_keys: set[tuple] | None = None,
) -> list[CollapsedMemberWarning]:
    """横梁・縦柱それぞれ、クリップ前後の長さを比較し、不自然な消失を検出する。

    skip_trim_keysで復帰済みとされた部材は、クリップされずそのまま使われる
    ので当然縮まない(=警告も出ない)。prepare_drawn_membersと同じ引数を渡す。
    """
    clipped_beams, clipped_posts, _, _, _ = prepare_drawn_members(
        tiers, frame_width, beams, posts, skip_trim_keys
    )

    beam_tier = {
        id(b): i + 1
        for i, group in enumerate(group_beams_by_tier(tiers, frame_width, beams))
        for b in group
    }
    post_tier = {
        id(p): i + 1
        for i, group in enumerate(group_posts_by_tier(tiers, frame_width, posts))
        for p in group
    }

    warnings: list[CollapsedMemberWarning] = []
    for original, clipped in zip(beams, clipped_beams):
        original_length = LineString(original.points).length
        if original_length < COLLAPSE_MIN_ORIGINAL_LENGTH:
            continue
        clipped_length = LineString(clipped.points).length
        if clipped_length < original_length * COLLAPSE_LENGTH_RATIO:
            warnings.append(CollapsedMemberWarning(
                beam_tier.get(id(original), 0), "横梁", original_length, clipped_length,
                original.points[0], original.points[-1],
            ))
    for original, clipped in zip(posts, clipped_posts):
        original_length = math.hypot(original.end[0] - original.start[0], original.end[1] - original.start[1])
        if original_length < COLLAPSE_MIN_ORIGINAL_LENGTH:
            continue
        clipped_length = math.hypot(clipped.end[0] - clipped.start[0], clipped.end[1] - clipped.start[1])
        if clipped_length < original_length * COLLAPSE_LENGTH_RATIO:
            warnings.append(CollapsedMemberWarning(
                post_tier.get(id(original), 0), "縦柱", original_length, clipped_length,
                original.start, original.end,
            ))
    return warnings
