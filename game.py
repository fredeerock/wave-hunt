"""Wave Hunt game logic.

One round:
  1. SCAN    - head looks at each team in turn (robot's left, ahead, robot's right);
               each team gets a few seconds to wave, jump and dance. The camera measures
               how much of the picture moves: that's the team's "wave points".
  2. PICK    - the team with the most wave points wins (ties go to straight ahead)
  3. TURN    - robot turns its body toward the winners
  4. WALK    - robot walks forward, steering toward the faces it sees
               (faces, not motion: once the robot walks, everything in view moves)
  5. PARTY   - robot waves back; the winning team gets a star
"""
import logging
import random
import threading
import time

log = logging.getLogger("game")

# ------------------------------------------------------------------
# Tuning knobs. Change these if the robot turns too much/too little.
# ------------------------------------------------------------------
SETTINGS = {
    "scan_pan": 0.75,         # radians the head turns to look left/right (~43 deg)
    "head_tilt": 0.15,        # radians the head tilts up to see standing people
    "get_ready_time": 1.5,    # seconds of "get ready" after the head turns to a team
    "wave_time": 3.0,         # seconds each team has to wave
    "min_wave_points": 4,     # below this, nobody really waved
    "turn_angle": 8,          # degrees per step while turning (max 10)
    "turn_max_time": 6.0,     # seconds: longest the robot keeps turning
    "turn_min_time": 1.5,     # seconds: turn at least this long before checking the camera
    "turn_sign": 1,           # set to -1 if the robot turns the wrong way
    "walk_step": 0.02,        # meters per step forward (max 0.05)
    "walk_time": 8.0,         # seconds the robot walks toward the winners
    "steer_gain": 6,          # how hard it steers toward faces while walking
    "close_enough": 0.22,     # stop when a face fills this fraction of the image width
}

TEAMS = ["left", "center", "right"]


class Abort(Exception):
    pass


class Game:
    def __init__(self, robot, vision):
        self.robot, self.vision = robot, vision
        self.state = "idle"
        self.message = "Press START to begin the hunt"
        self.scan_zone = None
        self.phase = None          # "ready" or "wave" while scanning
        self.phase_ends = 0.0
        self.zone_points = {t: None for t in TEAMS}
        self.winner = None
        self.scores = {t: 0 for t in TEAMS}
        self.auto = False
        self.round = 0
        self._abort = threading.Event()
        self._thread = None
        self._manual_until = 0.0
        self._manual_lock = threading.Lock()
        threading.Thread(target=self._watchdog, daemon=True, name="watchdog").start()

    # ---------------- public commands ----------------
    @property
    def busy(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.busy or not self.robot.connected:
            return
        self._abort.clear()
        self._thread = threading.Thread(target=self._play, daemon=True, name="round")
        self._thread.start()

    def stop(self, why="Stopped"):
        self._abort.set()
        self.auto = False
        with self._manual_lock:
            self._manual_until = 0
        threading.Thread(target=self._safe_stop, args=(why,), daemon=True).start()

    def _safe_stop(self, why):
        self.robot.stop()
        self.robot.head(0.0, SETTINGS["head_tilt"])
        if self._thread:
            self._thread.join(timeout=3)
        self._set("idle", why)
        self.scan_zone = None

    def manual(self, move):
        """Hold-to-drive. The browser re-sends every ~200 ms; we stop if it goes quiet."""
        if self.busy:
            return
        moves = {
            "forward": (SETTINGS["walk_step"], 0),
            "back": (-0.015, 0),
            "left": (0, 7 * SETTINGS["turn_sign"]),
            "right": (0, -7 * SETTINGS["turn_sign"]),
        }
        if move not in moves:
            return
        with self._manual_lock:
            self._manual_until = time.time() + 0.6
        self.robot.walk(*moves[move])
        self._set("manual", f"Driving: {move}")

    def look(self, where):
        pan = {"left": SETTINGS["scan_pan"], "center": 0.0, "right": -SETTINGS["scan_pan"]}.get(where)
        if pan is not None and not self.busy:
            self.robot.head(pan, SETTINGS["head_tilt"])

    def action(self, name):
        if not self.busy and name in ("wave", "greet", "twist", "stand"):
            self.robot.action(name)

    def reset_scores(self):
        self.scores = {t: 0 for t in TEAMS}

    def snapshot(self):
        return {
            "state": self.state,
            "message": self.message,
            "scan_zone": self.scan_zone,
            "phase": self.phase,
            "seconds_left": max(0.0, round(self.phase_ends - time.time(), 1)) if self.phase else None,
            "zone_points": self.zone_points,
            "wave_power": self.vision.wave_power(),
            "winner": self.winner,
            "scores": self.scores,
            "auto": self.auto,
            "round": self.round,
            "faces": self.vision.count(),
            "robot": self.robot.connected,
            "camera": self.vision.ok,
            "fps": round(self.vision.fps, 1),
            "battery": self.robot.battery,
        }

    # ---------------- internals ----------------
    def _set(self, state, message):
        self.state, self.message = state, message
        log.info("[%s] %s", state, message)

    def _wait(self, seconds):
        if self._abort.wait(seconds):
            raise Abort()
        if not self.robot.connected:
            raise Abort()

    def _watchdog(self):
        """Stops manual driving when the button is released, and stops walking if the camera dies."""
        while True:
            time.sleep(0.1)
            with self._manual_lock:
                expired = self._manual_until and time.time() > self._manual_until
                if expired:
                    self._manual_until = 0
            if expired and not self.busy:
                self.robot.stop()
                self._set("idle", "Press START to begin the hunt")

    def _play(self):
        try:
            while True:
                self._round()
                if not self.auto:
                    break
                self._wait(3)
        except Abort:
            if not self._abort.is_set():          # camera or robot dropped out (not the STOP button)
                self.robot.stop()
                self.scan_zone = None
                self._set("idle", "Lost the camera or robot connection, so I stopped. Press START to retry")
        except Exception:  # noqa: BLE001
            log.exception("round crashed")
            self.robot.stop()
            self._set("idle", "Something went wrong. Press START to try again")

    def _round(self):
        S = SETTINGS
        self.round += 1
        self.winner = None
        self.zone_points = {t: None for t in TEAMS}

        # 1. SCAN: each team gets its turn to wave
        self._set("scanning", "Each team gets a turn to wave")
        pans = {"left": S["scan_pan"], "center": 0.0, "right": -S["scan_pan"]}
        try:
            for team in TEAMS:
                self.scan_zone = team
                self.vision.tint_zone = TEAMS.index(team)
                self.robot.head(pans[team], S["head_tilt"], duration=0.5)
                self.phase, self.phase_ends = "ready", time.time() + S["get_ready_time"]
                self._wait(S["get_ready_time"])                  # head settles while the team gets ready
                self.vision.start_meter()
                self.phase, self.phase_ends = "wave", time.time() + S["wave_time"]
                while time.time() < self.phase_ends:
                    self.zone_points[team] = self.vision.read_meter()   # live score
                    self._wait(0.1)
                self.zone_points[team] = self.vision.read_meter()
        finally:
            self.scan_zone, self.phase = None, None
            self.vision.tint_zone = None
        self.robot.head(0.0, S["head_tilt"], duration=0.5)

        # 2. PICK
        best = max(self.zone_points.values())
        if best < S["min_wave_points"]:
            self._set("sad", "Nobody waved! Move those arms and try again")
            self._wait(2.5)
            self._set("idle", "Press START to begin the hunt")
            return
        tied = [t for t in TEAMS if self.zone_points[t] == best]
        self.winner = "center" if "center" in tied else random.choice(tied)
        self._set("picked", f"{best} wave points! Heading {self._where(self.winner)}")
        self._wait(2)

        # 3. TURN
        if self.winner != "center":
            direction = 1 if self.winner == "left" else -1
            self._set("turning", f"Turning {self._where(self.winner)}")
            t0 = time.time()
            while True:
                self.robot.walk(0, direction * S["turn_angle"] * S["turn_sign"])
                self._wait(0.2)
                elapsed = time.time() - t0
                c = self.vision.centroid()
                if elapsed > S["turn_min_time"] and c is not None and abs(c) < 0.25:
                    break                                     # faces are in the middle of the view
                if elapsed > S["turn_max_time"]:
                    break

        # 4. WALK
        self._set("walking", f"Walking to team {self.winner.upper()}")
        t0 = time.time()
        while time.time() - t0 < S["walk_time"]:
            if not self.vision.ok:
                raise Abort()                                 # camera lost: don't walk blind
            c = self.vision.centroid()
            steer = 0.0 if c is None else max(-10, min(10, -c * S["steer_gain"] * 1.5))
            self.robot.walk(S["walk_step"], steer * S["turn_sign"])
            if self.vision.biggest_face() > S["close_enough"]:
                break
            self._wait(0.2)
        self.robot.stop()

        # 5. PARTY
        self.scores[self.winner] += 1
        self._set("party", f"Team {self.winner.upper()} waved the hardest!")
        self.robot.action("wave")
        self._wait(4)
        self._set("idle", "Press START for another round")

    @staticmethod
    def _where(team):
        return {"left": "to the robot's left", "center": "straight ahead", "right": "to the robot's right"}[team]
