"""一般形状(多段・先すぼまり等)対応の法枠割付ロジック。

法面02.dxf / AC.dxf の実例から読み取った規則:
  - 枠は断面 frame_width x frame_height。展開図上は2本線(幅300mm)で表現。
  - 横梁・縦柱とも、内法(クリアスパン) nominal_gap を標準とする。
  - 横梁: 各段の最下点を基準に上へ配置。上下端の余りが min_segment 未満に
    なる場合は、余りを両端に均等に振り分けて極端に小さい枠を避ける。
  - 縦柱: 段をまたいで同じX位置になるよう、全段を通して総延長が最小になる
    開始位置を frame_width 刻みで探索する。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import LineString, Polygon

from .boundary import SlopeShape, Tier


@dataclass
class FrameRule:
    frame_width: float = 0.3  # 枠幅 (m)
    frame_height: float = 0.3  # 枠高さ (m)
    nominal_gap: float = 2.0  # 標準内法(クリアスパン) (m)
    min_segment: float = 0.6  # 端に許容する最小スパン (m)
    search_step: float = 0.3  # 縦柱開始位置の探索刻み (m)

    @property
    def pitch(self) -> float:
        return self.frame_width + self.nominal_gap


@dataclass
class BeamLayout:
    """1段分の横梁配置結果(センターライン位置のリスト、法尻から法肩の順)。"""

    positions: list[float]


@dataclass
class PostLayout:
    """縦柱配置結果(センターラインX位置のリストと、探索で得られた総延長)。"""

    positions: list[float]
    total_length: float
    offset: float


def _offset_inward(polygon: Polygon, distance: float) -> Polygon:
    result = polygon.buffer(-distance, join_style="mitre", mitre_limit=10)
    if result.is_empty:
        raise ValueError("オフセット量が大きすぎて形状が消失しました")
    if result.geom_type != "Polygon":
        # マルチポリゴンになった場合は最大面積のものを使う
        result = max(result.geoms, key=lambda g: g.area)
    return result


def _split_remainder(total_span: float, step: float, min_segment: float) -> tuple[int, float]:
    """total_span を step 間隔で分割する span 数 n と、両端に振り分ける余り端数を返す。

    n+1 本の位置(0 起点)を [edge_extra, edge_extra+step, ..., edge_extra+n*step]
    とすると、そのうち上下端の余白がそれぞれ edge_extra, total_span-(edge_extra+n*step)
    となる。両端が min_segment 未満にならないよう n を調整する。
    """
    if total_span <= 0:
        return 0, 0.0
    n = math.floor(total_span / step)
    remainder = total_span - n * step
    while n > 0 and remainder < min_segment:
        n -= 1
        remainder = total_span - n * step
    edge_extra = remainder / 2
    return n, edge_extra


def layout_horizontal_beams(tier: Tier, rule: FrameRule) -> BeamLayout:
    """1段分の横梁(周囲枠を除く内部の追加横梁)のセンター位置を求める。

    周囲枠(perimeter)は真の境界からframe_width分内側のラインを内縁とする
    別部材として扱うため、内部の横梁はそこからさらにnominal_gap離れた
    位置から始まる。
    """
    inner = _offset_inward(tier.polygon, rule.frame_width)
    _, inner_miny, _, inner_maxy = inner.bounds
    usable_span = (inner_maxy - inner_miny) - 2 * rule.nominal_gap - rule.frame_width
    if usable_span <= 0:
        return BeamLayout(positions=[])
    n, edge_extra = _split_remainder(usable_span, rule.pitch, rule.min_segment)
    start = inner_miny + rule.nominal_gap + rule.frame_width / 2 + edge_extra
    positions = [start + i * rule.pitch for i in range(n + 1)]
    return BeamLayout(positions=positions)


def beam_span_at(tier: Tier, y: float, rule: FrameRule) -> tuple[float, float] | None:
    """指定y位置での横梁の実長(x範囲)を返す。境界の外なら None。"""
    offset_poly = _offset_inward(tier.polygon, rule.frame_width / 2)
    minx, miny, maxx, maxy = offset_poly.bounds
    if y < miny - 1e-9 or y > maxy + 1e-9:
        return None
    probe = LineString([(minx - 1.0, y), (maxx + 1.0, y)])
    inter = probe.intersection(offset_poly)
    if inter.is_empty:
        return None
    xs = []
    if inter.geom_type == "LineString":
        xs = [c[0] for c in inter.coords]
    elif inter.geom_type == "MultiLineString":
        for geom in inter.geoms:
            xs += [c[0] for c in geom.coords]
    if not xs:
        return None
    return (min(xs), max(xs))


def _vertical_length_at_x(x: float, offset_tiers: list[Polygon]) -> float:
    total = 0.0
    for poly in offset_tiers:
        minx, miny, maxx, maxy = poly.bounds
        if x < minx - 1e-9 or x > maxx + 1e-9:
            continue
        probe = LineString([(x, miny - 1.0), (x, maxy + 1.0)])
        inter = probe.intersection(poly)
        total += inter.length
    return total


def layout_vertical_posts(shape: SlopeShape, rule: FrameRule) -> PostLayout:
    """段をまたいで同一X位置になるよう、総延長最小の縦柱配置を探索する。

    周囲枠(左右の外周斜め材含む)は別部材として扱うため、内部の縦柱は
    周囲枠の内縁からさらにnominal_gap離れた範囲内に配置する。
    """
    offset_tiers = [_offset_inward(t.polygon, rule.frame_width) for t in shape.tiers]
    minx = min(p.bounds[0] for p in offset_tiers)
    maxx = max(p.bounds[2] for p in offset_tiers)
    usable_minx = minx + rule.nominal_gap + rule.frame_width / 2
    usable_maxx = maxx - rule.nominal_gap - rule.frame_width / 2
    total_width = usable_maxx - usable_minx
    step = rule.pitch

    if total_width <= 0:
        return PostLayout(positions=[], total_length=0.0, offset=0.0)

    n = max(1, round(total_width / step))
    max_offset = total_width - n * step
    if max_offset < 0:
        n = max(1, n - 1)
        max_offset = total_width - n * step
    max_offset = max(0.0, max_offset)

    best: PostLayout | None = None
    steps = max(1, math.floor(max_offset / rule.search_step) + 1)
    for i in range(steps):
        offset = min(i * rule.search_step, max_offset)
        positions = [usable_minx + offset + j * step for j in range(n + 1)]
        if positions[-1] > usable_maxx + 1e-6:
            continue
        total_length = sum(_vertical_length_at_x(x, offset_tiers) for x in positions)
        if best is None or total_length < best.total_length:
            best = PostLayout(positions=positions, total_length=total_length, offset=offset)

    assert best is not None
    return best
