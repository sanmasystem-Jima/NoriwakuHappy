"""プレキャスト法枠工の数量算出。"""
from __future__ import annotations

import math

from ..geometry import GridLayout
from ..models import ProjectInput
from .base import MethodCalculator, QuantityResult


class PrecastCalculator(MethodCalculator):
    method_label = "プレキャスト法枠工"

    def calculate(self, project: ProjectInput, layout: GridLayout) -> QuantityResult:
        frame = project.frame
        opt = project.precast
        result = QuantityResult(method_label=self.method_label)

        total_length = layout.total_frame_length
        segment_count = math.ceil(total_length / opt.segment_length)
        joint_count = max(0, segment_count - layout.horizontal_member_count - layout.vertical_member_count)
        # 継目数 = 部材数 - 部材が途切れず1本で収まる枠の本数(簡易近似)

        total_weight = segment_count * opt.segment_length * opt.unit_weight_per_m / 1000.0  # t

        backfill_area = total_length * frame.frame_width
        backfill_volume = backfill_area * opt.backfill_thickness

        result.add(
            "プレキャスト部材数",
            segment_count,
            "本",
            f"総延長{total_length:.2f}m ÷ 部材長{opt.segment_length:.2f}m(切上げ)",
        )
        result.add(
            "部材質量",
            round(total_weight, 3),
            "t",
            f"部材数{segment_count}本×{opt.segment_length:.2f}m×{opt.unit_weight_per_m:.1f}kg/m",
        )
        result.add(
            "目地処理箇所数(概算)",
            joint_count,
            "箇所",
        )
        result.add(
            "裏込め材体積",
            round(backfill_volume, 3),
            "m3",
            f"総延長{total_length:.2f}m×枠幅{frame.frame_width:.2f}m×厚さ{opt.backfill_thickness:.2f}m",
        )
        result.add("縦枠本数", layout.vertical_member_count, "本")
        result.add("横枠本数", layout.horizontal_member_count, "本")
        result.add("枠総延長", round(total_length, 2), "m")

        return result
