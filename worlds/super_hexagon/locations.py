from typing import Dict, NamedTuple

from BaseClasses import Location

LEVELS = [
    "Hexagon",
    "Hexagoner",
    "Hexagonest",
    "Hyper Hexagon",
    "Hyper Hexagoner",
    "Hyper Hexagonest",
]


class HexagonLocation(Location):
    game = "Super Hexagon"


class HexagonLocationData(NamedTuple):
    location_id: int
    region: str


def _build_location_table() -> Dict[str, HexagonLocationData]:
    table: Dict[str, HexagonLocationData] = {}
    times_list = range(0, self.options.survival_time.value, 10)
    for level_idx, level in enumerate(LEVELS):
        for time_idx, time in enumerate(times_list):
            name = f"{level}: {time} seconds"
            table[name] = HexagonLocationData(
                location_id=(level_idx + 1) * len(times_list) + time_idx,
                region=level,
            )
    return table


location_data_table: Dict[str, HexagonLocationData] = _build_location_table()

location_table: Dict[str, int] = {name: data.location_id for name, data in location_data_table.items()}
