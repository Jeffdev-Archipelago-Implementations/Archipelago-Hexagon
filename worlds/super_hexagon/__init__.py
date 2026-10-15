from BaseClasses import ItemClassification, Region, Tutorial
from rule_builder.field_resolvers import FromOption
from rule_builder.rules import CanReachRegion, Has, HasFromListUnique
from worlds.AutoWorld import WebWorld, World
from worlds.LauncherComponents import Component, Type, components, icon_paths, launch as launch_component

from .items import HexagonItem, item_data_table, item_table
from .locations import GOAL_LEVELS, LEVELS, HexagonLocation, location_data_table, location_table
from .options import HexagonFragments, LevelsBeatRequired, SuperHexagonOptions


def _launch_client(*args: str) -> None:
    from .Client import launch
    launch_component(launch, name="SuperHexagonClient", args=args)


icon_paths["Super Hexagon"] = f"ap:{__name__}/assets/client_icon.png"

components.append(Component(
    "Super Hexagon Client",
    func=_launch_client,
    component_type=Type.CLIENT,
    game_name="Super Hexagon",
    icon="Super Hexagon",
))


class SuperHexagonWebWorld(WebWorld):
    theme = "grass"
    tutorials = [Tutorial(
        tutorial_name="Setup Guide",
        description="A guide to playing Super Hexagon in Archipelago.",
        language="English",
        file_name="setup_en.md",
        link="setup/en",
        authors=["Jeffdev"],
    )]


class SuperHexagonWorld(World):
    """
    Super Hexagon is a game where you play as a triangle avoiding dastardly hexagon walls.
    You must survive at all costs!
    """

    game = "Super Hexagon"
    options_dataclass = SuperHexagonOptions
    options: SuperHexagonOptions
    web = SuperHexagonWebWorld()
    item_name_to_id = item_table
    location_name_to_id = location_table

    def create_item(self, name: str) -> HexagonItem:
        data = item_data_table[name]
        return HexagonItem(name, data.classification, data.item_id, self.player)

    ut_can_gen_without_yaml = True

    @staticmethod
    def interpret_slot_data(slot_data: dict) -> dict:
        return slot_data

    def generate_early(self) -> None:
        slot_data = getattr(self.multiworld, "re_gen_passthrough", {}).get(self.game)
        if slot_data:
            self.options.goal.value = slot_data["goal"]
            self.options.survival_time.value = slot_data["survival_time"]
            self.options.levels_beat_required.value = slot_data["levels_beat_required"]
            self.options.hexagon_fragments.value = slot_data["hexagon_fragments"]

        # Round to a multiple of 10
        self.options.survival_time.value = round(self.options.survival_time.value, -1)

    def create_items(self) -> None:
        unlock_names = [
            "Hexagoner Unlock",
            "Hexagonest Unlock",
            "Hyper Hexagon Unlock",
            "Hyper Hexagoner Unlock",
            "Hyper Hexagonest Unlock",
        ]
        pool = [self.create_item(name) for name in unlock_names]
        
        for _ in range(self.options.hexagon_fragments.value):
            pool.append(self.create_item("Hexagon Fragment"))
        
        filler_count = len(self.multiworld.get_unfilled_locations(self.player)) - len(pool)
        pool += [self.create_item(self.get_filler_item_name()) for _ in range(filler_count)]
        self.multiworld.itempool += pool

    def create_regions(self) -> None:
        menu = Region("Menu", self.player, self.multiworld)
        self.multiworld.regions.append(menu)
        goal_level = GOAL_LEVELS[self.options.goal.value]
        beaten_events = [f"{lvl} Beaten" for lvl in LEVELS if lvl != goal_level]

        for level in LEVELS:
            region = Region(level, self.player, self.multiworld)
            region.locations += [
                HexagonLocation(self.player, loc_name, loc_data.location_id, region)
                for loc_name, loc_data in location_data_table.items()
                if loc_data.region == level and loc_data.time <= self.options.survival_time.value
            ]
            self.multiworld.regions.append(region)

            if level != goal_level:
                event = HexagonLocation(self.player, f"{level} Beaten", None, region)
                event.place_locked_item(HexagonItem(f"{level} Beaten", ItemClassification.progression, None, self.player))
                region.locations.append(event)

            if level == "Hexagon":
                self.create_entrance(menu, region)
            elif level == goal_level: # Goal level is not accessible until we are in go mode
                self.create_entrance(menu, region, Has(f"{level} Unlock")
                                     & Has("Hexagon Fragment", count=FromOption(HexagonFragments))
                                     & HasFromListUnique(*beaten_events, count=FromOption(LevelsBeatRequired)))
            else:
                self.create_entrance(menu, region, Has(f"{level} Unlock"))

    def set_rules(self) -> None:
        goal_level = GOAL_LEVELS[self.options.goal.value]
        self.set_completion_rule(CanReachRegion(goal_level))

    def get_filler_item_name(self) -> str:
        return "H E X A G O N"

    def fill_slot_data(self) -> dict:
        return {
            "goal": int(self.options.goal),
            "survival_time": int(self.options.survival_time),
            "levels_beat_required": int(self.options.levels_beat_required),
            "hexagon_fragments": int(self.options.hexagon_fragments),
            "death_link": int(self.options.death_link),
            "death_link_amnesty": int(self.options.death_link_amnesty)
        }
