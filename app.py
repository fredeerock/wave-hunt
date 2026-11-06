"""Face Hunt web app.  Run:  uv run app.py   then open http://localhost:8000"""
import argparse
import asyncio
import json
import logging
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse

from game import Game
from robot import Robot
from vision import Vision

HERE = Path(__file__).parent
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)-7s %(message)s", datefmt="%H:%M:%S")

parser = argparse.ArgumentParser()
parser.add_argument("--robot", default="192.168.149.1", help="robot IP address")
parser.add_argument("--port", type=int, default=8000)
parser.add_argument("--no-browser", action="store_true")
args, _ = parser.parse_known_args()

robot = Robot(args.robot)
vision = Vision(args.robot, str(HERE / "models" / "face_detection_yunet_2023mar.onnx"))
game = Game(robot, vision)
app = FastAPI()


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/video")
def video():
    def frames():
        while True:
            jpg = vision.annotated_jpeg()
            if jpg is None:
                time.sleep(0.2)
                continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()

    async def push():
        while True:
            await sock.send_text(json.dumps(game.snapshot()))
            await asyncio.sleep(0.1)

    pusher = asyncio.create_task(push())
    try:
        while True:
            msg = json.loads(await sock.receive_text())
            cmd = msg.get("cmd")
            # robot calls can block for a moment, so run them off the event loop
            run = lambda f, *a: asyncio.get_running_loop().run_in_executor(None, f, *a)  # noqa: E731
            if cmd == "start":
                await run(game.start)
            elif cmd == "stop":
                await run(game.stop, "Stopped. Press START when ready")
            elif cmd == "auto":
                game.auto = bool(msg.get("on"))
                if game.auto:
                    await run(game.start)
            elif cmd == "drive":
                await run(game.manual, msg.get("move"))
            elif cmd == "look":
                await run(game.look, msg.get("where"))
            elif cmd == "action":
                await run(game.action, msg.get("name"))
            elif cmd == "reset_scores":
                game.reset_scores()
    except WebSocketDisconnect:
        pass
    finally:
        pusher.cancel()


if __name__ == "__main__":
    url = f"http://localhost:{args.port}"
    print(f"\n  Face Hunt is running:  {url}\n  Robot: {args.robot}   (Ctrl+C to quit)\n")
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    try:
        uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
    finally:
        print("Stopping robot...")
        robot.stop()
