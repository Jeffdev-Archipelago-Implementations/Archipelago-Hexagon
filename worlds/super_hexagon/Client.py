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
from CommonClient import get_base_parser, gui_enabled, logger, server_loop

# Add UT into the client if its available
tracker_loaded: bool = False
try:
    from worlds.tracker.TrackerClient import ( # type: ignore
        TrackerCommandProcessor as ClientCommandProcessor,
        TrackerGameContext as CommonContext,
        UT_VERSION,
    )
    tracker_loaded = True
except ImportError:
    from CommonClient import ClientCommandProcessor, CommonContext

from .items import item_table
from .locations import GOAL_LEVELS, LEVELS, location_data_table, location_table

# Game process
PROCESS_NAME = "superhexagon.exe"

LEVEL_SELECT_POLL = 1 / 60  # about once per frame

# Memory addresses

PTR_GAME_OBJECT = 0x15E8EC

ADDR_CURRENT_LEVEL: int = 0x54F4
ADDR_GAME_STATE: int = 0x4C
ADDR_CURRENT_TIME: int = 0x2924 
ADDR_DEATH_FLAG: float = 0x2988

# Text Memory Addresses
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

# We use these to manually unlock these levels through the vanilla method, as we use our own locking system
HYPER_UNLOCK_FLAGS: Dict[int, int] = {
    3: 0x54C0,  # Hyper Hexagon
    2: 0x54C4,  # Hyper Hexagoner
    1: 0x54C8,  # Hyper Hexagonest
}

# Best times per level
FRAMES_PER_SECOND: int = 60
MAX_BEST_FRAMES: int = FRAMES_PER_SECOND * 60 * 60
ADDR_BEST_TIMES: Dict[str, int] = {
    "Hexagon":          0x2998,  # Hexagon
    "Hexagoner":        0x299C,  # Hexagoner
    "Hexagonest":       0x29A0,  # Hexagonest
    "Hyper Hexagon":    0x29A4,  # Hyper Hexagon
    "Hyper Hexagoner":  0x29A8,  # Hyper Hexagoner
    "Hyper Hexagonest": 0x29AC,  # Hyper Hexagonest
}

# Mappings

LEVEL_NAME_TO_INDEX: Dict[str, int] = {level: i for i, level in enumerate(LEVELS)}

# These are mapped to the in game indexes
UNLOCK_ITEM_TO_LEVEL_INDEX: Dict[str, int] = {
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

# Context

class SuperHexagonContext(CommonContext):
    command_processor = SuperHexagonCommandProcessor
    game = "Super Hexagon"
    items_handling = 0b111  # receive items from other worlds

    @property
    def best_times_key(self) -> str:
        return f"super_hexagon_best_times_{self.slot}"

    def __init__(self, server_address: str, password: str) -> None:
        super().__init__(server_address, password)
        self.mem: Optional[pymem.Pymem] = None  # pymem.Pymem instance
        self.game_connected: bool = False
        self.checked_this_session: set = set()
        self.applied_unlocks: set = set()
        self.slot_data: Dict = {}
        self.best_times_restored: bool = False  # stored times written into the game this hook
        self.best_times: Dict[str, float] = {}  # read once per game_watcher tick, empty until restored
        self.deathlink_applied: bool = False
        self.deathlink_counter: int = 0
        self.game_object_addr = None
        self.ptr = None
        self.level_lock_installed: bool = False
        self.lock_mask: Optional[int] = None  # mask currently written to the game
        self.text_too_long_warned: set = set()

    def make_gui(self):
        ui = super().make_gui()
        ui.base_title = "Super Hexagon Client"
        if tracker_loaded:
            ui.base_title += f" | Universal Tracker {UT_VERSION}"
        ui.base_title += " | Archipelago"
        return ui

    # AP server callbacks

    async def server_auth(self, password_requested: bool = False) -> None:
        if password_requested and not self.password:
            await super().server_auth(password_requested)
        await self.get_username()
        await self.send_connect()

    def on_package(self, cmd: str, args: dict) -> None:
        super().on_package(cmd, args)
        if cmd == "Connected":
            self.slot_data = args.get("slot_data", {})
            self.best_times_restored = False
            asyncio.create_task(self.send_msgs([{
                "cmd": "Set",
                "key": self.best_times_key,
                "default": {lvl: 0 for lvl in ADDR_BEST_TIMES},
                "want_reply": True,
                "operations": [{"operation": "default", "value": None}],
            }]))
            self.set_notify(self.best_times_key)
            asyncio.create_task(self.update_death_link(bool(self.slot_data.get("death_link", 0))))
        if cmd in {"Connected", "ReceivedItems"}:
            asyncio.create_task(self._apply_received_items())

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

    def read_best_frames(self) -> Dict[str, int]:
        return {lvl: self._read_uint32(self.ptr + addr) for lvl, addr in ADDR_BEST_TIMES.items()}

    def read_best_times(self) -> Dict[str, float]:
        return {lvl: frames / FRAMES_PER_SECOND for lvl, frames in self.read_best_frames().items()}

    def read_death_flag(self) -> bool:
        return self._read_float(self.ptr + ADDR_DEATH_FLAG) != 0

    # Location checking

    def collect_location_checks(self, best_times: Dict[str, float]) -> List[int]:
        """Return location IDs for every time milestone the player has reached that hasn't been sent yet."""
        return [data.location_id for data in location_data_table.values()
                if best_times[data.region] >= data.time and data.location_id not in self.checked_this_session]

    # Datastorage best time saving

    def stored_best_frames(self) -> Optional[Dict[str, int]]:
        """Validated best times from DataStorage, or None if they haven't arrived from the server yet."""
        if self.slot is None or self.best_times_key not in self.stored_data:
            return None
        raw = self.stored_data[self.best_times_key]
        if raw is None:  # key not created yet, the default Set's reply is still on its way
            return None
        if not isinstance(raw, dict):
            raw = {}
        # Missing or invalid levels count as 0
        valid: Dict[str, int] = {}
        for lvl in ADDR_BEST_TIMES:
            frames = raw.get(lvl)
            valid[lvl] = frames if type(frames) is int and 0 <= frames <= MAX_BEST_FRAMES else 0
        return valid

    def restore_best_times(self) -> None:
        """Make the game's best times match DataStorage, so a fresh slot starts every level at 0."""
        stored = self.stored_best_frames()
        if stored is None:
            return
        current = self.read_best_frames()
        for lvl, frames in stored.items():
            if frames != current[lvl]:
                self._write_uint32(self.ptr + ADDR_BEST_TIMES[lvl], frames)
        self.best_times_restored = True

    async def save_best_times(self) -> None:
        """Send any in-game best times that beat the stored ones."""
        stored = self.stored_best_frames()
        if stored is None:  # don't overwrite records we haven't seen yet
            return
        improved = {lvl: frames for lvl, frames in self.read_best_frames().items()
                    if 0 < frames <= MAX_BEST_FRAMES and frames > stored.get(lvl, 0)}
        if not improved:
            return
        await self.send_msgs([{
            "cmd": "Set",
            "key": self.best_times_key,
            "default": {},
            "want_reply": True,
            "operations": [{"operation": "update", "value": improved}],
        }])

    # Text replacement

    def write_text(self, string_addr: int, text: bytes) -> None:
        """Replace a std::string's contents, if it isn't already set and the new text fits."""
        capacity = self._read_uint32(self.ptr + string_addr + TEXT_CAP_OFFSET)
        if capacity < len(text):
            if text not in self.text_too_long_warned:
                self.text_too_long_warned.add(text)
                logger.warning(f"Text {text!r} is {len(text)} characters, only {capacity} fit, not shown.")
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
        unpatched = site == PATCH_SITE_ORIGINAL and routine_area == bytes(len(PATCH_ROUTINE))
        mask_start = PATCH_MASK_ADDR - PATCH_ROUTINE_ADDR
        already_patched = (site == PATCH_HOOK  # from a previous client session; the mask bytes may differ
                           and routine_area[:mask_start] == PATCH_ROUTINE[:mask_start]
                           and routine_area[mask_start + 4:] == PATCH_ROUTINE[mask_start + 4:])
        if not (unpatched or already_patched):
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

        if (self.fragments_received() < self.slot_data.get("hexagon_fragments", 0)
                or self.levels_beaten() < self.slot_data.get("levels_beat_required", 0)):
            unlocked.discard(self.goal_menu_index())
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

        for index, flag_addr in HYPER_UNLOCK_FLAGS.items():
            if index in unlocked and self._read_uint32(self.ptr + flag_addr) != 1:
                self._write_uint32(self.ptr + flag_addr, 1)

    def is_level_locked(self, menu_index: int) -> bool:
        return self.lock_mask is not None and bool(self.lock_mask & (1 << menu_index))

    def goal_menu_index(self) -> int:
        goal_level = GOAL_LEVELS[self.slot_data.get("goal", 0)]
        return UNLOCK_ITEM_TO_LEVEL_INDEX[f"{goal_level} Unlock"]

    def goal_locked_text(self) -> bytes:
        """Locked text for the goal level"""
        text = "GOAL:"
        fragments_needed = self.slot_data.get("hexagon_fragments", 0)
        if fragments_needed:
            fragments = self.fragments_received()
            text += f" {min(fragments, fragments_needed)}/{fragments_needed} FRAGS"
        beaten_needed = self.slot_data.get("levels_beat_required", 0)
        if beaten_needed:
            beaten = self.levels_beaten()
            text += f" {min(beaten, beaten_needed)}/{beaten_needed} LEVELS"
        return text.encode()

    # Item things

    async def _apply_received_items(self) -> None:
        """Write level-unlock bytes for every unlock item received from AP."""
        if not self.game_connected:
            return
        for network_item in self.items_received:
            item_name = self.item_names.lookup_in_game(network_item.item)
            level_idx = UNLOCK_ITEM_TO_LEVEL_INDEX.get(item_name)
            if level_idx is not None and level_idx not in self.applied_unlocks and self.level_lock_installed:
                try:
                    self.update_level_lock()
                except Exception as e:
                    logger.warning(f"Failed to write unlock for {item_name}: {e}")

    # Goal detection

    def fragments_received(self) -> int:
        return sum(1 for item in self.items_received
                    if self.item_names.lookup_in_game(item.item) == "Hexagon Fragment")

    def levels_beaten(self) -> int:
        goal_level = GOAL_LEVELS[self.slot_data.get("goal", 0)]
        survival_time = self.slot_data.get("survival_time", 60)
        return sum(1 for lvl, t in self.best_times.items() if lvl != goal_level and t >= survival_time)

    def is_goal_reached(self, best_times: Dict[str, float]) -> bool:
        goal_level = GOAL_LEVELS[self.slot_data.get("goal", 0)]
        return best_times[goal_level] >= self.slot_data.get("survival_time", 60.0)

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
                    ctx.best_times_restored = False
                    ctx.best_times = {}
                    ctx.level_lock_installed = ctx.install_level_lock()
                    logger.info("Connected to Super Hexagon. If the game closes, please restart this client.")
                    
                    await ctx._apply_received_items()
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

            # Sync best times with DataStorage
            if not ctx.best_times_restored:
                ctx.restore_best_times()
            if ctx.best_times_restored:
                await ctx.save_best_times()

            # Read game state
            best_times = ctx.read_best_times()
            ctx.best_times = best_times if ctx.best_times_restored else {}

            # DeathLink (currently only one way until I figure out how to make it apply to us)
            if "DeathLink" in ctx.tags:
                if ctx.read_death_flag() and ctx.deathlink_applied == False:
                    ctx.deathlink_counter += 1
                    if ctx.deathlink_counter >= ctx.slot_data.get("death_link_amnesty", 10):
                        await ctx.send_death(f"{ctx.player_names[ctx.slot]} crashed into a hexagon on {LEVELS[ctx.read_current_level()]}.")
                        ctx.deathlink_counter = 0
                    ctx.deathlink_applied = True
                elif ctx.read_death_flag() == False and ctx.deathlink_applied == True:
                    ctx.deathlink_applied = False

            # Send new location checks
            if ctx.best_times_restored:
                new_checks = ctx.collect_location_checks(best_times)
                if new_checks:
                    ctx.checked_this_session.update(await ctx.check_locations(new_checks))

            # Goal check, only once the game shows this slot's times, not whatever was saved locally before
            if ctx.best_times_restored and not ctx.finished_game and ctx.is_goal_reached(best_times):
                await ctx.send_msgs([{"cmd": "StatusUpdate", "status": ClientStatus.CLIENT_GOAL}])
                ctx.finished_game = True
                logger.info("Goal completed!")

        except (pymem.exception.MemoryReadError, pymem.exception.ProcessNotFound, Exception) as e:
            if ctx.game_connected:
                logger.warning(f"Error: {e}")
                ctx.game_connected = False
                ctx.mem = None

        await asyncio.sleep(0.1)

async def level_select_watcher(ctx: SuperHexagonContext) -> None:
    """Fast loop for text replacement."""
    while not ctx.exit_event.is_set():
        try:
            if ctx.game_connected and ctx.ptr:
                ctx.write_text(ADDR_MENU_TEXT, MENU_TEXT)

                if ctx.read_game_state() == 1:  # In level select
                    level = ctx.read_current_level()
                    locked = ctx.is_level_locked(level)
                    if not locked:
                        text = LEVEL_SELECT_TEXT
                    elif ctx.slot is not None and level == ctx.goal_menu_index():
                        text = ctx.goal_locked_text()
                    else:
                        text = LEVEL_SELECT_LOCKED_TEXT
                    ctx.write_text(ADDR_LEVEL_SELECT_TEXT, text)

        except Exception as e:
            logger.warning(f"Level select watcher: {e}")

        await asyncio.sleep(LEVEL_SELECT_POLL)


# Entry point

def launch(*args: str) -> None:
    async def main(parsed_args) -> None:
        ctx = SuperHexagonContext(parsed_args.connect, parsed_args.password)
        if tracker_loaded:
            ctx.run_generator()
            ctx.tags.discard("Tracker")
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
