"""JIS 異形棒鋼の呼び名と単位質量(kg/m)の対応表。

JIS G 3112 に基づく代表値。実際の設計では使用する規格書の値を
確認すること。
"""
from __future__ import annotations

REBAR_UNIT_WEIGHT_KG_PER_M: dict[str, float] = {
    "D10": 0.560,
    "D13": 0.995,
    "D16": 1.560,
    "D19": 2.250,
    "D22": 3.040,
    "D25": 3.980,
    "D29": 5.040,
    "D32": 6.230,
    "D35": 7.510,
    "D38": 8.950,
}


def unit_weight(bar_diameter: str) -> float:
    try:
        return REBAR_UNIT_WEIGHT_KG_PER_M[bar_diameter]
    except KeyError as exc:
        available = ", ".join(sorted(REBAR_UNIT_WEIGHT_KG_PER_M))
        raise ValueError(
            f"未対応の鉄筋径です: {bar_diameter}(対応径: {available})"
        ) from exc
