"""法枠工の入力パラメータを表すデータモデル。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MethodType(str, Enum):
    CAST_IN_PLACE = "cast_in_place"  # 現場打ちコンクリート法枠
    PRECAST = "precast"  # プレキャスト法枠
    SHOTCRETE = "shotcrete"  # 吹付法枠


@dataclass
class SlopeParameters:
    """法面の展開図の基本寸法。"""

    slope_length: float  # 法長 L (m) : 展開図の縦方向(斜面を下る方向)
    slope_width: float  # 展開幅 W (m) : 展開図の横方向(等高線方向)

    def __post_init__(self) -> None:
        if self.slope_length <= 0 or self.slope_width <= 0:
            raise ValueError("法長・展開幅は正の値である必要があります")


@dataclass
class FrameSpec:
    """法枠の断面・割付に関するパラメータ。"""

    target_vertical_pitch: float  # 縦ピッチ目標値 Pv (m) : 横枠(等高線方向の枠)の間隔
    target_horizontal_pitch: float  # 横ピッチ目標値 Ph (m) : 縦枠(斜面方向の枠)の間隔
    frame_width: float  # 枠幅 (m) : 断面の幅
    frame_height: float  # 枠成/枠高さ (m) : 断面の高さ

    def __post_init__(self) -> None:
        for name in (
            "target_vertical_pitch",
            "target_horizontal_pitch",
            "frame_width",
            "frame_height",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} は正の値である必要があります")

    @property
    def cross_section_area(self) -> float:
        return self.frame_width * self.frame_height


@dataclass
class CastInPlaceOptions:
    """現場打ちコンクリート法枠 固有パラメータ。"""

    bar_count_per_frame: int = 4  # 主筋本数(枠1本あたり)
    bar_diameter: str = "D13"  # 主筋径(JIS呼び名)
    stirrup_pitch: float = 0.3  # 配力筋/スターラップピッチ (m)
    anchor_per_intersection: float = 0.0  # 交点あたりのアンカー本数(0なら未使用)


@dataclass
class PrecastOptions:
    """プレキャスト法枠 固有パラメータ。"""

    segment_length: float = 2.0  # プレキャスト部材1本あたりの長さ (m)
    unit_weight_per_m: float = 150.0  # 部材の単位長さあたり質量 (kg/m)
    backfill_thickness: float = 0.1  # 裏込め材厚さ (m) ※裏込め幅は枠幅とみなす


@dataclass
class ShotcreteOptions:
    """吹付法枠 固有パラメータ。"""

    thickness: float = 0.2  # 吹付厚 (m)
    loss_factor: float = 1.15  # 吹付ロス係数(はね返り等)
    mesh_overlap_factor: float = 1.05  # ラス金網重ね代係数


@dataclass
class ProjectInput:
    """1回の計算に必要な入力一式。"""

    slope: SlopeParameters
    frame: FrameSpec
    method: MethodType
    cast_in_place: CastInPlaceOptions = field(default_factory=CastInPlaceOptions)
    precast: PrecastOptions = field(default_factory=PrecastOptions)
    shotcrete: ShotcreteOptions = field(default_factory=ShotcreteOptions)
