"""数量(拾い出し)の計算。

割付済みの部材データ(周囲枠・横梁・縦柱)から、各数量を計算式つきの
テキストレポートとして出力する。まずは計算ロジックが合っているかを
確認する段階なので、体裁(エクセル等)は後回しにし、テキスト出力のみ
とする(ユーザー指示、2026-08-03)。

各数量の式(ユーザーとの対話で確認済み):
- 周囲枠: 上・左・右・下の順に各辺を足し算。下枠のみ縦柱との交差数も集計
  (水切りモルタルの延長計算に使うため)。
- 縦柱: 左から順に足し算、合計。
- 横梁: 上から順に足し算、同時に交差する縦柱の本数(切り欠き)も集計し、
  最後に(本数×枠幅)を減算して正味合計とする。
- 水切りモルタル: 法肩(最上段の上枠)はそのままの延長。法尻(最下段の下枠)は
  下枠の延長から(交差本数×枠幅)を減算。中詰め工が厚層基材のときは、
  段と段の間の境界すべてにも同様の式で水切りモルタルが乗る
  (中詰め工の種類に関係なく、一番下の境界の下側には常に水切りモルタルが付く)。
- 中詰め面積 = ラス(メッシュ)面積 − (横梁+縦柱+周囲枠の面積、それぞれ
  延長×枠幅) − (段間の水切りモルタルの面積のみ。最上段・最下段の三角形は
  ラスの外なので含めない)。ラス面積はこのツールでは計算していないので
  外部から渡す。
- 型枠 = (横梁の正味長さ + 縦柱の延長 + 周囲枠の延長) × 2。単位はm
  (高さは掛けない)。
- 水抜きパイプ(中詰め=モルタル吹き付け工のときのみ) = 内部横梁(最下段を除く。
  最上段=法肩の梁は含む)ごとに、その梁と交差する縦柱の本数+1(縦柱で区切られる
  区間の数)を数え、その合計 × 枠幅。単位はm。
- 桁数(ユーザー指示、2026-08-04): 部材の長さは小数点以下1桁、枠幅・面積は
  小数点以下2桁で表示する。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import LineString, Point, Polygon

from .beams import Beam
from .boundary import SlopeShape
from .drafting import count_post_crossings, group_beams_by_tier, group_posts_by_tier, perimeter_outer_inner
from .perimeter import offset_centerline
from .posts import Post
from .spec import NAKAZUME_KOSOKISOZAI, NAKAZUME_MORTAR, FrameRule

Edge = tuple[tuple[float, float], tuple[float, float]]

_SIDE_LABELS = {"top": "上枠(法肩)", "left": "左枠", "right": "右枠", "bottom": "下枠(法尻)"}


def _edge_length(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def classify_perimeter_edges(tier_polygon: Polygon) -> dict[str, list[Edge]]:
    """周囲枠の各辺を上(法肩)・左・右・下(法尻)に分類する。

    法肩・法尻は横方向に連なる緩やかな辺の集まり、左右はそれをつなぐ
    急な(縦方向の)辺、という実際の法面形状の傾向に基づく簡易分類。
    辺の傾き(|dy|とdxの比較)でまず上下/左右のグループに分け、そのあと
    辺の中点の位置(タイヤの外接矩形の上半分/下半分、左半分/右半分)で
    上下・左右のどちらかを割り振る。
    """
    coords = list(tier_polygon.exterior.coords)
    minx, miny, maxx, maxy = tier_polygon.bounds
    mid_y = (miny + maxy) / 2
    mid_x = (minx + maxx) / 2
    sides: dict[str, list[Edge]] = {"top": [], "bottom": [], "left": [], "right": []}
    for a, b in zip(coords, coords[1:]):
        dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        if dy > dx:
            sides["right" if mx >= mid_x else "left"].append((a, b))
        else:
            sides["top" if my >= mid_y else "bottom"].append((a, b))
    return sides


@dataclass
class PerimeterSideResult:
    label: str
    edges: list[Edge]
    total: float
    crossing_count: int = 0  # 下枠のみ、縦柱の下端が触れている本数


def compute_perimeter_sides(
    tier_polygon: Polygon, frame_width: float, posts: list[Post]
) -> dict[str, PerimeterSideResult]:
    """周囲枠の延長は枠の中心線(真の境界から枠幅/2内側)で測る
    (STAGE1_周囲枠として図面に描くのと同じ基準)。交差する縦柱の判定は、
    縦柱側が実際にクリップされる内側面(真の境界から枠幅ぶん内側)を
    基準にする(縦柱の下端はここに乗っているため)。
    """
    centerline = offset_centerline(tier_polygon, frame_width / 2)
    inner = perimeter_outer_inner(tier_polygon, frame_width)[1]
    length_sides = classify_perimeter_edges(centerline)
    inner_sides = classify_perimeter_edges(inner)
    results: dict[str, PerimeterSideResult] = {}
    for label, edges in length_sides.items():
        total = sum(_edge_length(a, b) for a, b in edges)
        crossing_count = 0
        if label == "bottom" and inner_sides[label]:
            for p in posts:
                bottom_pt = Point(p.start if p.start[1] <= p.end[1] else p.end)
                if any(LineString([a, b]).distance(bottom_pt) < 1e-3 for a, b in inner_sides[label]):
                    crossing_count += 1
        results[label] = PerimeterSideResult(label=label, edges=edges, total=total, crossing_count=crossing_count)
    return results


@dataclass
class QuantityReport:
    text: str
    # 型枠・中詰め等、他の数量の計算に必要な集計値
    perimeter_total: float
    post_total: float
    beam_gross_total: float
    beam_net_total: float
    mizukiri_lengths: dict[str, float]  # "法肩" / "法尻" / "段間N" -> 延長
    formwork_total: float
    drain_pipe_total: float


def _format_sum(label: str, values: list[float], unit: str = "m") -> tuple[str, float]:
    total = sum(values)
    if not values:
        return f"{label}: (部材なし)", 0.0
    formula = " + ".join(f"{v:.1f}" for v in values)
    return f"{label}: {formula} = {total:.1f}{unit}", total


def build_quantity_report(
    shape: SlopeShape,
    rule: FrameRule,
    beams: list[Beam],
    posts: list[Post],
    nakazume_type: str = NAKAZUME_MORTAR,
    lath_area: float | None = None,
) -> QuantityReport:
    """割付済みの部材データから、計算式つきのテキストレポートを作る。

    beams/postsは、境界内側面までクリップ済み(prepare_drawn_membersの
    戻り値)を渡すこと。段(tier)は下から上の順に並んでいる前提。
    """
    lines: list[str] = []
    frame_width = rule.frame_width

    tier_polys = [t.polygon for t in shape.tiers]
    beam_groups = group_beams_by_tier(tier_polys, frame_width, beams)
    post_groups = group_posts_by_tier(tier_polys, frame_width, posts)
    crossing_counts = count_post_crossings(beams, posts)

    # --- 周囲枠 ---
    lines.append("【周囲枠】")
    perimeter_total = 0.0
    bottom_side_results: list[PerimeterSideResult] = []
    for tier_idx, (tier_poly, tier_posts) in enumerate(zip(tier_polys, post_groups)):
        if len(tier_polys) > 1:
            lines.append(f"- 下から{tier_idx + 1}段目")
        sides = compute_perimeter_sides(tier_poly, frame_width, tier_posts)
        for side_key in ("top", "left", "right", "bottom"):
            side = sides[side_key]
            text, _ = _format_sum(f"  {_SIDE_LABELS[side_key]}", [_edge_length(a, b) for a, b in side.edges])
            if side_key == "bottom":
                text += f"(縦柱との交差: {side.crossing_count}本)"
                bottom_side_results.append(side)
            lines.append(text)
            perimeter_total += side.total
    lines.append(f"周囲枠合計: {perimeter_total:.1f}m")
    lines.append("")

    # --- 縦柱(左から順) ---
    lines.append("【縦柱】(左から順)")
    posts_sorted = sorted(posts, key=lambda p: min(p.start[0], p.end[0]))
    text, post_total = _format_sum("縦柱合計", [p.length for p in posts_sorted])
    lines.append(text)
    lines.append("")

    # --- 横梁(上から順) ---
    lines.append("【横梁】(上から順)")
    beams_sorted = sorted(beams, key=lambda b: -min(p[1] for p in b.points))
    beam_lengths = [b.gross_length for b in beams_sorted]
    text, beam_gross_total = _format_sum("横梁合計(総延長)", beam_lengths)
    lines.append(text)
    counts_by_id = dict(zip((id(b) for b in beams), crossing_counts))
    counts_sorted = [counts_by_id[id(b)] for b in beams_sorted]
    total_crossings = sum(crossing_counts)
    beam_net_total = beam_gross_total - total_crossings * frame_width
    if counts_sorted:
        counts_formula = " + ".join(str(c) for c in counts_sorted)
        lines.append(f"交差する縦柱の本数(切り欠き)合計: {counts_formula} = {total_crossings}本")
    else:
        lines.append("交差する縦柱の本数(切り欠き)合計: (部材なし)")
    lines.append(
        f"正味合計 = {beam_gross_total:.1f} - ({total_crossings} × {frame_width:.2f}) = {beam_net_total:.1f}m"
    )
    lines.append("")

    # --- 水切りモルタル ---
    lines.append("【水切りモルタル】(延長)")
    mizukiri_lengths: dict[str, float] = {}
    top_side = compute_perimeter_sides(tier_polys[-1], frame_width, post_groups[-1])["top"]
    mizukiri_lengths["法肩"] = top_side.total
    lines.append(f"法肩: 上枠の延長そのまま = {top_side.total:.1f}m")

    bottom_side = bottom_side_results[0]
    toe_length = bottom_side.total - bottom_side.crossing_count * frame_width
    mizukiri_lengths["法尻"] = toe_length
    lines.append(
        f"法尻: {bottom_side.total:.1f} - ({bottom_side.crossing_count} × {frame_width:.2f}) = {toe_length:.1f}m"
    )

    if nakazume_type == NAKAZUME_KOSOKISOZAI:
        for tier_idx in range(len(tier_polys) - 1):
            # 上の段の下枠 = 下の段の上枠と同じ境界とみなし、上の段の下枠を使う
            upper_bottom = compute_perimeter_sides(tier_polys[tier_idx + 1], frame_width, post_groups[tier_idx + 1])["bottom"]
            length = upper_bottom.total - upper_bottom.crossing_count * frame_width
            key = f"段間{tier_idx + 1}"
            mizukiri_lengths[key] = length
            lines.append(
                f"  {key}: {upper_bottom.total:.1f} - ({upper_bottom.crossing_count} × {frame_width:.2f})"
                f" = {length:.1f}m"
            )
    lines.append("")

    # --- 型枠 ---
    lines.append("【型枠】")
    formwork_total = (beam_net_total + post_total + perimeter_total) * 2
    lines.append(
        f"(横梁正味 {beam_net_total:.1f} + 縦柱 {post_total:.1f} + 周囲枠 {perimeter_total:.1f}) × 2"
        f" = {formwork_total:.1f}m"
    )
    lines.append("")

    # --- 水抜きパイプ ---
    lines.append("【水抜きパイプ】")
    drain_pipe_total = 0.0
    if nakazume_type == NAKAZUME_MORTAR:
        # 最下段(1段目)を除く内部横梁ごとに、その梁と交差する縦柱の本数+1
        # (縦柱で区切られる区間の数)を数える。
        interior_beams = [b for group in beam_groups[1:] for b in group]
        pipe_counts = [counts_by_id[id(b)] + 1 for b in interior_beams]
        drain_pipe_count = sum(pipe_counts)
        if pipe_counts:
            pipe_counts_formula = " + ".join(str(c) for c in pipe_counts)
            lines.append(f"本数(内部横梁ごとに縦柱交差数+1の合計) = {pipe_counts_formula} = {drain_pipe_count}本")
        else:
            lines.append("本数(内部横梁ごとに縦柱交差数+1の合計): (部材なし)")
        drain_pipe_total = drain_pipe_count * frame_width
        lines.append(f"{drain_pipe_count}本 × 枠幅 {frame_width:.2f}m = {drain_pipe_total:.1f}m(VU50)")
    lines.append("")

    # --- 中詰め面積 ---
    lines.append("【中詰め面積】")
    if lath_area is None:
        lines.append("ラス面積が未入力のため計算できません(外部から与えてください)。")
    else:
        member_area = (beam_net_total + post_total + perimeter_total) * frame_width
        lines.append(
            f"部材面積 = (横梁正味 {beam_net_total:.1f} + 縦柱 {post_total:.1f} + 周囲枠 {perimeter_total:.1f})"
            f" × 枠幅 {frame_width:.2f} = {member_area:.2f}m2"
        )
        interior_mizukiri_area = 0.0
        if rule.gradient_n is not None:
            cross_section = frame_width * (frame_width * rule.gradient_n) / 2
            interior_keys = [k for k in mizukiri_lengths if k.startswith("段間")]
            interior_total_length = sum(mizukiri_lengths[k] for k in interior_keys)
            interior_mizukiri_area = interior_total_length * cross_section
            lines.append(
                f"水切り断面積 = 枠幅 {frame_width:.2f} × (枠幅 {frame_width:.2f} × 勾配n {rule.gradient_n:.2f}) / 2"
                f" = {cross_section:.2f}m2"
            )
            lines.append(
                f"段間水切り面積 = 水切り断面積 {cross_section:.2f} × 段間延長合計 {interior_total_length:.1f}"
                f" = {interior_mizukiri_area:.2f}m2"
            )
        else:
            lines.append("段間水切り面積: 勾配未確定のため0m2として計算")
        nakazume_area = lath_area - member_area - interior_mizukiri_area
        lines.append(
            f"中詰め面積 = ラス面積 {lath_area:.2f} - 部材面積 {member_area:.2f}"
            f" - 段間水切り面積 {interior_mizukiri_area:.2f} = {nakazume_area:.2f}m2"
        )

    return QuantityReport(
        text="\n".join(lines),
        perimeter_total=perimeter_total,
        post_total=post_total,
        beam_gross_total=beam_gross_total,
        beam_net_total=beam_net_total,
        mizukiri_lengths=mizukiri_lengths,
        formwork_total=formwork_total,
        drain_pipe_total=drain_pipe_total,
    )
