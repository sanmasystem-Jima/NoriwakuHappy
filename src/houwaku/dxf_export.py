"""法枠展開図をDXFファイルとして出力する。"""
from __future__ import annotations

import ezdxf

from .geometry import GridLayout
from .methods.base import QuantityResult

LAYER_OUTLINE = "HOUWAKU_OUTLINE"
LAYER_HORIZONTAL = "HOUWAKU_YOKO_WAKU"  # 横枠(等高線方向)
LAYER_VERTICAL = "HOUWAKU_TATE_WAKU"  # 縦枠(斜面方向)
LAYER_TEXT = "HOUWAKU_TEXT"
LAYER_TABLE = "HOUWAKU_QUANTITY_TABLE"


def build_document(layout: GridLayout, quantity: QuantityResult | None = None) -> "ezdxf.document.Drawing":
    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()

    doc.layers.add(LAYER_OUTLINE, color=7)
    doc.layers.add(LAYER_HORIZONTAL, color=1)
    doc.layers.add(LAYER_VERTICAL, color=5)
    doc.layers.add(LAYER_TEXT, color=7)
    doc.layers.add(LAYER_TABLE, color=7)

    slope = layout.slope
    w = slope.slope_width
    l = slope.slope_length

    # 法面外形(展開範囲)
    msp.add_lwpolyline(
        [(0, 0), (w, 0), (w, l), (0, l), (0, 0)],
        dxfattribs={"layer": LAYER_OUTLINE},
    )

    for start, end in layout.horizontal_lines():
        msp.add_line(start, end, dxfattribs={"layer": LAYER_HORIZONTAL})

    for start, end in layout.vertical_lines():
        msp.add_line(start, end, dxfattribs={"layer": LAYER_VERTICAL})

    title = (
        f"法長L={l:.2f}m 展開幅W={w:.2f}m  "
        f"縦ピッチ(実)={layout.vertical_axis.actual_pitch:.3f}m "
        f"横ピッチ(実)={layout.horizontal_axis.actual_pitch:.3f}m  "
        f"縦枠{layout.vertical_member_count}本 横枠{layout.horizontal_member_count}本"
    )
    msp.add_text(
        title,
        dxfattribs={"layer": LAYER_TEXT, "height": max(l, w) * 0.02 or 0.2},
    ).set_placement((0, -max(l, w) * 0.05 - 0.3))

    if quantity is not None:
        _add_quantity_table(msp, quantity, origin=(w + max(l, w) * 0.1, l))

    return doc


def _add_quantity_table(msp, quantity: QuantityResult, origin: tuple[float, float]) -> None:
    x0, y0 = origin
    line_height = 0.4
    text_height = 0.25

    msp.add_text(
        f"数量集計表 ({quantity.method_label})",
        dxfattribs={"layer": LAYER_TABLE, "height": text_height * 1.3},
    ).set_placement((x0, y0))

    for i, item in enumerate(quantity.items, start=1):
        line = f"{item.label}: {item.value} {item.unit}"
        if item.note:
            line += f"  ({item.note})"
        msp.add_text(
            line,
            dxfattribs={"layer": LAYER_TABLE, "height": text_height},
        ).set_placement((x0, y0 - i * line_height))


def export_dxf(path: str, layout: GridLayout, quantity: QuantityResult | None = None) -> None:
    doc = build_document(layout, quantity)
    doc.saveas(path)
