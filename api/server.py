# api/server.py

import asyncio
import os
import sys
from contextlib import asynccontextmanager

import rclpy  # type: ignore[import-untyped]
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'core'))
from bridge_client import BridgeClient
from llm_engine    import LLMEngine
from nav2_client   import Nav2Client

load_dotenv()

bridge: BridgeClient = None # pyright: ignore[reportAssignmentType]
engine: LLMEngine    = None # type: ignore
nav2:   Nav2Client | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bridge, engine, nav2

    print("[startup] Initialising ROS2 context...")
    rclpy.init()

    print("[startup] Connecting to rosbridge...")
    bridge = BridgeClient(host='localhost', port=9090)
    await asyncio.sleep(0.5)

    print("[startup] Starting Nav2 client...")
    try:
        nav2 = Nav2Client()
        print(f"[startup] Nav2 ready. Locations: {nav2.get_location_names()}")
    except Exception as e:
        print(f"[startup] Nav2 unavailable ({e}). Velocity commands only.")
        nav2 = None

    print("[startup] Loading LLM engine...")
    engine = LLMEngine(bridge_client=bridge, nav2_client=nav2)
    engine.refresh_topics()

    print("[startup] Ready at http://localhost:8000")
    yield

    print("[shutdown] Cleaning up...")
    bridge.close()
    rclpy.shutdown()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    with open("static/index.html") as f:
        return HTMLResponse(f.read())


@app.get("/health")
async def health():
    """Quick check endpoint — useful for debugging."""
    return {
        "status":  "ok",
        "bridge":  bridge.client.is_connected if bridge else False,
        "topics":  len(engine.topic_manifest) if engine else 0,
    }


@app.websocket("/ws/chat")
async def chat_endpoint(ws: WebSocket):
    await ws.accept()
    print(f"[ws] Client connected")

    # Send a welcome message immediately on connect
    await ws.send_json({
        "type": "status",
        "msg":  "Connected to robot controller. Ready for commands."
    })

    # Start a background task that pushes robot state every second
    # asyncio.create_task() runs it concurrently without blocking
    state_task = asyncio.create_task(stream_robot_state(ws))

    try:
        while True:
            # Wait for next message from the browser
            data     = await ws.receive_json()
            user_msg = data.get("message", "").strip()

            if not user_msg:
                continue

            # Handle conversation reset signal from UI
            if user_msg == '__reset__':
                engine.reset_history()
                await ws.send_json({
                    "type": "status",
                    "msg":  "Conversation history cleared. Ready for new commands."
                })
                continue

            # Tell the UI we received it and are working on it
            await ws.send_json({
                "type": "thinking",
                "msg":  "Interpreting command..."
            })

            # Run engine.chat() in a thread pool so the event loop
            # stays free to send state updates while the robot moves
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                engine.chat,
                user_msg
            )

            # Send the final response back to the UI
            await ws.send_json({
                "type": "response",
                "msg":  response
            })

    except WebSocketDisconnect:
        print(f"[ws] Client disconnected")
        state_task.cancel()

    except Exception as e:
        print(f"[ws] Error: {e}")
        state_task.cancel()
        await ws.send_json({"type": "error", "msg": str(e)})


async def stream_robot_state(ws: WebSocket):
    """Pushes live robot position + heading to UI every second."""
    try:
        while True:
            await asyncio.sleep(1.0)
            state = bridge.get_state()
            await ws.send_json({
                "type": "state",
                "data": state
            })
    except Exception:
        pass  # WebSocket closed, task will be cancelled