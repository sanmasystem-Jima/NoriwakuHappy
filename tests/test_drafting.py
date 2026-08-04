import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from houwaku.boundary import SlopeShape
from houwaku.centerline_import import beams_from_layer, posts_from_layer, tiers_from_layer
from houwaku.dxf_background import load_background
from houwaku.drafting import prepare_drawn_members
from houwaku.spec import FrameRule

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_beam_near_tangent_to_boundary_corner_not_collapsed():
    """求積図.dxf の実データで見つかった回帰テスト(2026-08-04)。

    横梁が境界のある頂点付近で、最も近い辺(距離基準)ではなく隣の辺と
    交わるべきケース。以前の実装は最近傍の1辺だけを試して失敗すると
    その点を丸ごと捨てて次の点へ進んでいたため、この横梁は境界に沿って
    ほぼ接するように延びる形状のせいで全区間が失敗し、部材全体が
    ほぼ1点(長さ0)に潰れていた(実際の計算書で交差する縦柱の本数が
    誤って0本になっていた原因)。
    """
    bg = load_background(str(PROJECT_ROOT / "求積図.dxf"))
    tiers_result = tiers_from_layer(bg, "外周")
    shape = SlopeShape(tiers=tiers_result.tiers)
    rule = FrameRule(frame_width=0.3, frame_height=0.3, pitch=2.0, gradient_n=1.0)
    beams_raw = beams_from_layer(bg, "横")
    posts_raw = posts_from_layer(bg, "縦")

    tier_polys = [t.polygon for t in shape.tiers]
    beams, posts, crossing_counts, _, _ = prepare_drawn_members(
        tier_polys, rule.frame_width, beams_raw, posts_raw, set()
    )

    beams_sorted = sorted(beams, key=lambda b: -min(p[1] for p in b.points))
    lengths = [b.gross_length for b in beams_sorted]

    assert len(lengths) == 9
    # 下から2番目(sorted内ではindex7)が、以前は0m近くまで潰れていた。
    assert lengths[7] > 20.0
    assert sum(lengths) > 190.0
