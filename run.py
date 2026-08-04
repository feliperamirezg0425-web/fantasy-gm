#!/usr/bin/env python3
"""
One-command launcher for Fantasy GM.

Run this with:  python3 run.py
(or double-click run.command on Mac / run.bat on Windows)

It will, in order:
  1. Create a virtual environment in backend/venv if one doesn't exist yet
  2. Install dependencies into it (only the first time, or after requirements.txt changes)
  3. Seed the mock database if it doesn't exist yet
  4. Start the API server
  5. Open the dashboard in your default browser
  6. Keep running until you press Ctrl+C, then shut everything down cleanly
"""
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(ROOT, "backend")
VENV_DIR = os.path.join(BACKEND, "venv")
REQUIREMENTS = os.path.join(BACKEND, "requirements.txt")
DB_PATH = os.path.join(BACKEND, "fantasy_gm.db")
DEPS_MARKER = os.path.join(VENV_DIR, ".deps_installed_hash")
FRONTEND_INDEX = os.path.join(ROOT, "frontend", "index.html")
PORT = 8000

IS_WINDOWS = platform.system() == "Windows"


def venv_python():
    if IS_WINDOWS:
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python3")


def run(cmd, **kwargs):
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


def ensure_venv():
    if not os.path.exists(venv_python()):
        print("Setting up a Python virtual environment (first run only)...")
        run([sys.executable, "-m", "venv", VENV_DIR])


def requirements_hash():
    with open(REQUIREMENTS, "rb") as f:
        import hashlib
        return hashlib.sha256(f.read()).hexdigest()


def ensure_deps():
    current_hash = requirements_hash()
    installed_hash = None
    if os.path.exists(DEPS_MARKER):
        with open(DEPS_MARKER) as f:
            installed_hash = f.read().strip()
    if installed_hash != current_hash:
        print("Installing dependencies (first run, or requirements.txt changed)...")
        run([venv_python(), "-m", "pip", "install", "-q", "--upgrade", "pip"])
        run([venv_python(), "-m", "pip", "install", "-q", "-r", REQUIREMENTS])
        with open(DEPS_MARKER, "w") as f:
            f.write(current_hash)
    else:
        print("Dependencies already installed, skipping.")


def ensure_seeded():
    if not os.path.exists(DB_PATH):
        print("Seeding mock database (first run only)...")
        run([venv_python(), "-m", "app.seed_mock_data"], cwd=BACKEND)
    else:
        print("Database already seeded, skipping. (Delete backend/fantasy_gm.db to reseed.)")


def wait_for_health(timeout=20):
    url = f"http://localhost:{PORT}/health"
    for _ in range(timeout * 2):
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    if shutil.which is None:
        pass
    ensure_venv()
    ensure_deps()
    ensure_seeded()

    print(f"\nStarting API server on http://localhost:{PORT} ...")
    server_proc = subprocess.Popen(
        [venv_python(), "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(PORT)],
        cwd=BACKEND,
    )

    try:
        if wait_for_health():
            print("API is up.")
        else:
            print("Warning: API did not respond to a health check in time — it may still be starting.")

        frontend_url = "file://" + FRONTEND_INDEX
        print(f"Opening dashboard: {frontend_url}")
        webbrowser.open(frontend_url)

        print("\nFantasy GM is running.")
        print(f"  API + docs: http://localhost:{PORT}/docs")
        print(f"  Dashboard:  {frontend_url}")
        print("\nPress Ctrl+C to stop.\n")

        server_proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        if server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()
        print("Stopped.")


if __name__ == "__main__":
    main()
