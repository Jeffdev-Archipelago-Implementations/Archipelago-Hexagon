import logging

from BaseClasses import ItemClassification, Region, Tutorial
from rule_builder.field_resolvers import FromOption
from rule_builder.rules import CanReachRegion, Has, HasFromListUnique
from worlds.AutoWorld import WebWorld, World
from worlds.LauncherComponents import Component, Type, components, icon_paths, launch as launch_component

from .items import HexagonItem, item_data_table, item_table
from .locations import (LEVELS, OPTION_LEVELS, SURVIVAL_TIME_KEYS, HexagonLocation, location_data_table,
                        location_table)
from .options import (HexagonFragments, LevelsBeatRequired, SuperHexagonOptions,
                      hexagon_option_groups)


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
    option_groups = hexagon_option_groups


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

    def survival_time(self, level: str) -> int:
        return getattr(self.options, SURVIVAL_TIME_KEYS[level]).value

    def generate_early(self) -> None:
        slot_data = getattr(self.multiworld, "re_gen_passthrough", {}).get(self.game)
        if slot_data:
            for key, value in slot_data.items():
                if hasattr(self.options, key):
                    getattr(self.options, key).value = set(value) if key == "disable_hyper_levels" else value

        # Round to a multiple of 10
        for key in SURVIVAL_TIME_KEYS.values():
            option = getattr(self.options, key)
            option.value = round(option.value, -1)
        self.options.goal_time.value = round(self.options.goal_time.value, -1)

        self.start_level = OPTION_LEVELS[self.options.starting_level.value]
        self.goal_level = OPTION_LEVELS[self.options.goal.value]
        if self.goal_level == self.start_level:
            # Move the goal ahead to the next available (not disabled) level, or back to the previous one if there are none ahead
            goal_index = self.options.goal.value
            disabled = self.options.disable_hyper_levels.value
            ahead = [lvl for lvl in OPTION_LEVELS[goal_index + 1:] if lvl not in disabled]
            behind = [lvl for lvl in OPTION_LEVELS[:goal_index] if lvl not in disabled]
            new_goal = ahead[0] if ahead else behind[-1]
            logging.warning(f"Super Hexagon: {self.player_name}'s goal and starting level are both {self.goal_level}, "
                            f"moving goal to {new_goal}.")
            self.goal_level = new_goal
            self.options.goal.value = OPTION_LEVELS.index(new_goal)

        # The goal and starting levels can never be disabled
        self.options.disable_hyper_levels.value -= {self.goal_level, self.start_level}
        self.enabled_levels = [lvl for lvl in LEVELS if lvl not in self.options.disable_hyper_levels.value]

        # Can't require beating more levels than exist (excluding the goal)
        self.options.levels_beat_required.value = min(self.options.levels_beat_required.value,
                                                      len(self.enabled_levels) - 1)

        # Make sure the unlocks and fragments fit into the available locations
        location_count = sum(self.survival_time(lvl) // 10 for lvl in self.enabled_levels)
        unlock_count = len(self.enabled_levels) - 1
        max_fragments = location_count - unlock_count
        if self.options.hexagon_fragments.value > max_fragments:
            logging.warning(f"Super Hexagon: {self.player_name} has more Hexagon Fragments than there are locations, "
                            f"reducing to {max_fragments}.")
            self.options.hexagon_fragments.value = max_fragments

    def create_items(self) -> None:
        # The starting level is always open, so it doesn't need an unlock
        pool = [self.create_item(f"{lvl} Unlock") for lvl in self.enabled_levels if lvl != self.start_level]

        for _ in range(self.options.hexagon_fragments.value):
            pool.append(self.create_item("Hexagon Fragment"))

        filler_count = len(self.multiworld.get_unfilled_locations(self.player)) - len(pool)
        pool += [self.create_item(self.get_filler_item_name()) for _ in range(filler_count)]
        self.multiworld.itempool += pool

    def create_regions(self) -> None:
        menu = Region("Menu", self.player, self.multiworld)
        self.multiworld.regions.append(menu)
        beaten_events = [f"{lvl} Beaten" for lvl in self.enabled_levels if lvl != self.goal_level]

        for level in self.enabled_levels:
            region = Region(level, self.player, self.multiworld)
            region.locations += [
                HexagonLocation(self.player, loc_name, loc_data.location_id, region)
                for loc_name, loc_data in location_data_table.items()
                if loc_data.region == level and loc_data.time <= self.survival_time(level)
            ]
            self.multiworld.regions.append(region)

            if level != self.goal_level:
                event = HexagonLocation(self.player, f"{level} Beaten", None, region)
                event.place_locked_item(HexagonItem(f"{level} Beaten", ItemClassification.progression, None, self.player))
                region.locations.append(event)

            if level == self.start_level:
                self.create_entrance(menu, region)
            elif level == self.goal_level: # Goal level is not accessible until we are in go mode
                self.create_entrance(menu, region, Has(f"{level} Unlock")
                                     & Has("Hexagon Fragment", count=FromOption(HexagonFragments))
                                     & HasFromListUnique(*beaten_events, count=FromOption(LevelsBeatRequired)))
            else:
                self.create_entrance(menu, region, Has(f"{level} Unlock"))

    def set_rules(self) -> None:
        self.set_completion_rule(CanReachRegion(self.goal_level))

    def get_filler_item_name(self) -> str:
        return "H E X A G O N"

    def fill_slot_data(self) -> dict:
        slot_data = self.options.as_dict(
            "goal",
            "goal_time",
            "starting_level",
            *SURVIVAL_TIME_KEYS.values(),
            "levels_beat_required",
            "hexagon_fragments",
            "death_link",
            "death_link_amnesty",
        )
        slot_data["disable_hyper_levels"] = sorted(self.options.disable_hyper_levels.value)
        return slot_data
