from typing import Dict, NamedTuple

from BaseClasses import Item, ItemClassification

ITEM_BASE_ID = 100


class HexagonItem(Item):
    game = "Super Hexagon"


class HexagonItemData(NamedTuple):
    code: int
    classification: ItemClassification


item_data_table: Dict[str, HexagonItemData] = {
    "Hexagon Unlock":          HexagonItemData(ITEM_BASE_ID + 0, ItemClassification.progression),
    "Hexagoner Unlock":        HexagonItemData(ITEM_BASE_ID + 1, ItemClassification.progression),
    "Hexagonest Unlock":       HexagonItemData(ITEM_BASE_ID + 2, ItemClassification.progression),
    "Hyper Hexagon Unlock":    HexagonItemData(ITEM_BASE_ID + 3, ItemClassification.progression),
    "Hyper Hexagoner Unlock":  HexagonItemData(ITEM_BASE_ID + 4, ItemClassification.progression),
    "Hyper Hexagonest Unlock": HexagonItemData(ITEM_BASE_ID + 5, ItemClassification.progression),
    "Hexagon":                 HexagonItemData(ITEM_BASE_ID + 6, ItemClassification.filler),
}

item_table: Dict[str, int] = {name: data.code for name, data in item_data_table.items()}
