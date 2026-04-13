from BaseClasses import Region, Tutorial
from worlds.AutoWorld import WebWorld, World
from worlds.LauncherComponents import Component, Type, components, launch as launch_component

from .items import HexagonItem, item_data_table, item_table
from .locations import LEVELS, HexagonLocation, location_data_table, location_table
from .options import SuperHexagonOptions


def _launch_client(*args: str) -> None:
    from .Client import launch
    launch_component(launch, name="SuperHexagonClient", args=args)


components.append(Component(
    "Super Hexagon Client",
    func=_launch_client,
    component_type=Type.CLIENT,
    game_name="Super Hexagon",
))


class SuperHexagonWebWorld(WebWorld):
    theme = "grass"
    tutorials = [Tutorial(
        tutorial_name="Setup Guide",
        description="A guide to playing Super Hexagon in Archipelago.",
        language="English",
        file_name="guide_en.md",
        link="guide/en",
        authors=["Jeffdev"],
    )]


class SuperHexagonWorld(World):
    """
    Super Hexagon is a small indie game by Terry Cavanagh.
    Avoid the walls of hexagons while surviving as long as you can!
    Survive 60 seconds to complete levels.
    """

    game = "Super Hexagon"
    options_dataclass = SuperHexagonOptions
    options: SuperHexagonOptions
    web = SuperHexagonWebWorld()
    item_name_to_id = item_table
    location_name_to_id = location_table

    def create_item(self, name: str) -> HexagonItem:
        data = item_data_table[name]
        return HexagonItem(name, data.classification, data.code, self.player)

    def generate_early(self) -> None:
        self.push_precollected(self.create_item("Hexagon Unlock"))

    def create_items(self) -> None:
        unlock_names = [
            "Hexagoner Unlock",
            "Hexagonest Unlock",
            "Hyper Hexagon Unlock",
            "Hyper Hexagoner Unlock",
            "Hyper Hexagonest Unlock",
        ]
        pool = [self.create_item(name) for name in unlock_names]
        filler_count = len(location_table) - len(pool)
        pool += [self.create_item("Hexagon") for _ in range(filler_count)]
        self.multiworld.itempool += pool

    def create_regions(self) -> None:
        menu = Region("Menu", self.player, self.multiworld)
        self.multiworld.regions.append(menu)

        for level in LEVELS:
            region = Region(level, self.player, self.multiworld)
            region.locations += [
                HexagonLocation(self.player, loc_name, loc_data.address, region)
                for loc_name, loc_data in location_data_table.items()
                if loc_data.region == level
            ]
            self.multiworld.regions.append(region)

            if level == "Hexagon":
                menu.connect(region)
            else:
                unlock_item = f"{level} Unlock"
                entrance = menu.connect(region)
                entrance.access_rule = lambda state, item=unlock_item: state.has(item, self.player)

    def set_rules(self) -> None:
        goal_level = "Hexagonest" if self.options.goal == 0 else "Hyper Hexagonest"
        goal_location = f"{goal_level}: 60 seconds"
        unlock_items = [
            "Hexagoner Unlock",
            "Hexagonest Unlock",
            "Hyper Hexagon Unlock",
            "Hyper Hexagoner Unlock",
            "Hyper Hexagonest Unlock",
        ]
        levels_needed = self.options.levels_beat_required.value
        self.multiworld.completion_condition[self.player] = lambda state: (
            state.can_reach_location(goal_location, self.player) and
            state.has_from_list_unique(unlock_items, self.player, levels_needed)
        )

    def get_filler_item_name(self) -> str:
        return "Hexagon"

    def fill_slot_data(self) -> dict:
        return {
            "goal": int(self.options.goal),
            "levels_beat_required": int(self.options.levels_beat_required),
        }
