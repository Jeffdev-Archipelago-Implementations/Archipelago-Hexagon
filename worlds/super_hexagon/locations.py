from typing import Dict, NamedTuple

from BaseClasses import Location

LOCATION_BASE_ID = 100

LEVELS = [
    "Hexagon",
    "Hexagoner",
    "Hexagonest",
    "Hyper Hexagon",
    "Hyper Hexagoner",
    "Hyper Hexagonest",
]

TIMES = [10, 20, 30, 40, 50, 60]


class HexagonLocation(Location):
    game = "Super Hexagon"


class HexagonLocationData(NamedTuple):
    address: int
    region: str


def _build_location_table() -> Dict[str, HexagonLocationData]:
    table: Dict[str, HexagonLocationData] = {}
    for level_idx, level in enumerate(LEVELS):
        for time_idx, time in enumerate(TIMES):
            name = f"{level}: {time} seconds"
            table[name] = HexagonLocationData(
                address=LOCATION_BASE_ID + level_idx * len(TIMES) + time_idx,
                region=level,
            )
    return table


location_data_table: Dict[str, HexagonLocationData] = _build_location_table()

location_table: Dict[str, int] = {name: data.address for name, data in location_data_table.items()}
