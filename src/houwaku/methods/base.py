"""工法別の数量算出インターフェース。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..geometry import GridLayout
from ..models import ProjectInput


@dataclass
class QuantityItem:
    label: str  # 項目名(例: コンクリート体積)
    value: float
    unit: str  # 単位(例: m3, m2, kg, 本)
    note: str = ""  # 補足(算出根拠など)


@dataclass
class QuantityResult:
    method_label: str
    items: list[QuantityItem] = field(default_factory=list)

    def add(self, label: str, value: float, unit: str, note: str = "") -> None:
        self.items.append(QuantityItem(label=label, value=value, unit=unit, note=note))


class MethodCalculator(ABC):
    """工法ごとの数量算出ロジックの基底クラス。"""

    method_label: str = ""

    @abstractmethod
    def calculate(self, project: ProjectInput, layout: GridLayout) -> QuantityResult:
        ...

    def _intersection_overlap_volume(self, project: ProjectInput, layout: GridLayout) -> float:
        """縦横の枠が交差する部分の体積二重計上分を返す。

        総延長×断面積で体積を出すと、交点部分(枠幅四方)が縦横双方から
        1回ずつ、計2回カウントされるため、1回分を差し引く。
        """
        frame = project.frame
        return layout.intersection_count * frame.frame_width * frame.cross_section_area
