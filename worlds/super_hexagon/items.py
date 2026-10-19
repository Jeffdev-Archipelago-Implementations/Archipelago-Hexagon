from typing import Dict, NamedTuple

from BaseClasses import Item, ItemClassification


class HexagonItem(Item):
    game = "Super Hexagon"


class HexagonItemData(NamedTuple):
    item_id: int
    classification: ItemClassification

# Same as in the game indexes, for the levels
item_data_table: Dict[str, HexagonItemData] = {
    "Hyper Hexagonest Unlock": HexagonItemData(1, ItemClassification.progression),
    "Hyper Hexagoner Unlock":  HexagonItemData(2, ItemClassification.progression),
    "Hyper Hexagon Unlock":    HexagonItemData(3, ItemClassification.progression),
    "Hexagonest Unlock":       HexagonItemData(4, ItemClassification.progression),
    "Hexagoner Unlock":        HexagonItemData(5, ItemClassification.progression),
    "Hexagon Unlock":          HexagonItemData(6, ItemClassification.progression), 
    "H E X A G O N":           HexagonItemData(7, ItemClassification.filler),
    "Hexagon Fragment":        HexagonItemData(8, ItemClassification.progression),
}

item_table: Dict[str, int] = {name: data.item_id for name, data in item_data_table.items()}
