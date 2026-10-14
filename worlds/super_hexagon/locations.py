from typing import Dict, NamedTuple

from BaseClasses import Location
from .options import SurvivalTime

# In the ingame index order, cause its dumb
LEVELS = [
    "Hexagon",
    "Hyper Hexagonest",
    "Hyper Hexagoner",
    "Hyper Hexagon",
    "Hexagonest",
    "Hexagoner",
]
LEVEL_CHECK_OFFSET = 100


class HexagonLocation(Location):
    game = "Super Hexagon"


class HexagonLocationData(NamedTuple):
    location_id: int
    region: str
    time: int

def _build_location_table() -> Dict[str, HexagonLocationData]:
    table: Dict[str, HexagonLocationData] = {}
    for level_idx, level in enumerate(LEVELS):
        for time in range(0, SurvivalTime.range_end + 10, 10):
            table[f"{level}: {time} seconds"] = HexagonLocationData(
                location_id=1 + level_idx * LEVEL_CHECK_OFFSET + time // 10,
                region=level,
                time=time,
            )
    return table



location_data_table: Dict[str, HexagonLocationData] = _build_location_table()

location_table: Dict[str, int] = {name: data.location_id for name, data in location_data_table.items()}
