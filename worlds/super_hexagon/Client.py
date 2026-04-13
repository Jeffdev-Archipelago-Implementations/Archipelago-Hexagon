from __future__ import annotations

import asyncio
import struct
import time
from typing import Dict, List, Optional

import ModuleUpdate
ModuleUpdate.update()

import Utils

if __name__ == "__main__":
    Utils.init_logging("SuperHexagonClient", exception_logger="Client")

from NetUtils import ClientStatus
from CommonClient import (
    ClientCommandProcessor, CommonContext, get_base_parser,
    gui_enabled, logger, server_loop,
)

from .items import item_table
from .locations import LEVELS, TIMES, location_table

# Game process
PROCESS_NAME = "superhexagon.exe"

# Memory addresses — TODO: fill in after reverse engineering / Cheat Engine scan, may be different

# Index of the level currently selected/played (uint8, 0–5)
# 0=Hexagon, 1=Hexagoner, 2=Hexagonest, 3=Hyper Hexagon, 4=Hyper Hexagoner, 5=Hyper Hexagonest
ADDR_CURRENT_LEVEL: int = 0x00000000  # TODO

# Whether the player is currently in an active game session (uint8, 1=playing)
ADDR_IN_GAME: int = 0x00000000  # TODO

# Time survived in the current session, in seconds (float32)
ADDR_CURRENT_TIME: float = 0x00000000  # TODO

# Best time per level, in seconds (float32 each).
# Index order matches LEVELS list.
ADDR_BEST_TIMES: List[int] = [
    0x00000000,  # Hexagon        TODO
    0x00000000,  # Hexagoner      TODO
    0x00000000,  # Hexagonest     TODO
    0x00000000,  # Hyper Hexagon  TODO
    0x00000000,  # Hyper Hexagoner TODO
    0x00000000,  # Hyper Hexagonest TODO
]

# Level-unlock flag per level (uint8, 1=unlocked).
# Writing 1 here should make the game treat that level as accessible.
ADDR_LEVEL_UNLOCKS: List[int] = [
    0x00000000,  # Hexagon          (always 1; Hexagon Unlock is precollected)  TODO
    0x00000000,  # Hexagoner Unlock  TODO
    0x00000000,  # Hexagonest Unlock TODO
    0x00000000,  # Hyper Hexagon Unlock     TODO
    0x00000000,  # Hyper Hexagoner Unlock   TODO
    0x00000000,  # Hyper Hexagonest Unlock  TODO
]


# Mappings

LEVEL_NAME_TO_INDEX: Dict[str, int] = {level: i for i, level in enumerate(LEVELS)}

UNLOCK_ITEM_TO_LEVEL_INDEX: Dict[str, int] = {
    "Hexagon Unlock":          0,
    "Hexagoner Unlock":        1,
    "Hexagonest Unlock":       2,
    "Hyper Hexagon Unlock":    3,
    "Hyper Hexagoner Unlock":  4,
    "Hyper Hexagonest Unlock": 5,
}

# Command processor

class SuperHexagonCommandProcessor(ClientCommandProcessor):
    def _cmd_status(self):
        """Show current game connection status."""
        if isinstance(self.ctx, SuperHexagonContext):
            if self.ctx.game_connected:
                logger.info("Connected to Super Hexagon.")
            else:
                logger.info("Not connected to Super Hexagon — is the game running?")

    def _cmd_resync(self):
        """Resend all pending location checks and re-apply all received items."""
        if isinstance(self.ctx, SuperHexagonContext):
            logger.info("Resyncing…")
            self.ctx.checked_this_session.clear()
            self.ctx.applied_unlocks.clear()

# Context

class SuperHexagonContext(CommonContext):
    command_processor = SuperHexagonCommandProcessor
    game = "Super Hexagon"
    items_handling = 0b111  # receive items from other worlds

    def __init__(self, server_address: str, password: str) -> None:
        super().__init__(server_address, password)
        self.mem: Optional[object] = None  # pymem.Pymem instance
        self.game_connected: bool = False

        # Location IDs we have already sent this session so we don't double-send
        self.checked_this_session: set = set()

        # Level indices whose unlock byte we have already written
        self.applied_unlocks: set = set()

        # Slot data from the server (contains "goal" and "levels_beat_required")
        self.slot_data: Dict = {}

    # AP server callbacks

    async def server_auth(self, password_requested: bool = False) -> None:
        if password_requested and not self.password:
            await super().server_auth(password_requested)
        await self.get_username()
        await self.send_connect()

    def on_package(self, cmd: str, args: dict) -> None:
        if cmd == "Connected":
            self.slot_data = args.get("slot_data", {})
        if cmd in {"Connected", "ReceivedItems"}:
            asyncio.create_task(self._apply_received_items())

    # Memory helpers

    def _read_uint8(self, address: int) -> int:
        return struct.unpack_from("B", self.mem.read_bytes(address, 1))[0]

    def _read_float(self, address: int) -> float:
        return struct.unpack_from("f", self.mem.read_bytes(address, 4))[0]

    def _write_uint8(self, address: int, value: int) -> None:
        self.mem.write_bytes(address, struct.pack("B", value), 1)

    # Game-state readers

    def read_current_level(self) -> int:
        return self._read_uint8(ADDR_CURRENT_LEVEL)

    def read_in_game(self) -> bool:
        return self._read_uint8(ADDR_IN_GAME) != 0

    def read_current_time(self) -> float:
        return self._read_float(ADDR_CURRENT_TIME)

    def read_best_times(self) -> List[float]:
        return [self._read_float(addr) for addr in ADDR_BEST_TIMES]

    def read_level_unlocks(self) -> List[bool]:
        return [self._read_uint8(addr) != 0 for addr in ADDR_LEVEL_UNLOCKS]

    # Location checking

    def collect_location_checks(self, best_times: List[float]) -> List[int]:
        """Return location IDs for every time milestone the player has reached."""
        to_send: List[int] = []
        for level_idx, level_name in enumerate(LEVELS):
            best = best_times[level_idx]
            for t in TIMES:
                if best >= t:
                    loc_name = f"{level_name}: {t} seconds"
                    loc_id = location_table.get(loc_name)
                    if loc_id is not None and loc_id not in self.checked_this_session:
                        to_send.append(loc_id)
                        self.checked_this_session.add(loc_id)
        return to_send

    # Item application

    async def _apply_received_items(self) -> None:
        """Write level-unlock bytes for every unlock item received from AP."""
        if not self.game_connected:
            return
        for network_item in self.items_received:
            item_name = self.item_names.lookup_in_game(network_item.item)
            level_idx = UNLOCK_ITEM_TO_LEVEL_INDEX.get(item_name)
            if level_idx is not None and level_idx not in self.applied_unlocks:
                try:
                    self._write_uint8(ADDR_LEVEL_UNLOCKS[level_idx], 1)
                    self.applied_unlocks.add(level_idx)
                    logger.info(f"Applied unlock: {item_name}")
                except Exception as e:
                    logger.warning(f"Failed to write unlock for {item_name}: {e}")

    # Goal detection

    def is_goal_reached(self, best_times: List[float]) -> bool:
        goal = self.slot_data.get("goal", 0)
        # 0 = Hexagonest (index 2), 1 = Hyper Hexagonest (index 5)
        goal_level_idx = 2 if goal == 0 else 5
        return best_times[goal_level_idx] >= 60.0

# Game-watcher loop

async def game_watcher(ctx: SuperHexagonContext) -> None:
    import pymem  # imported here to allow the client to load without pymem installed

    while not ctx.exit_event.is_set():
        try:
            # Connection process
            if not ctx.game_connected:
                try:
                    ctx.mem = pymem.Pymem(PROCESS_NAME)
                    ctx.game_connected = True
                    ctx.applied_unlocks.clear()
                    logger.info("Connected to Super Hexagon.")
                    # Re-apply all already-received unlocks on fresh hook
                    await ctx._apply_received_items()
                except pymem.exception.ProcessNotFound:
                    await asyncio.sleep(3)
                    continue

            if not ctx.server_address:
                await asyncio.sleep(1)
                continue

            # Read game state
            best_times = ctx.read_best_times()

            # Send new location checks
            new_checks = ctx.collect_location_checks(best_times)
            if new_checks:
                combined = list(ctx.checked_locations) + new_checks
                await ctx.send_msgs([{"cmd": "LocationChecks", "locations": new_checks}])

            # Goal check
            if not ctx.finished_game and ctx.is_goal_reached(best_times):
                await ctx.send_msgs([{"cmd": "StatusUpdate", "status": ClientStatus.CLIENT_GOAL}])
                ctx.finished_game = True
                logger.info("Goal reached — congratulations!")

        except (pymem.exception.MemoryReadError, pymem.exception.ProcessNotFound, Exception) as e:
            if ctx.game_connected:
                logger.warning(f"Lost connection to Super Hexagon: {e}")
                ctx.game_connected = False
                ctx.mem = None

        await asyncio.sleep(0.1)

# Entry point

def launch(*args: str) -> None:
    async def main(parsed_args) -> None:
        ctx = SuperHexagonContext(parsed_args.connect, parsed_args.password)
        ctx.server_task = asyncio.create_task(server_loop(ctx), name="server loop")
        if gui_enabled:
            ctx.run_gui()
        ctx.run_cli()
        watcher = asyncio.create_task(game_watcher(ctx), name="SuperHexagonWatcher")

        await ctx.exit_event.wait()
        ctx.server_address = None
        await watcher
        await ctx.shutdown()

    parser = get_base_parser(description="Super Hexagon Archipelago Client")
    asyncio.run(main(parser.parse_args(args)))
