"""現場打ちコンクリート法枠工の数量算出。"""
from __future__ import annotations

from ..geometry import GridLayout
from ..models import ProjectInput
from ..rebar import unit_weight
from .base import MethodCalculator, QuantityResult


class CastInPlaceCalculator(MethodCalculator):
    method_label = "現場打ちコンクリート法枠工"

    def calculate(self, project: ProjectInput, layout: GridLayout) -> QuantityResult:
        frame = project.frame
        opt = project.cast_in_place
        result = QuantityResult(method_label=self.method_label)

        total_length = layout.total_frame_length
        overlap_volume = self._intersection_overlap_volume(project, layout)
        concrete_volume = total_length * frame.cross_section_area - overlap_volume

        # 型枠は枠の両側面のみ(底面は地山、天端は均しのため型枠不要)
        formwork_area = total_length * 2 * frame.frame_height

        bar_length_total = total_length * opt.bar_count_per_frame
        bar_weight = bar_length_total * unit_weight(opt.bar_diameter) / 1000.0  # kg -> t

        stirrup_count = round(total_length / opt.stirrup_pitch)

        result.add(
            "コンクリート体積",
            round(concrete_volume, 3),
            "m3",
            f"総延長{total_length:.2f}m×断面積{frame.cross_section_area:.3f}m2-交点重複{overlap_volume:.3f}m3",
        )
        result.add(
            "型枠面積",
            round(formwork_area, 2),
            "m2",
            f"総延長{total_length:.2f}m×側面2面×枠高{frame.frame_height:.2f}m",
        )
        result.add(
            "主筋質量",
            round(bar_weight, 3),
            "t",
            f"{opt.bar_diameter} {opt.bar_count_per_frame}本×総延長{total_length:.2f}m",
        )
        result.add(
            "スターラップ本数(概算)",
            stirrup_count,
            "本",
            f"総延長{total_length:.2f}m ÷ ピッチ{opt.stirrup_pitch:.2f}m",
        )

        if opt.anchor_per_intersection > 0:
            anchor_count = round(layout.intersection_count * opt.anchor_per_intersection)
            result.add(
                "アンカー本数",
                anchor_count,
                "本",
                f"交点数{layout.intersection_count} × {opt.anchor_per_intersection}本/交点",
            )

        result.add("縦枠本数", layout.vertical_member_count, "本")
        result.add("横枠本数", layout.horizontal_member_count, "本")
        result.add("枠総延長", round(total_length, 2), "m")

        return result
