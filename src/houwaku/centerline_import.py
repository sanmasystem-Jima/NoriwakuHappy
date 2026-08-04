"""手描き(現場のCADで作成済み)の法枠中心線・境界を、DXFレイヤー指定で読み込む。

自動割付(のちのちの改良項目)とは別に、展開図に重ねて人が描いた
境界(枠の外側)・横梁・縦柱をそのまま使い、自動製図(整形・寸法記入)と
数量計算だけをツール側で行うための入力経路。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import Polygon

from .beams import Beam
from .boundary import Tier
from .dxf_background import BackgroundGeometry
from .posts import Post


def _round_pt(p: tuple[float, float], ndigits: int) -> tuple[float, float]:
    return (round(p[0], ndigits), round(p[1], ndigits))


def _merge_connected_chains(
    chains: list[list[tuple[float, float]]], ndigits: int = 4
) -> list[list[tuple[float, float]]]:
    """端点を共有するチェーン同士を1本の折れ線につなげる。

    測点(折れ点)を境に、1本の横梁が左半分・右半分の2本の別々のLINEとして
    描かれていることがある。ちょうど2本のチェーンだけが同じ点で接している
    場合に限り、それらを1本の折れ線として結合する(3本以上が同じ点に
    集まっている場合は判断がつかないので結合しない)。
    """
    endpoint_count: dict[tuple[float, float], int] = {}
    for c in chains:
        for p in (c[0], c[-1]):
            key = _round_pt(p, ndigits)
            endpoint_count[key] = endpoint_count.get(key, 0) + 1

    used = [False] * len(chains)
    merged: list[list[tuple[float, float]]] = []

    for i, c in enumerate(chains):
        if used[i]:
            continue
        used[i] = True
        current = list(c)

        # 終点側に伸ばす
        while True:
            key = _round_pt(current[-1], ndigits)
            if endpoint_count.get(key, 0) != 2:
                break
            partner = next(
                (j for j, other in enumerate(chains) if not used[j] and (_round_pt(other[0], ndigits) == key or _round_pt(other[-1], ndigits) == key)),
                None,
            )
            if partner is None:
                break
            other = chains[partner]
            to_add = other[1:] if _round_pt(other[0], ndigits) == key else list(reversed(other))[1:]
            current.extend(to_add)
            used[partner] = True

        # 始点側に伸ばす
        while True:
            key = _round_pt(current[0], ndigits)
            if endpoint_count.get(key, 0) != 2:
                break
            partner = next(
                (j for j, other in enumerate(chains) if not used[j] and (_round_pt(other[0], ndigits) == key or _round_pt(other[-1], ndigits) == key)),
                None,
            )
            if partner is None:
                break
            other = chains[partner]
            to_add = other[:-1] if _round_pt(other[-1], ndigits) == key else list(reversed(other))[:-1]
            current = to_add + current
            used[partner] = True

        merged.append(current)

    return merged


def beams_from_layer(bg: BackgroundGeometry, layer_name: str) -> list[Beam]:
    chains = [c for c in bg.layers.get(layer_name, []) if len(c) >= 2]
    return [Beam(points=chain) for chain in _merge_connected_chains(chains)]


def posts_from_layer(bg: BackgroundGeometry, layer_name: str) -> list[Post]:
    return [Post(start=chain[0], end=chain[-1]) for chain in bg.layers.get(layer_name, []) if len(chain) >= 2]


@dataclass
class BoundaryImportResult:
    tiers: list[Tier]
    stray_segment_count: int = 0  # 閉じたループにならなかった線分の数(参考・注記線など)


def _edge_key(a: tuple[float, float], b: tuple[float, float]) -> tuple:
    return (a, b) if a <= b else (b, a)


def tiers_from_layer(bg: BackgroundGeometry, layer_name: str, ndigits: int = 4) -> BoundaryImportResult:
    """指定レイヤーの線分をつなぎ、閉じたループ(=各段の境界)を復元する。

    線分は必ずしも1本のポリラインとして描かれているとは限らない(手描きのLINE
    の集まりのことが多い)ため、端点同士をつないでループを探す。閉じなかった
    線分(注記・引出線など)は無視し、件数だけ報告する。
    """
    adjacency: dict[tuple[float, float], list[tuple[float, float]]] = {}
    for chain in bg.layers.get(layer_name, []):
        rounded = [_round_pt(p, ndigits) for p in chain]
        for a, b in zip(rounded, rounded[1:]):
            if a == b:
                continue
            adjacency.setdefault(a, []).append(b)
            adjacency.setdefault(b, []).append(a)

    visited_edges: set[tuple] = set()
    loops: list[list[tuple[float, float]]] = []
    stray_segments = 0

    for start, neighbors in adjacency.items():
        for first_next in list(neighbors):
            key = _edge_key(start, first_next)
            if key in visited_edges:
                continue
            visited_edges.add(key)
            path = [start, first_next]
            prev, cur = start, first_next
            closed = False
            while cur != start:
                candidates = [n for n in adjacency.get(cur, []) if _edge_key(cur, n) not in visited_edges]
                if not candidates:
                    break
                nxt = candidates[0]
                visited_edges.add(_edge_key(cur, nxt))
                prev, cur = cur, nxt
                path.append(cur)
                if cur == start:
                    closed = True
                    break
            if closed and len(path) - 1 >= 3:
                loops.append(path[:-1])
            else:
                stray_segments += len(path) - 1

    tiers = [Tier(polygon=Polygon(loop)) for loop in loops]
    tiers.sort(key=lambda t: t.polygon.centroid.y)
    return BoundaryImportResult(tiers=tiers, stray_segment_count=stray_segments)
