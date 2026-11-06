# Wave Hunt

Three teams compete to attract the AiNex robot. It turns its head to each team in turn,
and that team has a few seconds to wave, jump and dance. The camera measures how much
movement it sees ("wave points"), and the robot walks over to the team that moved the most.
Teams score a star each time the robot comes to them.

Because it counts movement, not people, a small team that goes wild can beat a big team
that barely moves.

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

The game only opens on this laptop. To open it from another device (say, a tablet), run
`uv run app.py --host 0.0.0.0` and visit `http://<laptop-ip>:8000`. Anyone who can reach
that address can drive the robot, so only do this on a network you trust.

## Playing

- Split the room into three teams: **Coral** stands on the robot's left, **Gold** in front,
  **Mint** on the robot's right.
- Press **Start hunt** (or Enter). For each team the screen says "get ready", then
  "wave!" with a countdown. Everything that moves lights up in the team's color.
- The team with the most wave points wins: the robot turns, walks to them and waves back.
- Between rounds, the **Wave power** meter at the top shows movement live, so teams can
  practice.
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

Wave points come from comparing each camera frame with the one before it and counting how
much of the picture changed. That only works while the robot stands still, so once it walks
it steers by faces instead, using OpenCV's YuNet face detector (`models/`). It stops when a
face gets big in the picture, meaning it has arrived.

| File | Role |
| --- | --- |
| `app.py` | Web server: page, video stream, live game state |
| `game.py` | The game rounds, plus the tuning knobs in `SETTINGS` |
| `robot.py` | Sends commands to the robot |
| `vision.py` | Gets camera frames, measures movement, finds faces |
| `static/index.html` | The game screen |

## Tuning

Edit `SETTINGS` at the top of `game.py`:

- Robot turns the **wrong way**: set `turn_sign` to `-1`.
- Turns **too far / not far enough**: change `turn_max_time`.
- Walks **too far / not far enough**: change `walk_time` or `walk_step`.
- Teams need **more or less time** to wave: change `wave_time`.
- "Nobody waved!" when people did: lower `min_wave_points`.
- Misses people's faces while walking: raise `head_tilt` so it looks up more.

## Safety

- Keep the robot on the floor with clear space around it, not on a table.
- The robot stops on its own if the camera feed or the robot connection drops,
  and when you quit the app with Ctrl+C.
