from __future__ import annotations

import asyncio
import ctypes
import struct
from typing import Dict, List, Optional

import pymem
from pymem.ressources.kernel32 import VirtualProtectEx

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

LEVEL_SELECT_POLL = 1 / 60  # about once per fram

# Memory addresses

PTR_GAME_OBJECT = 0x15E8EC

ADDR_CURRENT_LEVEL: int = 0x54F4
ADDR_GAME_STATE: int = 0x4C
ADDR_CURRENT_TIME: int = 0x2924 
ADDR_DEATH_FLAG: float = 0x2988


# Text Memory Addresses
# Each is a std::string in the game object: pointer to the characters, length at +0x10, capacity at +0x14
ADDR_MENU_TEXT: int = 0x56CC          # main menu "PRESS SPACE TO SELECT"
ADDR_LEVEL_SELECT_TEXT: int = 0x5564  # level select "PRESS SPACE TO START"
TEXT_LEN_OFFSET: int = 0x10
TEXT_CAP_OFFSET: int = 0x14

MENU_TEXT: bytes = b"CONNECT TO ARCHIPELAGO"
LEVEL_SELECT_TEXT: bytes = b"PRESS SPACE TO START"
LEVEL_SELECT_LOCKED_TEXT: bytes = b"LOCKED"

# Level lock patch: when space is pressed, jump to our routine, which skips locked levels
PATCH_SITE: int = 0x24EC2
PATCH_SITE_ORIGINAL: bytes = bytes.fromhex("8b87f4540000")
PATCH_HOOK: bytes = bytes.fromhex("e901ab0c0090")  # jump to our routine
PATCH_ROUTINE_ADDR: int = 0xEF9C8  # unused space in the game's code
PATCH_ROUTINE: bytes = bytes.fromhex("8b87f4540000683f0000000fa304248d6424040f82b955f3ffe9e254f3ff")
PATCH_MASK_ADDR: int = PATCH_ROUTINE_ADDR + 7  # lock mask inside the routine
PAGE_EXECUTE_READWRITE: int = 0x40
ALL_LEVELS_MASK: int = 0b111111  # one bit per level, 1 = locked

# Native hyper unlock flags, keyed by menu index. The game's own lock still applies to hyper levels,
# so these are set to 1 when the matching unlock item is received.
HYPER_UNLOCK_FLAGS: Dict[int, int] = {
    3: 0x54C0,  # Hyper Hexagon
    2: 0x54C4,  # Hyper Hexagoner
    1: 0x54C8,  # Hyper Hexagonest
}

# Best time per level, in seconds (float32 each)
ADDR_BEST_TIMES: List[int] = [
    0x2998,  # Hexagon
    0x299C,  # Hexagoner
    0x29A0,  # Hexagonest
    0x29A4,  # Hyper Hexagon
    0x29A8,  # Hyper Hexagoner
    0x29AC,  # Hyper Hexagonest
]

# Mappings

LEVEL_NAME_TO_INDEX: Dict[str, int] = {level: i for i, level in enumerate(LEVELS)}

# These are mapped to the in game indexes
UNLOCK_ITEM_TO_LEVEL_INDEX: Dict[str, int] = {
    "Hexagon Unlock":          0,
    "Hexagoner Unlock":        5,
    "Hexagonest Unlock":       4,
    "Hyper Hexagon Unlock":    3,
    "Hyper Hexagoner Unlock":  2,
    "Hyper Hexagonest Unlock": 1,
}

# Status Command

class SuperHexagonCommandProcessor(ClientCommandProcessor):
    def _cmd_status(self):
        """Show current game connection status."""
        if isinstance(self.ctx, SuperHexagonContext):
            if self.ctx.game_connected:
                logger.info("Connected to Super Hexagon.")
                logger.info(f"Best times: {self.ctx.read_best_times()}")
                logger.info(f"Current level: {self.ctx.read_current_level()}")
                logger.info(f"Current time: {self.ctx.read_current_time()}")
                logger.info(f"Death flag: {self.ctx.read_death_flag()}")
            else:
                logger.info("Not connected to Super Hexagon, is the game running?")
                
    def _cmd_reset_times(self):
        """Reset all best times to 0 by writing 0 to their memory addresses."""
        if isinstance(self.ctx, SuperHexagonContext):
            if self.ctx.game_connected:
                for addr in ADDR_BEST_TIMES:
                    self.ctx._write_uint32(self.ctx.ptr + addr, 0)
                logger.info("Reset best times.")
            else:
                logger.info("Not connected to Super Hexagon, is the game running?")

# Context

class SuperHexagonContext(CommonContext):
    command_processor = SuperHexagonCommandProcessor
    game = "Super Hexagon"
    items_handling = 0b111  # receive items from other worlds

    def __init__(self, server_address: str, password: str) -> None:
        super().__init__(server_address, password)
        self.mem: Optional[pymem.Pymem] = None  # pymem.Pymem instance
        self.game_connected: bool = False
        self.checked_this_session: set = set()
        self.applied_unlocks: set = set()
        self.slot_data: Dict = {}
        self.game_object_addr = None
        self.ptr = None
        self.level_lock_installed: bool = False
        self.lock_mask: Optional[int] = None  # mask currently written to the game

    # AP server callbacks

    async def server_auth(self, password_requested: bool = False) -> None:
        if password_requested and not self.password:
            await super().server_auth(password_requested)
        await self.get_username()
        await self.send_connect()

    def on_package(self, cmd: str, args: dict) -> None:
        if cmd == "Connected":
            self.slot_data = args.get("slot_data", {})
        # if cmd in {"Connected", "ReceivedItems"}:
        #     asyncio.create_task(self._apply_received_items())

    # Memory helpers

    def _read_uint8(self, address: int) -> int:
        return struct.unpack_from("B", self.mem.read_bytes(address, 1))[0]
    
    def _read_uint32(self, address: int) -> int:
        return struct.unpack_from("I", self.mem.read_bytes(address, 4))[0]

    def _read_float(self, address: int) -> float:
        return struct.unpack_from("f", self.mem.read_bytes(address, 4))[0]

    def _write_uint8(self, address: int, value: int) -> None:
        self.mem.write_bytes(address, struct.pack("B", value), 1)
        
    def _write_uint32(self, address: int, value: int) -> None:
        self.mem.write_bytes(address, struct.pack("I", value), 4)
        
    def _write_float(self, address: int, value: float) -> None:
        self.mem.write_bytes(address, struct.pack("f", value), 4)

    # Game-state readers

    def read_current_level(self) -> int:
        return self._read_uint8(self.ptr + ADDR_CURRENT_LEVEL)

    def read_game_state(self) -> int:
        return self._read_uint8(self.ptr + ADDR_GAME_STATE)

    def read_current_time(self) -> float:
        return self._read_float(self.ptr + ADDR_CURRENT_TIME)

    def read_best_times(self) -> List[float]:
        return [self._read_uint32(self.ptr + addr) / 60 for addr in ADDR_BEST_TIMES]

    def read_death_flag(self) -> bool:
        return self._read_float(self.ptr + ADDR_DEATH_FLAG) != 0

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

    # Text replacement

    def write_text(self, string_addr: int, text: bytes) -> None:
        """Replace a std::string's contents, if it isn't already set and the new text fits."""
        if self._read_uint32(self.ptr + string_addr + TEXT_CAP_OFFSET) < len(text):
            return
        chars_addr = self._read_uint32(self.ptr + string_addr)
        if self.mem.read_bytes(chars_addr, len(text) + 1) == text + b"\x00":
            return
        self.mem.write_bytes(chars_addr, text + b"\x00", len(text) + 1)
        self._write_uint32(self.ptr + string_addr + TEXT_LEN_OFFSET, len(text))

    # Level lock patch

    def _write_code(self, address: int, data: bytes) -> None:
        """Write into the game's code, temporarily making it writable."""
        old = ctypes.c_ulong(0)
        if not VirtualProtectEx(self.mem.process_handle, ctypes.c_void_p(address), len(data),
                                PAGE_EXECUTE_READWRITE, ctypes.byref(old)):
            raise OSError(f"VirtualProtectEx failed at {address:#x}")
        try:
            self.mem.write_bytes(address, data, len(data))
        finally:
            VirtualProtectEx(self.mem.process_handle, ctypes.c_void_p(address), len(data),
                             old.value, ctypes.byref(ctypes.c_ulong()))

    def install_level_lock(self) -> bool:
        """Install the level lock patch with every level locked. Returns False if the game doesn't match."""
        base = self.mem.process_base.lpBaseOfDll
        site = self.mem.read_bytes(base + PATCH_SITE, len(PATCH_HOOK))
        routine_area = self.mem.read_bytes(base + PATCH_ROUTINE_ADDR, len(PATCH_ROUTINE))

        # Only patch a game we recognise
        if not (site == PATCH_SITE_ORIGINAL and routine_area == bytes(len(PATCH_ROUTINE))) and site != PATCH_HOOK:
            logger.warning("Unrecognised Super Hexagon version, level locking is disabled.")
            return False

        self._write_code(base + PATCH_ROUTINE_ADDR, PATCH_ROUTINE)
        self._write_code(base + PATCH_SITE, PATCH_HOOK)
        self.lock_mask = ALL_LEVELS_MASK
        return True

    def uninstall_level_lock(self) -> None:
        """Restore the original game code."""
        base = self.mem.process_base.lpBaseOfDll
        self._write_code(base + PATCH_SITE, PATCH_SITE_ORIGINAL)
        self._write_code(base + PATCH_ROUTINE_ADDR, bytes(len(PATCH_ROUTINE)))
        self.level_lock_installed = False
        self.lock_mask = None

    def unlocked_menu_indices(self) -> set:
        unlocked = set()
        for network_item in self.items_received:
            item_name = self.item_names.lookup_in_game(network_item.item)
            if item_name in UNLOCK_ITEM_TO_LEVEL_INDEX:
                unlocked.add(UNLOCK_ITEM_TO_LEVEL_INDEX[item_name])
        return unlocked

    def update_level_lock(self) -> None:
        """Lock every level whose unlock item hasn't been received (all of them until connected to a slot)."""
        unlocked = self.unlocked_menu_indices() if self.slot is not None else set()
        mask = ALL_LEVELS_MASK
        for index in unlocked:
            mask &= ~(1 << index)

        if mask != self.lock_mask:
            self._write_code(self.mem.process_base.lpBaseOfDll + PATCH_MASK_ADDR, struct.pack("<I", mask))
            self.lock_mask = mask

        # The game's own hyper lock would still block received hyper levels
        for index, flag_addr in HYPER_UNLOCK_FLAGS.items():
            if index in unlocked and self._read_uint32(self.ptr + flag_addr) != 1:
                self._write_uint32(self.ptr + flag_addr, 1)

    def is_level_locked(self, menu_index: int) -> bool:
        return self.lock_mask is not None and bool(self.lock_mask & (1 << menu_index))

    # Item application

    # async def _apply_received_items(self) -> None:
    #     """Write level-unlock bytes for every unlock item received from AP."""
    #     if not self.game_connected:
    #         return
    #     for network_item in self.items_received:
    #         item_name = self.item_names.lookup_in_game(network_item.item)
    #         level_idx = UNLOCK_ITEM_TO_LEVEL_INDEX.get(item_name)
    #         if level_idx is not None and level_idx not in self.applied_unlocks:
    #             try:
    #                 self._write_uint8(self.ptr + ADDR_HYPER_UNLOCKS[level_idx], 1)
    #                 self.applied_unlocks.add(level_idx)
    #                 logger.info(f"Applied unlock: {item_name}")
    #             except Exception as e:
    #                 logger.warning(f"Failed to write unlock for {item_name}: {e}")

    # Goal detection

    def is_goal_reached(self, best_times: List[float]) -> bool:
        goal = self.slot_data.get("goal", 0)
        # 0 = Hexagonest (index 2), 1 = Hyper Hexagonest (index 5)
        goal_level_idx = 2 if goal == 0 else 5
        return best_times[goal_level_idx] >= 60.0

# Game-watcher loop

async def game_watcher(ctx: SuperHexagonContext) -> None:
    import pymem

    while not ctx.exit_event.is_set():
        try:
            # Connection process
            if not ctx.game_connected:
                try:
                    ctx.mem = pymem.Pymem(PROCESS_NAME)
                    ctx.game_connected = True
                    ctx.game_object_addr = ctx.mem.process_base.lpBaseOfDll + PTR_GAME_OBJECT if ctx.mem else None
                    ctx.ptr = struct.unpack_from("I", ctx.mem.read_bytes(ctx.mem.process_base.lpBaseOfDll + PTR_GAME_OBJECT, 4))[0]
                    ctx.applied_unlocks.clear()
                    ctx.level_lock_installed = ctx.install_level_lock()
                    logger.info("Connected to Super Hexagon.")
                    
                    # Re-apply all already-received unlocks on fresh hook
                    # await ctx._apply_received_items()
                except pymem.exception.ProcessNotFound:
                    await asyncio.sleep(3)
                    continue

            ctx.ptr = ctx._read_uint32(ctx.mem.process_base.lpBaseOfDll + PTR_GAME_OBJECT)
            if not ctx.ptr:
                await asyncio.sleep(0.5)
                continue

            if ctx.level_lock_installed:
                ctx.update_level_lock()

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
                logger.info("Goal completed!")

        except (pymem.exception.MemoryReadError, pymem.exception.ProcessNotFound, Exception) as e:
            if ctx.game_connected:
                logger.warning(f"Lost connection to Super Hexagon: {e}")
                ctx.game_connected = False
                ctx.mem = None

        await asyncio.sleep(0.1)

async def level_select_watcher(ctx: SuperHexagonContext) -> None:
    """Fast loop for menu handling: text replacement and skipping locked levels."""
    while not ctx.exit_event.is_set():
        try:
            if ctx.game_connected and ctx.ptr:
                ctx.write_text(ADDR_MENU_TEXT, MENU_TEXT)

                if ctx.read_game_state() == 1:  # In level select
                    locked = ctx.is_level_locked(ctx.read_current_level())
                    ctx.write_text(ADDR_LEVEL_SELECT_TEXT, LEVEL_SELECT_LOCKED_TEXT if locked else LEVEL_SELECT_TEXT)

        except Exception as e:
            logger.warning(f"Level select watcher: {e}")

        await asyncio.sleep(LEVEL_SELECT_POLL)


# Entry point

def launch(*args: str) -> None:
    async def main(parsed_args) -> None:
        ctx = SuperHexagonContext(parsed_args.connect, parsed_args.password)
        ctx.server_task = asyncio.create_task(server_loop(ctx), name="server loop")
        if gui_enabled:
            ctx.run_gui()
        ctx.run_cli()
        watcher = asyncio.create_task(game_watcher(ctx), name="SuperHexagonWatcher")
        menu_watcher = asyncio.create_task(level_select_watcher(ctx), name="SuperHexagonMenuWatcher")

        await ctx.exit_event.wait()
        ctx.server_address = None
        await watcher
        await menu_watcher

        # Leave the game playable after the client closes
        if ctx.game_connected and ctx.level_lock_installed:
            try:
                ctx.uninstall_level_lock()
            except Exception as e:
                logger.warning(f"Could not remove level lock: {e}")
        await ctx.shutdown()

    parser = get_base_parser(description="Super Hexagon Archipelago Client")
    asyncio.run(main(parser.parse_args(args)))
