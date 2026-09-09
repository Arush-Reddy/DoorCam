import os
import re
import time
import logging
import threading
import subprocess

logger = logging.getLogger(__name__)

# Global state
ACTIVE_TUNNEL_PROC = None
PUBLIC_TUNNEL_URL = None
TUNNEL_LOCK = threading.Lock()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(BASE_DIR, "tools")
CLOUDFLARED_EXE = os.path.join(TOOLS_DIR, "cloudflared.exe")
PUBLIC_URL_FILE = os.path.join(BASE_DIR, "public_url.txt")

def get_public_url():
    """Returns the active Cloudflare Tunnel public URL, or None."""
    global PUBLIC_TUNNEL_URL
    with TUNNEL_LOCK:
        if PUBLIC_TUNNEL_URL:
            return PUBLIC_TUNNEL_URL
    if os.path.exists(PUBLIC_URL_FILE):
        try:
            with open(PUBLIC_URL_FILE, "r") as f:
                url = f.read().strip()
                if url.startswith("https://"):
                    return url
        except Exception:
            pass
    return None

def _tunnel_worker(local_port: int = 5000):
    global ACTIVE_TUNNEL_PROC, PUBLIC_TUNNEL_URL

    if not os.path.exists(CLOUDFLARED_EXE):
        logger.warning("cloudflared.exe not found in %s. Remote tunnel disabled.", TOOLS_DIR)
        return

    cmd = [CLOUDFLARED_EXE, "tunnel", "--url", f"http://127.0.0.1:{local_port}"]
    logger.info("Starting Cloudflare Tunnel to 127.0.0.1:%d...", local_port)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        ACTIVE_TUNNEL_PROC = proc

        found_url = False
        for line in proc.stdout:
            if not found_url:
                match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                if match:
                    url = match.group(0)
                    with TUNNEL_LOCK:
                        PUBLIC_TUNNEL_URL = url
                    found_url = True
                    
                    # Write to disk for other processes
                    try:
                        with open(PUBLIC_URL_FILE, "w", encoding="utf-8") as f:
                            f.write(url)
                    except Exception:
                        pass

                    print("\n" + "="*70)
                    print("[REMOTE ACCESS] Your DoorCam is now globally live on the internet!")
                    print(f"Public HTTPS URL: {url}")
                    print("Access your doorbell anywhere in the world on 4G/5G mobile data!")
                    print("="*70 + "\n", flush=True)
                    logger.info("[TUNNEL] Public HTTPS URL: %s", url)

        # Keep process alive
        proc.wait()
    except Exception as e:
        logger.error("[TUNNEL] Error in Cloudflare Tunnel process: %s", e)
    finally:
        with TUNNEL_LOCK:
            PUBLIC_TUNNEL_URL = None
        if os.path.exists(PUBLIC_URL_FILE):
            try:
                os.remove(PUBLIC_URL_FILE)
            except Exception:
                pass

def start_tunnel(local_port: int = 5000):
    """Launches the Cloudflare Tunnel worker in a background daemon thread."""
    thread = threading.Thread(target=_tunnel_worker, args=(local_port,), daemon=True)
    thread.start()
    return thread

def stop_tunnel():
    """Stops the active Cloudflare tunnel process."""
    global ACTIVE_TUNNEL_PROC
    if ACTIVE_TUNNEL_PROC:
        try:
            ACTIVE_TUNNEL_PROC.terminate()
            ACTIVE_TUNNEL_PROC = None
        except Exception:
            pass

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    start_tunnel()
    print("Tunnel starting... Waiting 15s...")
    time.sleep(15)
    print("Detected URL:", get_public_url())
