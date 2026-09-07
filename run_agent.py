import subprocess
import sys
import time

while True:
    try:
        print("[run_agent] Starting agent connect --room snapback-call...")
        proc = subprocess.run([sys.executable, "agent.py", "connect", "--room", "snapback-call"])
        print(f"[run_agent] Agent exited with code {proc.returncode}. Reconnecting in 1s...")
        time.sleep(1)
    except KeyboardInterrupt:
        break
    except Exception as e:
        print(f"[run_agent] Error: {e}. Retrying in 2s...")
        time.sleep(2)
