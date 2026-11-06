# Face Hunt

The AiNex robot looks left, straight ahead and right, counts the faces it sees in each
direction, then turns and walks toward the team with the most faces. Teams score a star
each time the robot comes to them.

## Run it

1. Join the robot's Wi-Fi on the laptop (the robot is at `192.168.149.1`).
2. Make sure the robot is switched on and standing.
3. In this folder run:

   ```
   uv run app.py
   ```

   or double-click `start.command`. A browser opens at http://localhost:8000.

The first run needs internet to install Python packages; after that it works offline.
To use a robot at a different address: `uv run app.py --robot 192.168.149.1`.

## Playing

- Split the room into three teams: **Coral** stands on the robot's left, **Gold** in front,
  **Mint** on the robot's right.
- Press **Start hunt** (or Enter). The robot scans, picks a winner, turns, walks toward
  them and waves.
- **Stop** (or Space) stops the robot immediately, at any time.
- Hold the arrow buttons (or arrow keys) to drive the robot yourself between rounds.
- Click anything once so the browser allows sound.

## How it works

Nothing is installed on the robot. The laptop connects to the robot's own ROS bridge
(`ws://192.168.149.1:9090`), which the factory software already runs:

| What | ROS topic / service |
| --- | --- |
| Camera frames | `/camera/image_raw/compressed` |
| Walk / turn | `/app/set_walking_param` + `/walking/command` |
| Turn head | `/head_pan_controller/command` |
| Wave, bow, dance | `/app/set_action` |

Face detection runs on the laptop with OpenCV's YuNet model (`models/`), so it is fast and
can see many faces at once.

| File | Role |
| --- | --- |
| `app.py` | Web server: page, video stream, live game state |
| `game.py` | The game rounds, plus the tuning knobs in `SETTINGS` |
| `robot.py` | Sends commands to the robot |
| `vision.py` | Gets camera frames and finds faces |
| `static/index.html` | The game screen |

## Tuning

Edit `SETTINGS` at the top of `game.py`:

- Robot turns the **wrong way**: set `turn_sign` to `-1`.
- Turns **too far / not far enough**: change `turn_max_time`.
- Walks **too far / not far enough**: change `walk_time` or `walk_step`.
- Misses people's faces: raise `head_tilt` so it looks up more.

## Safety

- Keep the robot on the floor with clear space around it, not on a table.
- The robot stops on its own if the camera feed or the robot connection drops,
  and when you quit the app with Ctrl+C.
