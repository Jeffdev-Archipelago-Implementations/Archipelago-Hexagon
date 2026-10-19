from __future__ import annotations

import asyncio
import ctypes
import struct
from typing import Dict, List, Optional, Tuple

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
from .locations import LEVELS, OPTION_LEVELS, SURVIVAL_TIME_KEYS, location_data_table, location_table

# Game process
PROCESS_NAME = "superhexagon.exe"

LEVEL_SELECT_POLL = 1 / 60  # about once per frame

# Memory addresses

PTR_GAME_OBJECT = 0x15E8EC

ADDR_CURRENT_LEVEL: int = 0x54F4
ADDR_GAME_STATE: int = 0x4C
ADDR_CURRENT_TIME: int = 0x2924 
ADDR_DEATH_FLAG: float = 0x2988
ADDR_FIRST_TIME_FLAG = 0x1B4B0

# Text Memory Addresses
ADDR_MENU_TEXT: int = 0x56CC          # main menu "PRESS SPACE TO SELECT"
ADDR_LEVEL_SELECT_TEXT: int = 0x5564  # level select "PRESS SPACE TO START"
TEXT_LEN_OFFSET: int = 0x10
TEXT_CAP_OFFSET: int = 0x14
SSO_CAPACITY: int = 16  # strings with a smaller capacity are stored inline

MENU_TEXT: bytes = b"NOT CONNECTED TO ARCHIPELAGO"
MENU_TEXT_CONNECTED: bytes = b"CONNECTED TO ARCHIPELAGO"
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

# Save lock patch
SAVE_FUNCS: List[int] = [
    0x1B550,  # scores.dat
    0x1B6D0,  # suphex.dat
]
SAVE_FUNC_ORIGINAL: bytes = bytes.fromhex("55")  # push ebp
SAVE_FUNC_PATCH: bytes = bytes.fromhex("c3")  # ret

# Death collision patch
DEATH_HOOK_SITE: int = 0x29B31
DEATH_HOOK_ORIGINAL: bytes = bytes.fromhex("f30f108788290000")  # movss xmm0, [edi+0x2988]
DEATH_HOOK: bytes = bytes.fromhex("e9b25e0c00909090")  # jump to our routine
DEATH_ROUTINE_ADDR: int = 0xEF9E8  # unused space after the level lock routine
DEATH_ROUTINE: bytes = bytes.fromhex("50b80000000085c058740ac787882900000000803ff30f108788290000e92fa1f3ff")
DEATH_KILL_FLAG_ADDR: int = DEATH_ROUTINE_ADDR + 2  # kill flag inside the routine
ADDR_DEATH_TIMER: int = 0x188  # nonzero from the moment of death until the game over screen

# Stage complete patch (skips unlocking stuff when you beat the normal levels)
STAGE_COMPLETE_PATCHES: List[Tuple[int, bytes, bytes]] = [  # site, original, patched
    (0x1B4D3, bytes.fromhex("7574"), bytes.fromhex("eb74")),  # normal mode: jne -> jmp past the unlock
    (0x1B518, bytes.fromhex("752f"), bytes.fromhex("eb2f")),  # hyper mode: jne -> jmp past the finish
    # Fix the ending bar text checking for hexagonest level, instead just check for the color scheme to display text properly
    # Otherwise the bar is just full white lol
    (0xB5AA, bytes.fromhex("83bba454000002"),   # cmp dword ptr [ebx + 0x54a4], 2   (stage)
             bytes.fromhex("83bbd42701001e")),  # cmp dword ptr [ebx + 0x127d4], 30 (colour scheme)
]

# Game over banner: "CONGRATULATIONS" with a second line picked by type
ADDR_COMPLETE_BANNER: int = 0x5750        # 0 none, 1 new hyper mode, 2 game complete, 3 hyper level complete
ADDR_COMPLETE_BANNER_STAGE: int = 0x5754  # stage the banner is for (0-2)
BANNER_GAME_COMPLETE: int = 2
ADDR_ENDING_FLAG: int = 0x54FC  # 1 plays the ending cutscene when the current run ends, 2 while it plays
ENDING_PLAYING: int = 2

# Hyper things

HYPER_UNLOCK_FLAGS: Dict[int, int] = {
    3: 0x54C0,  # Hyper Hexagon
    2: 0x54C4,  # Hyper Hexagoner
    1: 0x54C8,  # Hyper Hexagonest
}

# The game's own hyper lock text
HYPER_LOCK_TEXT: Dict[int, Tuple[int, bytes]] = {
    3: (0xF1550, b"COMPLETE HEXAGON TO UNLOCK"),     # Hyper Hexagon
    2: (0xF156C, b"COMPLETE HEXAGONER TO UNLOCK"),   # Hyper Hexagoner
    1: (0xF158C, b"COMPLETE HEXAGONEST TO UNLOCK"),  # Hyper Hexagonest
}
HYPER_DISABLED_TEXT: Dict[int, Tuple[int, bytes]] = {
    3: (0xF1550, b"HYPER HEXAGON DISABLED"), 
    2: (0xF156C, b"HYPER HEXAGONER DISABLED"),
    1: (0xF158C, b"HYPER HEXAGONEST DISABLED"),
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
UNLOCK_ITEM_TO_LEVEL_INDEX: Dict[str, int] = {f"{level} Unlock": i for i, level in enumerate(LEVELS)}

# Status Command

class SuperHexagonCommandProcessor(ClientCommandProcessor):
    def _cmd_status(self):
        """Show current game connection status."""
        if isinstance(self.ctx, SuperHexagonContext):
            if self.ctx.game_connected:
                logger.info("Connected to Super Hexagon.")
            else:
                logger.info("Not connected to Super Hexagon, is the game running?")
                
    def _cmd_deathlink(self):
        """Toggle Deathlink on or off."""
        if isinstance(self.ctx, SuperHexagonContext):
            if "DeathLink" in self.ctx.tags:
                asyncio.create_task(self.ctx.update_death_link(False))
            else:
                asyncio.create_task(self.ctx.update_death_link(True))
            logger.info(f"Deathlink {'enabled' if "DeathLink" not in self.ctx.tags else 'disabled'}.")
            
    def _cmd_deathlink_amnesty(self, value):
        """Set the number of deaths it takes for a deathlink to send."""
        if isinstance(self.ctx, SuperHexagonContext):
            try:
                if int(value) > 20:
                    logger.warning("Deathlink Amnesty cannot be set higher than 20.")
                    return
                self.ctx.deathlink_amnesty = int(value)
                logger.info(f"Deathlink Amnesty set to {value}.")
            except ValueError:
                logger.warning("Invalid value for Deathlink Amnesty. Please enter a valid integer.")

    def _cmd_survival_times(self):
        """Show the expected survival time for each level."""
        if isinstance(self.ctx, SuperHexagonContext):
            if self.ctx.slot is None:
                logger.info("Connect to a slot to see your survival times.")
                return
            disabled = self.ctx.slot_data.get("disable_hyper_levels", [])
            lines = []
            for level in OPTION_LEVELS:
                if level in disabled:
                    lines.append(f"{level}: Disabled")
                    continue
                line = f"{level}: {self.ctx.survival_time(level)}s"
                if level == self.ctx.goal_level:
                    line += f" (Goal Time: {self.ctx.slot_data.get('goal_time', 60)}s)"
                lines.append(line)
            logger.info("Survival times:\n" + "\n".join(lines))

    def _cmd_goal(self):
        """Show your goal time, if your goal level is available, and your fragments/levels beaten."""
        if isinstance(self.ctx, SuperHexagonContext):
            if self.ctx.slot is None:
                logger.info("Connect to a slot to see your goal.")
                return
            ctx = self.ctx
            goal = ctx.goal_level
            has_unlock = any(ctx.item_names.lookup_in_game(item.item) == f"{goal} Unlock"
                             for item in ctx.items_received)
            fragments_needed = ctx.slot_data.get("hexagon_fragments", 0)
            beaten_needed = ctx.slot_data.get("levels_beat_required", 0)
            lines = [
                f"Goal: Survive {ctx.slot_data.get('goal_time', 60)}s in {goal}",
                f"{goal} Unlock: {'Received' if has_unlock else 'Not received'}",
            ]
            if fragments_needed:
                lines.append(f"Hexagon Fragments: {ctx.fragments_received()}/{fragments_needed}")
            if beaten_needed:
                beaten = f"{ctx.levels_beaten()}/{beaten_needed}"
                if not ctx.best_times:
                    beaten += " (connect the game to see your times)"
                lines.append(f"Levels beaten: {beaten}")
            logger.info("\n".join(lines))

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
        self.deathlink_amnesty: int = 10
        self.death_hook_installed: bool = False
        self.stage_complete_patched: bool = False
        self.game_complete_started: bool = False  # banner and ending set up for the goal run
        self.kill_flag_set: bool = False
        self.was_dying: bool = False
        self.game_object_addr = None
        self.ptr = None
        self.level_lock_installed: bool = False
        self.lock_mask: Optional[int] = None  # mask currently written to the game
        self.disabled_text_written: set = set()  # hyper levels currently showing their disabled text
        self.text_too_long_warned: set = set()
        self.save_block_installed: bool = False
        self.original_save_values: Optional[Dict[int, int]] = None  # saved fields we overwrite, restored on exit
        self.unsupported_warned: bool = False
        self.save_block_pid: Optional[int] = None  # game process the save block was installed into
        self.save_pending: bool = False  # a run ended, send any new records once the game over screen shows

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
            self.deathlink_amnesty = self.slot_data.get("death_link_amnesty", 10)
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

    def is_dying(self) -> bool:
        return self.read_game_state() == 0 and self._read_float(self.ptr + ADDR_DEATH_TIMER) != 0

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
            return
        # MSVC std::string keeps short strings inline instead of on the heap
        if capacity < SSO_CAPACITY:
            chars_addr = self.ptr + string_addr
        else:
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
        self.disabled_text_written.clear()
        return True

    def uninstall_level_lock(self) -> None:
        """Restore the original game code."""
        base = self.mem.process_base.lpBaseOfDll
        self._write_code(base + PATCH_SITE, PATCH_SITE_ORIGINAL)
        self._write_code(base + PATCH_ROUTINE_ADDR, bytes(len(PATCH_ROUTINE)))
        for index in self.disabled_text_written:
            addr, original = HYPER_LOCK_TEXT[index]
            self._write_code(base + addr, original + b"\x00")
        self.disabled_text_written.clear()
        self.level_lock_installed = False
        self.lock_mask = None

    # Save block patch

    def install_save_block(self) -> bool:
        """Stop the game from saving. Returns False if the game doesn't match."""
        base = self.mem.process_base.lpBaseOfDll
        sites = [self.mem.read_bytes(base + func, len(SAVE_FUNC_PATCH)) for func in SAVE_FUNCS]
        if any(site not in (SAVE_FUNC_ORIGINAL, SAVE_FUNC_PATCH) for site in sites):
            return False
        if SAVE_FUNC_PATCH in sites and self.save_block_pid == self.mem.process_id:
            pass
        elif SAVE_FUNC_PATCH in sites:
            # Left over from a client that didn't exit cleanly
            logger.warning("Super Hexagon was patched by a previous client session. "
                           "Restart the game after closing this client so your save isn't overwritten.")
            self.original_save_values = None
        else:
            self.original_save_values = {}  # captured once the game object exists
        for func in SAVE_FUNCS:
            self._write_code(base + func, SAVE_FUNC_PATCH)
        self.save_block_installed = True
        self.save_block_pid = self.mem.process_id
        return True

    def capture_save_values(self) -> None:
        """Remember the real values of every saved field the client overwrites."""
        if self.original_save_values == {}:
            offsets = list(ADDR_BEST_TIMES.values()) + list(HYPER_UNLOCK_FLAGS.values())
            self.original_save_values = {offset: self._read_uint32(self.ptr + offset) for offset in offsets}

    def uninstall_save_block(self) -> None:
        """Put the real values back, then let the game save again."""
        if not self.original_save_values:  # unknown originals, leave saving off until the game restarts
            logger.warning("Saving stays disabled until Super Hexagon is restarted.")
            return
        ptr = self._read_uint32(self.mem.process_base.lpBaseOfDll + PTR_GAME_OBJECT)
        for offset, value in self.original_save_values.items():
            self._write_uint32(ptr + offset, value)
        for func in SAVE_FUNCS:
            self._write_code(self.mem.process_base.lpBaseOfDll + func, SAVE_FUNC_ORIGINAL)
        self.save_block_installed = False

    # Death patch

    def install_death_hook(self) -> bool:
        """Install the death patch with the kill flag off. Returns False if the game doesn't match."""
        base = self.mem.process_base.lpBaseOfDll
        site = self.mem.read_bytes(base + DEATH_HOOK_SITE, len(DEATH_HOOK))
        routine_area = self.mem.read_bytes(base + DEATH_ROUTINE_ADDR, len(DEATH_ROUTINE))

        unpatched = site == DEATH_HOOK_ORIGINAL and routine_area == bytes(len(DEATH_ROUTINE))
        flag_start = DEATH_KILL_FLAG_ADDR - DEATH_ROUTINE_ADDR
        already_patched = (site == DEATH_HOOK 
                           and routine_area[:flag_start] == DEATH_ROUTINE[:flag_start]
                           and routine_area[flag_start + 4:] == DEATH_ROUTINE[flag_start + 4:])
        if not (unpatched or already_patched):
            logger.warning("Unrecognised Super Hexagon version, receiving DeathLinks is disabled.")
            return False

        self._write_code(base + DEATH_ROUTINE_ADDR, DEATH_ROUTINE)
        self._write_code(base + DEATH_HOOK_SITE, DEATH_HOOK)
        self.kill_flag_set = False
        return True

    def uninstall_death_hook(self) -> None:
        """Restore the original game code."""
        base = self.mem.process_base.lpBaseOfDll
        self._write_code(base + DEATH_HOOK_SITE, DEATH_HOOK_ORIGINAL)
        self._write_code(base + DEATH_ROUTINE_ADDR, bytes(len(DEATH_ROUTINE)))
        self.death_hook_installed = False
        self.kill_flag_set = False

    # Stage complete patch

    def install_stage_complete_patch(self) -> bool:
        """Stop the game unlocking hyper levels and showing its own banners. Returns False if the game doesn't match."""
        base = self.mem.process_base.lpBaseOfDll
        sites = [self.mem.read_bytes(base + site, len(original)) for site, original, _ in STAGE_COMPLETE_PATCHES]
        if any(current not in (original, patched)
               for current, (_, original, patched) in zip(sites, STAGE_COMPLETE_PATCHES)):
            logger.warning("Unrecognised Super Hexagon version, the game's own level complete banners stay on.")
            return False
        for site, _, patched in STAGE_COMPLETE_PATCHES:
            self._write_code(base + site, patched)
        return True

    def uninstall_stage_complete_patch(self) -> None:
        """Restore the original game code."""
        base = self.mem.process_base.lpBaseOfDll
        for site, original, _ in STAGE_COMPLETE_PATCHES:
            self._write_code(base + site, original)
        self.stage_complete_patched = False

    def start_game_complete(self) -> None:
        """Setup game completion cutscene once this run ends."""
        self._write_uint32(self.ptr + ADDR_COMPLETE_BANNER_STAGE, 2)
        self._write_uint32(self.ptr + ADDR_COMPLETE_BANNER, BANNER_GAME_COMPLETE)
        self._write_uint32(self.ptr + ADDR_ENDING_FLAG, 1)
        self.game_complete_started = True

    def goal_time_passed_this_run(self) -> bool:
        """In a run on the goal level and past the goal time."""
        return (self.read_game_state() == 0
                and LEVELS[self.read_current_level()] == self.goal_level
                and self.read_current_time() / FRAMES_PER_SECOND >= self.slot_data.get("goal_time", 60))

    def set_kill_flag(self, on: bool) -> None:
        self._write_code(self.mem.process_base.lpBaseOfDll + DEATH_KILL_FLAG_ADDR, struct.pack("<I", int(on)))
        self.kill_flag_set = on

    def unlocked_menu_indices(self) -> set:
        unlocked = set()
        unlocked.add(LEVEL_NAME_TO_INDEX[self.start_level]) # The starting level is always unlocked
        for network_item in self.items_received:
            item_name = self.item_names.lookup_in_game(network_item.item)
            if item_name in UNLOCK_ITEM_TO_LEVEL_INDEX:
                unlocked.add(UNLOCK_ITEM_TO_LEVEL_INDEX[item_name])

        if not self.goal_requirements_met():
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

        disabled = self.disabled_menu_indices() if self.slot is not None else set()
        for index, flag_addr in HYPER_UNLOCK_FLAGS.items():
            if index in disabled:
                # Use the game's own hyper lock for disabled levels
                if self._read_uint32(self.ptr + flag_addr) != 0:
                    self._write_uint32(self.ptr + flag_addr, 0)
            elif index in unlocked and self._read_uint32(self.ptr + flag_addr) != 1:
                self._write_uint32(self.ptr + flag_addr, 1)

        # Add in custom text for disabled levels
        base = self.mem.process_base.lpBaseOfDll
        for index, (addr, original) in HYPER_LOCK_TEXT.items():
            if index in disabled and index not in self.disabled_text_written:
                self._write_code(base + addr, HYPER_DISABLED_TEXT[index][1] + b"\x00")
                self.disabled_text_written.add(index)
            elif index not in disabled and index in self.disabled_text_written:
                self._write_code(base + addr, original + b"\x00")
                self.disabled_text_written.discard(index)

    def disabled_menu_indices(self) -> set:
        return {LEVEL_NAME_TO_INDEX[level] for level in self.slot_data.get("disable_hyper_levels", [])}

    def is_level_locked(self, menu_index: int) -> bool:
        return self.lock_mask is not None and bool(self.lock_mask & (1 << menu_index))

    @property
    def goal_level(self) -> str:
        return OPTION_LEVELS[self.slot_data.get("goal", 2)]

    @property
    def start_level(self) -> str:
        return OPTION_LEVELS[self.slot_data.get("starting_level", 0)]

    def survival_time(self, level: str) -> int:
        return self.slot_data.get(SURVIVAL_TIME_KEYS[level], 60)

    def goal_menu_index(self) -> int:
        return LEVEL_NAME_TO_INDEX[self.goal_level]

    def goal_locked_text(self) -> bytes:
        """Locked text for the goal level"""
        text = f"GOAL:"
        fragments_needed = self.slot_data.get("hexagon_fragments", 0)
        if fragments_needed:
            fragments = self.fragments_received()
            text += f" {min(fragments, fragments_needed)}/{fragments_needed} FRAGS"
        beaten_needed = self.slot_data.get("levels_beat_required", 0)
        if beaten_needed:
            beaten = self.levels_beaten()
            text += f" {min(beaten, beaten_needed)}/{beaten_needed} LEVELS"
        if text == "GOAL:":
            text += " NEEDS UNLOCK"
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
        disabled = self.slot_data.get("disable_hyper_levels", [])
        return sum(1 for lvl, t in self.best_times.items()
                   if lvl != self.goal_level and lvl not in disabled and t >= self.survival_time(lvl))

    def goal_requirements_met(self) -> bool:
        return (self.fragments_received() >= self.slot_data.get("hexagon_fragments", 0)
                and self.levels_beaten() >= self.slot_data.get("levels_beat_required", 0))

    def is_goal_reached(self, best_times: Dict[str, float]) -> bool:
        # The goal level stays locked until the requirements are met, so being able to play it is enough
        return best_times[self.goal_level] >= self.slot_data.get("goal_time", 60)
    
    # Deathlink
    def on_deathlink(self, data: dict) -> None:
        super().on_deathlink(data)
        if self.game_connected and self.ptr:
            try:
                if self.read_game_state() == 0:  # in game
                    self.kill_player()
            except Exception as e:
                logger.warning(f"Could not apply DeathLink: {e}")
    
    def kill_player(self) -> None:
        """Make the game think the player hit a wall, so it plays its own death sequence."""
        if not self.death_hook_installed:
            return
        self.deathlink_applied = True  # so game_watcher doesn't count or resend this death
        self.set_kill_flag(True)

# Game-watcher loop

async def game_watcher(ctx: SuperHexagonContext) -> None:
    import pymem

    while not ctx.exit_event.is_set():
        try:
            # Connection process
            if not ctx.game_connected:
                try:
                    ctx.mem = pymem.Pymem(PROCESS_NAME)
                    # Never touch the player's save, so don't hook a game we can't stop from saving
                    if not ctx.install_save_block():
                        if not ctx.unsupported_warned:
                            logger.error("Unrecognised Super Hexagon version.")
                            ctx.unsupported_warned = True
                        ctx.mem = None
                        await asyncio.sleep(3)
                        continue
                    ctx.game_connected = True
                    ctx.game_object_addr = ctx.mem.process_base.lpBaseOfDll + PTR_GAME_OBJECT if ctx.mem else None
                    ctx.ptr = struct.unpack_from("I", ctx.mem.read_bytes(ctx.mem.process_base.lpBaseOfDll + PTR_GAME_OBJECT, 4))[0]
                    ctx.applied_unlocks.clear()
                    ctx.best_times_restored = False
                    ctx.best_times = {}
                    ctx.level_lock_installed = ctx.install_level_lock()
                    ctx.death_hook_installed = ctx.install_death_hook()
                    ctx.stage_complete_patched = ctx.install_stage_complete_patch()
                    ctx.was_dying = False
                    ctx.save_pending = False
                    logger.info("Connected to Super Hexagon. If the game closes, please restart this client.")
                    
                    await ctx._apply_received_items()
                except pymem.exception.ProcessNotFound:
                    await asyncio.sleep(3)
                    continue

            ctx.ptr = ctx._read_uint32(ctx.mem.process_base.lpBaseOfDll + PTR_GAME_OBJECT)
            if not ctx.ptr:
                await asyncio.sleep(0.5)
                continue

            ctx.capture_save_values()

            if ctx.level_lock_installed:
                ctx.update_level_lock()

            if not ctx.server_address:
                await asyncio.sleep(1)
                continue

            # Sync best times with DataStorage
            if not ctx.best_times_restored:
                ctx.restore_best_times()

            # Read game state
            best_times = ctx.read_best_times()
            ctx.best_times = best_times if ctx.best_times_restored else {}

            # Deaths
            dying = ctx.is_dying()
            if ctx.kill_flag_set and (dying or ctx.read_game_state() != 0):
                ctx.set_kill_flag(False)  # the death has started, turn it off so the next run isn't killed too
                if not dying:  # left the level before the kill happened
                    ctx.deathlink_applied = False
            if dying and not ctx.was_dying:
                ctx.save_pending = True
                if ctx.deathlink_applied:  # caused by a received DeathLink, don't count or resend it
                    ctx.deathlink_applied = False
                elif "DeathLink" in ctx.tags:
                    ctx.deathlink_counter += 1
                    if ctx.deathlink_counter >= ctx.deathlink_amnesty:
                        await ctx.send_death(f"{ctx.player_names[ctx.slot]} crashed into a hexagon on {LEVELS[ctx.read_current_level()]}.")
                        ctx.deathlink_counter = 0
            ctx.was_dying = dying

            # Send any new records once the run is over and the game over screen shows them
            if ctx.save_pending and ctx.best_times_restored and ctx.read_game_state() != 0:
                await ctx.save_best_times()
                ctx.save_pending = False

            # Send new location checks
            if ctx.best_times_restored:
                new_checks = ctx.collect_location_checks(best_times)
                if new_checks:
                    ctx.checked_this_session.update(await ctx.check_locations(new_checks))

            # Start the game complete sequence if we've passed the goal time in this run
            if (ctx.best_times_restored and ctx.slot is not None and not ctx.finished_game
                    and not ctx.game_complete_started and ctx.goal_time_passed_this_run()):
                ctx.start_game_complete()

            # If we started the game complete sequence, get the goal ready
            if ctx.game_complete_started:
                goal_ready = (ctx._read_uint32(ctx.ptr + ADDR_ENDING_FLAG) != 1
                              or ctx.read_game_state() == 1)
            # Otherwise, only once the game shows this slot's times, not whatever was saved locally before
            else:
                goal_ready = ctx.best_times_restored and ctx.is_goal_reached(best_times)
            if goal_ready and not ctx.finished_game:
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
    last_error: Optional[str] = None
    while not ctx.exit_event.is_set():
        try:
            if ctx.game_connected and ctx.mem:
                ctx.ptr = ctx._read_uint32(ctx.mem.process_base.lpBaseOfDll + PTR_GAME_OBJECT)
            if ctx.game_connected and ctx.ptr:
                if ctx.slot is not None:
                    ctx.write_text(ADDR_MENU_TEXT, MENU_TEXT_CONNECTED)
                else:
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
            last_error = None

        except Exception as e:
            if ctx.game_connected and str(e) != last_error:
                logger.debug(f"Level select watcher: {e}")
            last_error = str(e)

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
        if ctx.game_connected and ctx.death_hook_installed:
            try:
                ctx.uninstall_death_hook()
            except Exception as e:
                logger.warning(f"Could not remove death patch: {e}")
        if ctx.game_connected and ctx.stage_complete_patched:
            try:
                ctx.uninstall_stage_complete_patch()
            except Exception as e:
                logger.warning(f"Could not remove stage complete patch: {e}")
        if ctx.game_connected and ctx.save_block_installed:
            try:
                ctx.uninstall_save_block()
            except Exception as e:
                logger.warning(f"Could not restore your save values, restart Super Hexagon before playing: {e}")
        await ctx.shutdown()

    parser = get_base_parser(description="Super Hexagon Archipelago Client")
    asyncio.run(main(parser.parse_args(args)))
