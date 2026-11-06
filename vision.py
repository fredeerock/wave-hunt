"""Reads the robot's camera stream and finds faces (on the laptop, not the robot)."""
import base64
import json
import logging
import threading
import time

import cv2
import numpy as np
from websockets.sync.client import connect

log = logging.getLogger("vision")

# Team colors in BGR (robot's left third, middle, right third of the image)
ZONE_COLORS = [(107, 107, 255), (60, 201, 255), (166, 230, 46)]


class Vision:
    def __init__(self, host: str, model_path: str):
        self.url = f"ws://{host}:9090"
        self.det = cv2.FaceDetectorYN.create(model_path, "", (640, 480),
                                             score_threshold=0.7, nms_threshold=0.3, top_k=50)
        self.frame = None
        self.faces = []        # list of (x, y, w, h, score) in pixels
        self.motion = 0.0      # fraction of the picture that moved since the last frame (0..1)
        self.motion_live = 0.0 # smoothed version for the on-screen meter
        self.motion_mask = None
        self.tint_zone = None  # 0/1/2 to color all motion in one team's color
        self._prev_small = None
        self._acc_sum, self._acc_n = 0.0, 0
        self.size = (640, 480)
        self.stamp = 0.0
        self.fps = 0.0
        self.cond = threading.Condition()
        threading.Thread(target=self._run, daemon=True, name="camera").start()

    # ---------- stream ----------
    # The robot's web_video_server (port 8080) tends to freeze, so we pull the
    # compressed JPEG topic through rosbridge on its own websocket instead.
    def _run(self):
        sub = {"op": "subscribe", "topic": "/camera/image_raw/compressed",
               "type": "sensor_msgs/CompressedImage", "throttle_rate": 40, "queue_length": 1}
        while True:
            try:
                log.info("opening camera stream %s", self.url)
                with connect(self.url, open_timeout=5, close_timeout=1, max_size=None) as ws:
                    ws.send(json.dumps(sub))
                    while True:
                        m = json.loads(ws.recv(timeout=3))
                        if m.get("op") == "publish":
                            self._process(base64.b64decode(m["msg"]["data"]))
            except Exception as e:  # noqa: BLE001
                log.warning("camera: %s (retrying)", e or type(e).__name__)
                time.sleep(1)

    def _process(self, jpg):
        img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return
        h, w = img.shape[:2]
        self.det.setInputSize((w, h))
        _, raw = self.det.detect(img)
        faces = [] if raw is None else [(int(f[0]), int(f[1]), int(f[2]), int(f[3]), float(f[14])) for f in raw]
        motion, mask = self._measure_motion(img)
        now = time.time()
        with self.cond:
            if self.stamp:
                self.fps = 0.9 * self.fps + 0.1 / max(now - self.stamp, 1e-3)
            self.frame, self.faces, self.size, self.stamp = img, faces, (w, h), now
            self.motion, self.motion_mask = motion, mask
            self.motion_live = 0.7 * self.motion_live + 0.3 * motion
            self._acc_sum += motion
            self._acc_n += 1
            self.cond.notify_all()

    def start_meter(self):
        """Begin adding up motion (call once the head has stopped moving)."""
        with self.cond:
            self._acc_sum, self._acc_n = 0.0, 0
            self._prev_small = None   # don't count the jump from the old head position

    def read_meter(self):
        """Wave points since start_meter(): average moving fraction x 1000 (vigorous waving ~ 30-100)."""
        with self.cond:
            return round(1000 * self._acc_sum / self._acc_n) if self._acc_n else 0

    def _measure_motion(self, img):
        """How much of the picture changed since the last frame (waving hands, jumping...)."""
        small = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(img, (160, 120)), cv2.COLOR_BGR2GRAY), (5, 5), 0)
        prev, self._prev_small = self._prev_small, small
        if prev is None:
            return 0.0, None
        mask = cv2.absdiff(small, prev) > 22
        frac = float(mask.mean())
        if frac > 0.5:          # whole picture changed: head/robot moving or lights flicker, not waving
            return 0.0, None
        return frac, mask

    def wave_power(self):
        """Live wave power on the same scale as the scan score."""
        return round(self.motion_live * 1000) if self.ok else 0

    # ---------- queries ----------
    @property
    def ok(self):
        return time.time() - self.stamp < 2.0

    def count(self):
        return len(self.faces) if self.ok else 0

    def centroid(self):
        """Horizontal position of the crowd, -1 (image left) .. +1 (image right), or None."""
        faces, (w, _) = self.faces, self.size
        if not faces or not self.ok:
            return None
        xs = [(f[0] + f[2] / 2) for f in faces]
        return (sum(xs) / len(xs)) / (w / 2) - 1.0

    def biggest_face(self):
        """Width of the largest face as a fraction of image width (0 if none)."""
        if not self.faces or not self.ok:
            return 0.0
        return max(f[2] for f in self.faces) / self.size[0]

    # ---------- display ----------
    def annotated_jpeg(self, wait=1.0):
        with self.cond:
            self.cond.wait(wait)
            frame, faces, mask = self.frame, list(self.faces), self.motion_mask
        if frame is None:
            return None
        img = frame.copy()
        h, w = img.shape[:2]
        if mask is not None and mask.any():
            # light up whatever moved, in the color of the team zone it's in
            big = cv2.resize(cv2.dilate(mask.astype(np.uint8), np.ones((3, 3), np.uint8)), (w, h),
                             interpolation=cv2.INTER_NEAREST).astype(bool)
            tint = np.zeros_like(img)
            if self.tint_zone is not None:        # a team's turn: the whole view is theirs
                tint[:] = ZONE_COLORS[self.tint_zone]
            else:
                for z, c in enumerate(ZONE_COLORS):
                    tint[:, z * w // 3:(z + 1) * w // 3] = c
            img[big] = (0.45 * img[big] + 0.55 * tint[big]).astype(np.uint8)
        for i, (x, y, fw, fh, _) in enumerate(faces, 1):
            zone = min(2, max(0, int((x + fw / 2) / (w / 3))))
            c = ZONE_COLORS[zone]
            L = max(10, fw // 4)
            for (px, py, dx, dy) in ((x, y, 1, 1), (x + fw, y, -1, 1), (x, y + fh, 1, -1), (x + fw, y + fh, -1, -1)):
                cv2.line(img, (px, py), (px + dx * L, py), c, 4, cv2.LINE_AA)
                cv2.line(img, (px, py), (px, py + dy * L), c, 4, cv2.LINE_AA)
            label = f"#{i}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_DUPLEX, 0.7, 2)
            cv2.rectangle(img, (x, y - th - 12), (x + tw + 10, y - 2), c, -1)
            cv2.putText(img, label, (x + 5, y - 7), cv2.FONT_HERSHEY_DUPLEX, 0.7, (40, 20, 14), 2, cv2.LINE_AA)
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return jpg.tobytes() if ok else None
