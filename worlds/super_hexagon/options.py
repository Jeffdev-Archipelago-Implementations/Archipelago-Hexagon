from dataclasses import dataclass

from Options import Choice, PerGameCommonOptions, Range


class Goal(Choice):
    """The level you must survive 60 seconds in to complete your goal."""
    display_name = "Goal"
    option_hexagonest = 0
    option_hyper_hexagonest = 1
    default = 0


class LevelsBeatRequired(Range):
    """Number of levels you need to have beaten before unlocking your goal level.
    The goal level's own unlock counts toward this total.
    0 requires no levels beaten, allowing you to goal when you receive your goal level. 
    5 requires all other levels beaten before receiving your goal level."""
    display_name = "Levels Beat Required"
    range_start = 0
    range_end = 5
    default = 2


@dataclass
class SuperHexagonOptions(PerGameCommonOptions):
    goal: Goal
    levels_beat_required: LevelsBeatRequired
