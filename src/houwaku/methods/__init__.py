from .base import QuantityItem, QuantityResult, MethodCalculator
from .cast_in_place import CastInPlaceCalculator
from .precast import PrecastCalculator
from .shotcrete import ShotcreteCalculator
from ..models import MethodType

CALCULATORS: dict[MethodType, type[MethodCalculator]] = {
    MethodType.CAST_IN_PLACE: CastInPlaceCalculator,
    MethodType.PRECAST: PrecastCalculator,
    MethodType.SHOTCRETE: ShotcreteCalculator,
}


def get_calculator(method: MethodType) -> MethodCalculator:
    return CALCULATORS[method]()


__all__ = [
    "QuantityItem",
    "QuantityResult",
    "MethodCalculator",
    "CastInPlaceCalculator",
    "PrecastCalculator",
    "ShotcreteCalculator",
    "CALCULATORS",
    "get_calculator",
]
