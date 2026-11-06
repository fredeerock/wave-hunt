"""Talks to the AiNex robot through rosbridge (ws://<robot>:9090).

Nothing is installed on the robot: we publish the same ROS topics and call the
same services that Hiwonder's own joystick / phone app use.
"""
import itertools
import json
import logging
import threading
import time

from websockets.sync.client import connect

log = logging.getLogger("robot")

TOPICS = {
    "/app/set_walking_param": "ainex_interfaces/AppWalkingParam",
    "/app/set_action": "std_msgs/String",
    "/head_pan_controller/command": "ainex_interfaces/HeadState",
    "/head_tilt_controller/command": "ainex_interfaces/HeadState",
}

BODY_HEIGHT = 0.025   # same default height the joystick uses
GAIT_SPEED = 3        # 1 (slow) .. 4 (fast); joystick uses 3


class Robot:
    def __init__(self, host: str):
        self.url = f"ws://{host}:9090"
        self.connected = False
        self.walking = False
        self.battery = None          # volts
        self._ws = None
        self._send_lock = threading.Lock()
        self._ids = itertools.count()
        self._pending = {}
        threading.Thread(target=self._run, daemon=True, name="rosbridge").start()

    # ---------- connection ----------
    def _run(self):
        while True:
            try:
                log.info("connecting to %s", self.url)
                ws = connect(self.url, open_timeout=5, close_timeout=1, max_size=None)
                self._ws = ws
                for topic, typ in TOPICS.items():
                    self._send({"op": "advertise", "topic": topic, "type": typ})
                self._send({"op": "subscribe", "topic": "/ros_robot_controller/battery",
                            "type": "std_msgs/UInt16", "throttle_rate": 2000})
                time.sleep(0.5)  # give ROS time to wire up publishers
                self.connected = True
                self.walking = False
                log.info("connected to robot")
                for raw in ws:
                    self._on_message(json.loads(raw))
            except Exception as e:  # noqa: BLE001
                if self.connected:
                    log.warning("robot connection lost: %s", e)
            self.connected = False
            self._ws = None
            time.sleep(2)

    def _on_message(self, m):
        op = m.get("op")
        if op == "publish" and m.get("topic", "").endswith("battery"):
            self.battery = m["msg"]["data"] / 1000.0
        elif op == "service_response":
            slot = self._pending.get(m.get("id"))
            if slot:
                slot["result"] = m
                slot["event"].set()

    def _send(self, obj):
        ws = self._ws
        if ws is None:
            return False
        try:
            with self._send_lock:
                ws.send(json.dumps(obj))
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("send failed: %s", e)
            return False

    def publish(self, topic, msg):
        return self._send({"op": "publish", "topic": topic, "msg": msg})

    def call(self, service, args=None, timeout=5.0):
        sid = f"c{next(self._ids)}"
        slot = {"event": threading.Event(), "result": None}
        self._pending[sid] = slot
        try:
            if not self._send({"op": "call_service", "id": sid, "service": service, "args": args or {}}):
                return None
            slot["event"].wait(timeout)
            return slot["result"]
        finally:
            self._pending.pop(sid, None)

    # ---------- robot actions ----------
    def head(self, pan=0.0, tilt=None, duration=0.4):
        """pan in radians: positive = robot's LEFT, negative = robot's RIGHT."""
        self.publish("/head_pan_controller/command", {"position": float(pan), "duration": float(duration)})
        if tilt is not None:
            self.publish("/head_tilt_controller/command", {"position": float(tilt), "duration": float(duration)})

    def walk(self, x=0.0, angle=0.0, y=0.0):
        """x: step length in meters (+forward, max 0.05). angle: turn per step in degrees (+left, max 10)."""
        if not self.walking:
            self.call("/walking/command", {"command": "enable_control"}, timeout=2)
            self.call("/walking/command", {"command": "start"}, timeout=2)
            self.walking = True
        self.publish("/app/set_walking_param", {
            "speed": GAIT_SPEED, "height": BODY_HEIGHT,
            "x": float(x), "y": float(y), "angle": float(angle),
        })

    def stop(self):
        self.publish("/app/set_walking_param", {"speed": GAIT_SPEED, "height": BODY_HEIGHT,
                                                "x": 0.0, "y": 0.0, "angle": 0.0})
        self.call("/walking/command", {"command": "stop"}, timeout=5)
        self.walking = False

    def action(self, name):
        """Play a built-in action group, e.g. 'wave', 'greet', 'twist', 'stand'."""
        self.walking = False
        self.publish("/app/set_action", {"data": name})
