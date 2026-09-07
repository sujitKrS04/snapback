import asyncio
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any, Optional

from dotenv import load_dotenv
from livekit import api, rtc
from livekit.agents.utils import http_context

from agent import run_agent_in_room

load_dotenv()
logger = logging.getLogger("snapback-agent-runner")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")

ROOM_NAME = os.getenv("ROOM_NAME", "snapback-call")
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")


async def run_agent_loop() -> None:
    """Resilient agent loop connecting directly to room with auto-reconnect."""
    while True:
        try:
            logger.info("Connecting directly to LiveKit room '%s'...", ROOM_NAME)
            token = (
                api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
                .with_identity("snapback-voice-agent")
                .with_grants(
                    api.VideoGrants(
                        room_join=True,
                        room=ROOM_NAME,
                        can_publish=True,
                        can_subscribe=True,
                        can_publish_data=True,
                    )
                )
                .to_jwt()
            )

            room = rtc.Room()
            disconnect_event = asyncio.Event()

            @room.on("disconnected")
            def on_disconnected(reason: Any = None) -> None:
                logger.info("Room disconnected (%s). Triggering auto-reconnect...", reason)
                disconnect_event.set()

            async with http_context.open():
                await room.connect(LIVEKIT_URL, token)
                logger.info("Successfully connected to '%s'! Initializing voice pipeline...", ROOM_NAME)
                pipeline = await run_agent_in_room(room)
                logger.info("Agent is live and active in room '%s' (active_tts_provider=%s).", ROOM_NAME, pipeline.active_tts_provider)
                await disconnect_event.wait()

            logger.info("Cleaning up before reconnecting...")
            await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("Agent loop cancelled.")
            break
        except Exception as e:
            logger.error("Agent loop exception: %s. Reconnecting in 2s...", e)
            await asyncio.sleep(2)


PID_FILE = Path("logs/agent.pid")


def acquire_singleton_lock() -> bool:
    """Ensure only one run_agent process runs at any given time."""
    try:
        import psutil
        if PID_FILE.exists():
            try:
                old_pid = int(PID_FILE.read_text().strip())
                if psutil.pid_exists(old_pid) and old_pid != os.getpid():
                    p = psutil.Process(old_pid)
                    cmd = " ".join(p.cmdline())
                    if "run_agent.py" in cmd:
                        logger.warning("Another run_agent.py is already running (pid=%s). Exiting duplicate process.", old_pid)
                        return False
            except Exception:
                pass
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()))
        return True
    except Exception as e:
        logger.debug(f"Lock check error: {e}")
        return True


def main() -> None:
    if not acquire_singleton_lock():
        sys.exit(0)
    try:
        asyncio.run(run_agent_loop())
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if PID_FILE.exists() and int(PID_FILE.read_text().strip()) == os.getpid():
                PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()

