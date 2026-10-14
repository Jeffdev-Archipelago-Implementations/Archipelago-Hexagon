from dataclasses import dataclass

from Options import Choice, PerGameCommonOptions, Range, DeathLink


class Goal(Choice):
    """
    The level you must survive in to complete your goal.
    Can be set to any level except Hexagon, as that is always the first level you start with.
    """
    display_name = "Goal"
    option_hexagoner = 0
    option_hexagonest = 1
    option_hyper_hexagon = 2
    option_hyper_hexagoner = 3
    option_hyper_hexagonest = 4
    default = 1
    
class SurvivalTime(Range):
    """
    The amount of time you are expected to survive in each level. This adjusts the check count, with every 10 seconds sending a check.
    Careful with this, as this game is pretty hard, and the Archipelago doesn't really help you survive longer!
    Your required goal time will be whatever this is set to.
    Will round to the nearest 10 seconds, if its not set to a multiple of 10.
    """
    display_name = "Survival Time"
    range_start = 30
    range_end = 180
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
    The number of fragments you need to collect to unlock the goal level.
    If set to 0, you will not need any hexagon fragments, and none will be added into the pool.
    """
    display_name = "Hexagon Fragments"
    range_start = 0
    range_end = 10
    default = 5


@dataclass
class SuperHexagonOptions(PerGameCommonOptions):
    goal: Goal
    survival_time: SurvivalTime
    levels_beat_required: LevelsBeatRequired
    hexagon_fragments: HexagonFragments
    death_link: DeathLink
