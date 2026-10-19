from dataclasses import dataclass

from Options import Choice, OptionGroup, OptionSet, PerGameCommonOptions, Range, DeathLink

## GOAL OPTIONS 

class Goal(Choice):
    """
    The level you must survive in to complete your goal.
    Cannot be the same as your starting level. If it is the same, the goal level will be changed to the next available level.
    """
    display_name = "Goal"
    option_hexagon = 0
    option_hexagoner = 1
    option_hexagonest = 2
    option_hyper_hexagon = 3
    option_hyper_hexagoner = 4
    option_hyper_hexagonest = 5
    default = 2
    
class GoalTime(Range):
    """
    The amount of time you are expected to survive in order to goal on your goal level.
    Will round to the nearest 10 seconds, if its not set to a multiple of 10.
    """
    display_name = "Goal Time"
    range_start = 30
    range_end = 240
    default = 60
    
## LEVEL OPTIONS

class StartingLevel(Choice):
    """
    The level you start on. This is the first level you will play, and the first level you will have to survive in.
    Cannot be the same as your goal level. If it is the same, your goal will be moved instead (see the Goal option).
    """
    display_name = "Starting Level"
    option_hexagon = 0
    option_hexagoner = 1
    option_hexagonest = 2
    option_hyper_hexagon = 3
    option_hyper_hexagoner = 4
    option_hyper_hexagonest = 5
    default = 0
    
class DisableHyperLevels(OptionSet):
    """
    Choose which Hyper levels to remove from the game. Disabled levels are fully removed from the game and not playable, and have no checks.
    A disabled level cannot be your goal or starting level. If a level is set as a goal or starting level, it will be removed from this list.
    Valid options: "Hyper Hexagon", "Hyper Hexagoner", "Hyper Hexagonest"
    """
    display_name = "Disable Hyper Levels"
    valid_keys = {"Hyper Hexagon", "Hyper Hexagoner", "Hyper Hexagonest"}
    default = {"Hyper Hexagonest"}

class HexagonSurvivalTime(Range):
    """
    The amount of time you are expected to survive the Hexagon level. This adjusts the check count, with every 10 seconds sending a check.
    Will round to the nearest 10 seconds, if its not set to a multiple of 10.
    """
    display_name = "Hexagon Survival Time"
    range_start = 30
    range_end = 240
    default = 60
    
class HexagonerSurvivalTime(Range):
    """
    The amount of time you are expected to survive the Hexagoner level. This adjusts the check count, with every 10 seconds sending a check.
    Will round to the nearest 10 seconds, if its not set to a multiple of 10.
    """
    display_name = "Hexagoner Survival Time"
    range_start = 30
    range_end = 240
    default = 60
    
class HexagonestSurvivalTime(Range):
    """
    The amount of time you are expected to survive the Hexagonest level. This adjusts the check count, with every 10 seconds sending a check.
    Will round to the nearest 10 seconds, if its not set to a multiple of 10.
    """
    display_name = "Hexagonest Survival Time"
    range_start = 30
    range_end = 240
    default = 60
    
class HyperHexagonSurvivalTime(Range):
    """
    The amount of time you are expected to survive the Hyper Hexagon level. This adjusts the check count, with every 10 seconds sending a check.
    Will round to the nearest 10 seconds, if its not set to a multiple of 10.
    This option is ignored if this level is disabled.
    """
    display_name = "Hyper Hexagon Survival Time"
    range_start = 30
    range_end = 240
    default = 60
    
class HyperHexagonerSurvivalTime(Range):
    """
    The amount of time you are expected to survive the Hyper Hexagoner level. This adjusts the check count, with every 10 seconds sending a check.
    Will round to the nearest 10 seconds, if its not set to a multiple of 10.
    This option is ignored if this level is disabled.
    """
    display_name = "Hyper Hexagoner Survival Time"
    range_start = 30
    range_end = 240
    default = 60
    
class HyperHexagonestSurvivalTime(Range):
    """
    The amount of time you are expected to survive the Hyper Hexagonest level. This adjusts the check count, with every 10 seconds sending a check.
    Will round to the nearest 10 seconds, if its not set to a multiple of 10.
    This option is ignored if this level is disabled.
    """
    display_name = "Hyper Hexagonest Survival Time"
    range_start = 30
    range_end = 240
    default = 60

class LevelsBeatRequired(Range):
    """
    Number of levels (excluding goal) you need to have beaten before unlocking your goal level.
    A level is beaten when you survive for your set maximum survival time.
    0 requires no levels beaten, allowing you to goal when you receive your goal level.
    """
    display_name = "Levels Beat Required"
    range_start = 0
    range_end = 5
    default = 2
    
class HexagonFragments(Range):
    """
    The number of fragment items you need to collect to unlock the goal level.
    If set to 0, you will not need any hexagon fragments, and none will be added into the pool.
    """
    display_name = "Hexagon Fragments"
    range_start = 0
    range_end = 10
    default = 5

class DeathlinkAmnesty(Range):
    """
    The amount of deaths it takes for a deathlink to count.
    Can be adjusted in the client after this is set.
    """
    display_name = "Deathlink Amnesty"
    range_start = 1
    range_end = 20
    default = 10

hexagon_option_groups = [
    OptionGroup("Goal Options", [
        Goal,
        GoalTime,
        HexagonFragments,
        LevelsBeatRequired,
    ]),
    OptionGroup("Level Options", [
        StartingLevel,
        DisableHyperLevels,
        HexagonSurvivalTime,
        HexagonerSurvivalTime,
        HexagonestSurvivalTime,
        HyperHexagonSurvivalTime,
        HyperHexagonerSurvivalTime,
        HyperHexagonestSurvivalTime,
    ]),
    OptionGroup("DeathLink Options", [
        DeathLink,
        DeathlinkAmnesty,
    ]),
]

@dataclass
class SuperHexagonOptions(PerGameCommonOptions):
    goal: Goal
    goal_time: GoalTime
    starting_level: StartingLevel
    disable_hyper_levels: DisableHyperLevels
    hexagon_survival_time: HexagonSurvivalTime
    hexagoner_survival_time: HexagonerSurvivalTime
    hexagonest_survival_time: HexagonestSurvivalTime
    hyper_hexagon_survival_time: HyperHexagonSurvivalTime
    hyper_hexagoner_survival_time: HyperHexagonerSurvivalTime
    hyper_hexagonest_survival_time: HyperHexagonestSurvivalTime
    levels_beat_required: LevelsBeatRequired
    hexagon_fragments: HexagonFragments
    death_link: DeathLink
    death_link_amnesty: DeathlinkAmnesty