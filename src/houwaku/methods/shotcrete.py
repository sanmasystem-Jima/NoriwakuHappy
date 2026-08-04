"""吹付法枠工の数量算出。"""
from __future__ import annotations

from ..geometry import GridLayout
from ..models import ProjectInput
from .base import MethodCalculator, QuantityResult


class ShotcreteCalculator(MethodCalculator):
    method_label = "吹付法枠工"

    def calculate(self, project: ProjectInput, layout: GridLayout) -> QuantityResult:
        frame = project.frame
        opt = project.shotcrete
        result = QuantityResult(method_label=self.method_label)

        total_length = layout.total_frame_length
        overlap_area = layout.intersection_count * frame.frame_width * frame.frame_width
        shotcrete_area = total_length * frame.frame_width - overlap_area

        design_volume = shotcrete_area * opt.thickness
        material_volume = design_volume * opt.loss_factor

        mesh_area = shotcrete_area * opt.mesh_overlap_factor

        result.add(
            "吹付面積",
            round(shotcrete_area, 2),
            "m2",
            f"総延長{total_length:.2f}m×枠幅{frame.frame_width:.2f}m-交点重複{overlap_area:.2f}m2",
        )
        result.add(
            "吹付設計体積",
            round(design_volume, 3),
            "m3",
            f"吹付面積×吹付厚{opt.thickness:.2f}m",
        )
        result.add(
            "吹付材料数量(ロス込み)",
            round(material_volume, 3),
            "m3",
            f"設計体積×ロス係数{opt.loss_factor:.2f}",
        )
        result.add(
            "ラス金網数量",
            round(mesh_area, 2),
            "m2",
            f"吹付面積×重ね代係数{opt.mesh_overlap_factor:.2f}",
        )
        result.add("縦枠本数", layout.vertical_member_count, "本")
        result.add("横枠本数", layout.horizontal_member_count, "本")
        result.add("枠総延長", round(total_length, 2), "m")

        return result
