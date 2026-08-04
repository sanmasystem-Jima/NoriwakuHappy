"""DXFファイルから背景表示用の線分を読み込む(レイヤー名に依存しない汎用取込)。

背景としての表示(segments)はレイヤー名に関係なく全部読み込むが、
手描きで入力された法枠中心線(横梁・縦柱)をレイヤー名指定で個別に
取り出せるよう、レイヤーごとの線分チェーン(layers)も保持する。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class BackgroundGeometry:
    segments: list[tuple[tuple[float, float], tuple[float, float]]]
    bounds: tuple[float, float, float, float]  # minx, miny, maxx, maxy
    unit_scale: float  # ワールド座標(m)に変換するための倍率
    layers: dict[str, list[list[tuple[float, float]]]] = field(default_factory=dict)

    @property
    def layer_names(self) -> list[str]:
        return sorted(self.layers.keys())


def scale_background(background: BackgroundGeometry, factor: float) -> BackgroundGeometry:
    """全座標をfactor倍した新しいBackgroundGeometryを返す。

    CAD(V-nas)側の保存時の仕様で、意図しない縮尺がDXFに付いてしまう
    ことがあるため、自動判定(mm/m)だけでは吸収しきれない場合の手動補正
    に使う(factor=1.0なら補正なし)。
    """
    if factor == 1.0:
        return background

    def scale_pt(p: tuple[float, float]) -> tuple[float, float]:
        return (p[0] * factor, p[1] * factor)

    minx, miny, maxx, maxy = background.bounds
    return BackgroundGeometry(
        segments=[(scale_pt(a), scale_pt(b)) for a, b in background.segments],
        bounds=(minx * factor, miny * factor, maxx * factor, maxy * factor),
        unit_scale=background.unit_scale * factor,
        layers={
            name: [[scale_pt(p) for p in chain] for chain in chains]
            for name, chains in background.layers.items()
        },
    )


FRAME_COVERAGE_RATIO = 0.9  # 図枠とみなす、閉じたポリライン自身の外形が全体に占める割合の下限


def is_frame_polyline(points: list[tuple[float, float]], bounds: tuple[float, float, float, float]) -> bool:
    """図枠(用紙の外周・表題欄の2重の矩形など)とみなせる閉じたポリラインか
    どうか。ぴったり全体の外形と一致するものだけでなく、2重に描かれた
    枠(内側の矩形は全体よりわずかに小さい)も両方とも図枠として除外する
    ため、「閉じていて、自身の外形が全体の大部分(FRAME_COVERAGE_RATIO以上)
    を占める」ことだけを条件にする。
    """
    if len(points) < 3:
        return False
    if math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1]) > 1e-6:
        return False
    minx, miny, maxx, maxy = bounds
    total_w, total_h = maxx - minx, maxy - miny
    if total_w <= 0 or total_h <= 0:
        return False
    pxs = [p[0] for p in points]
    pys = [p[1] for p in points]
    poly_w, poly_h = max(pxs) - min(pxs), max(pys) - min(pys)
    return poly_w >= total_w * FRAME_COVERAGE_RATIO and poly_h >= total_h * FRAME_COVERAGE_RATIO


FRAME_EDGE_TOLERANCE_RATIO = 0.02  # 図枠の辺とみなす、端からのズレの許容割合


def is_frame_edge_segment(
    points: list[tuple[float, float]], bounds: tuple[float, float, float, float]
) -> bool:
    """閉じたポリラインではなく、個別のLINEで(閉じずに)描かれた図枠の1辺
    かどうか。矩形の図枠が、4本の独立したLINE(閉じたポリラインでは
    ない)として描かれていることがあり、is_frame_polylineだけでは
    (「閉じている」ことが条件のため)これを検出できない。

    ほぼ水平/垂直で、全体の外形の大部分(FRAME_COVERAGE_RATIO以上)を
    覆い、かつ全体の端(上下または左右)のすぐ近くにある直線を図枠の
    辺とみなす。
    """
    if len(points) < 2:
        return False
    minx, miny, maxx, maxy = bounds
    total_w, total_h = maxx - minx, maxy - miny
    if total_w <= 0 or total_h <= 0:
        return False
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    tol = max(total_w, total_h) * FRAME_EDGE_TOLERANCE_RATIO

    if span_y <= tol and span_x >= total_w * FRAME_COVERAGE_RATIO:
        y = sum(ys) / len(ys)
        if abs(y - miny) <= tol or abs(y - maxy) <= tol:
            return True
    if span_x <= tol and span_y >= total_h * FRAME_COVERAGE_RATIO:
        x = sum(xs) / len(xs)
        if abs(x - minx) <= tol or abs(x - maxx) <= tol:
            return True
    return False


def is_frame_chain(points: list[tuple[float, float]], bounds: tuple[float, float, float, float]) -> bool:
    """図枠(閉じたポリライン、または個別のLINEで描かれた辺)とみなせるか。"""
    return is_frame_polyline(points, bounds) or is_frame_edge_segment(points, bounds)


def segments_excluding_frame(
    background: BackgroundGeometry,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """図枠(用紙の外周・表題欄の2重の矩形など)を除いた線分。

    参照表示(GUIプレビュー)やDXF書き出しのフォールバック(元ファイルを
    開き直せない場合)で共通して使う。
    """
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for point_lists in background.layers.values():
        for points in point_lists:
            if is_frame_chain(points, background.bounds):
                continue
            segments.extend(zip(points, points[1:]))
    return segments


def content_bounds(background: BackgroundGeometry) -> tuple[float, float, float, float]:
    """図枠を除いた実質的な内容(境界・縦柱・横梁など)だけの外形。

    図枠(用紙の外周など)を含めたまま高さ・幅を比較すると、割付図と
    ラス網展開図の縮尺が合っているかの判定(高さの一致確認)がずれる
    ため、図枠を除いた内容だけで判定する。
    """
    segments = segments_excluding_frame(background)
    if not segments:
        return background.bounds
    xs = [p[0] for a, b in segments for p in (a, b)]
    ys = [p[1] for a, b in segments for p in (a, b)]
    return (min(xs), min(ys), max(xs), max(ys))


SYSTEM_LAYERS = {"V-nasSTD"}  # V-nas系CADが出力する用紙枠等のシステムレイヤー(実際の図形内容ではない)


def _iter_entities_by_layer(msp):
    """(レイヤー名, 点列)を返す。点列は2点以上(LINEなら2点、ポリラインならN点)。"""
    for entity in msp:
        dxftype = entity.dxftype()
        layer = entity.dxf.layer
        if layer in SYSTEM_LAYERS:
            continue

        if dxftype == "LINE":
            yield layer, [(entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y)]
        elif dxftype == "LWPOLYLINE":
            points = [(p[0], p[1]) for p in entity.get_points()]
            if entity.closed and len(points) > 1:
                points.append(points[0])
            if len(points) > 1:
                yield layer, points
        elif dxftype == "POLYLINE":
            points = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
            if entity.is_closed and len(points) > 1:
                points.append(points[0])
            if len(points) > 1:
                yield layer, points
        elif dxftype == "ARC":
            center = entity.dxf.center
            radius = entity.dxf.radius
            a0 = math.radians(entity.dxf.start_angle)
            a1 = math.radians(entity.dxf.end_angle)
            if a1 < a0:
                a1 += 2 * math.pi
            steps = 12
            points = [
                (
                    center.x + radius * math.cos(a0 + (a1 - a0) * i / steps),
                    center.y + radius * math.sin(a0 + (a1 - a0) * i / steps),
                )
                for i in range(steps + 1)
            ]
            yield layer, points


AUTO_MM_EXTENT_THRESHOLD = 1000.0  # 現実の法面でこれを超える寸法は考えにくい→mm単位の描き忘れとみなす
BOUNDARY_LAYER_NAME = "外周"  # 割付図・ラス網展開図で共通の境界レイヤー名の規約


def load_background(path: str, unit_scale: float | None = None) -> BackgroundGeometry:
    """DXFの全レイヤーの線分を、レイヤー名に関係なく背景として読み込む。

    同時に、レイヤーごとの点列(layers)も保持しておき、手描きの法枠中心線
    レイヤーを名前指定で個別に取り出せるようにする。

    unit_scale: DXFの生座標をワールド座標(メートル)に変換する倍率。
    Noneなら自動判定する — 割付図は専用テンプレート(単位=m)での作成を
    基本とするが、うっかりmm単位のまま描いてしまうこともあるため、
    生座標の範囲がAUTO_MM_EXTENT_THRESHOLDを超えていたら(現実の法面で
    あり得ない大きさ)、mm単位とみなして自動的に1/1000する。

    判定に使う範囲は、"外周"レイヤーがあればそれだけから計算する。
    V-nas系の実測図は用紙枠・丈量表などファイル全体の範囲を歪める図形を
    同じファイルに含むのが常態で、レイヤー名を問わず全図形から範囲を
    取ると、そうした現場と無関係な図形の増減で判定が不安定になる
    (新しい邪魔者が増えるたびに個別除外が必要になってしまう)。
    境界レイヤーの範囲だけを見れば、それ以外に何が同居していても
    影響されない。"外周"レイヤーが無いファイルは、従来通り全図形から
    判定する(後方互換のフォールバック)。
    """
    import ezdxf

    doc = ezdxf.readfile(path)
    msp = doc.modelspace()

    raw_entities = list(_iter_entities_by_layer(msp))
    if not raw_entities:
        raise ValueError("DXFファイルから図形(LINE/LWPOLYLINE/POLYLINE/ARC)が見つかりませんでした")

    if unit_scale is None:
        boundary_entities = [(layer, pts) for layer, pts in raw_entities if layer == BOUNDARY_LAYER_NAME]
        extent_entities = boundary_entities or raw_entities
        raw_minx = min(p[0] for _, pts in extent_entities for p in pts)
        raw_maxx = max(p[0] for _, pts in extent_entities for p in pts)
        raw_miny = min(p[1] for _, pts in extent_entities for p in pts)
        raw_maxy = max(p[1] for _, pts in extent_entities for p in pts)
        if max(raw_maxx - raw_minx, raw_maxy - raw_miny) > AUTO_MM_EXTENT_THRESHOLD:
            unit_scale = 1.0 / 1000.0
        else:
            unit_scale = 1.0

    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    layers: dict[str, list[list[tuple[float, float]]]] = {}
    minx = miny = float("inf")
    maxx = maxy = float("-inf")

    for layer, raw_points in raw_entities:
        points = [(x * unit_scale, y * unit_scale) for x, y in raw_points]
        layers.setdefault(layer, []).append(points)
        for a, b in zip(points, points[1:]):
            segments.append((a, b))
            minx = min(minx, a[0], b[0])
            maxx = max(maxx, a[0], b[0])
            miny = min(miny, a[1], b[1])
            maxy = max(maxy, a[1], b[1])

    return BackgroundGeometry(
        segments=segments,
        bounds=(minx, miny, maxx, maxy),
        unit_scale=unit_scale,
        layers=layers,
    )
