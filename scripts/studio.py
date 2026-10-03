"""One-command setup and start for Agent Studio and the voice agent.

    make setup    or  python scripts/studio.py setup   install everything (once)
    make start    or  python scripts/studio.py start   run backend + studio, open the browser

    setup --no-keys     skip the API key questions      start --no-browser   do not open a browser

Standard library only, so any Python 3 can run it. It builds the backend virtualenv with Python
3.11 (or 3.12) and installs the frontend with pnpm, or with Node's bundled corepack when pnpm is
missing. Both agents (Clinic Scheduler on the SF catalog, National Scheduler) ship in
backend/agents, so the studio opens with them; there is nothing to create.
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
LOGS = ROOT / ".studio"
WINDOWS = os.name == "nt"
VENV = BACKEND / ".venv"
VENV_PYTHON = VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")
BACKEND_PORT = 7860
# Python 3.13 removed audioop, which Pipecat still imports; 3.11 is the tested version.
PYTHONS = ((3, 11), (3, 12))
KEYS = (
    ("OPENAI_API_KEY", "OpenAI (required for a call)"),
    ("ELEVENLABS_API_KEY", "ElevenLabs (required for a call)"),
    ("CMD_API_KEY", "Command Code JEV (optional: without it, JEV mode runs on rules only)"),
)


def say(text: str) -> None:
    print(f"\033[36m==>\033[0m {text}", flush=True)


def fail(text: str) -> None:
    print(f"\033[31mxx\033[0m  {text}", file=sys.stderr, flush=True)
    sys.exit(1)


def run(cmd: list[str], cwd: Path = ROOT, env: dict | None = None) -> None:
    print("    $ " + " ".join(cmd), flush=True)
    if subprocess.run(cmd, cwd=cwd, env=env).returncode != 0:
        fail(f"failed: {' '.join(cmd)}")


def python_version(cmd: list[str]) -> tuple[int, int] | None:
    try:
        out = subprocess.run([*cmd, "-c", "import sys; print(*sys.version_info[:2])"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    parts = out.stdout.split()
    return (int(parts[0]), int(parts[1])) if out.returncode == 0 and len(parts) == 2 else None


def find_python() -> list[str]:
    """A Python that can run the backend: 3.11 first, then 3.12."""
    candidates = [[sys.executable], ["python3.11"], ["python3.12"], ["python3"], ["python"]]
    if WINDOWS:
        candidates = [["py", "-3.11"], ["py", "-3.12"], *candidates]
    found = {}
    for cmd in candidates:
        version = python_version(cmd)
        if version in PYTHONS and version not in found:
            found[version] = cmd
    for version in PYTHONS:
        if version in found:
            return found[version]
    fail("Python 3.11 is needed for the backend (3.12 also works). "
         + ("Install it from python.org." if WINDOWS else "macOS: brew install python@3.11"))
    raise AssertionError


def pnpm() -> tuple[list[str], dict]:
    """pnpm, or pnpm through Node's bundled corepack (no global install needed)."""
    env = dict(os.environ)
    if path := shutil.which("pnpm"):
        return [path], env
    if path := shutil.which("corepack"):
        env["COREPACK_ENABLE_DOWNLOAD_PROMPT"] = "0"
        return [path, "pnpm"], env
    fail("Node.js 22 is needed for the studio (it includes corepack, which provides pnpm). See nodejs.org.")
    raise AssertionError


def ask_keys(prompt: bool = True) -> None:
    """backend/.env from the example; offers to fill in the keys (hidden input, Enter skips).
    Asks only in an interactive terminal: on Windows the hidden prompt reads the console directly
    and would wait forever under a script or CI."""
    env_file = BACKEND / ".env"
    if not env_file.exists():
        shutil.copyfile(BACKEND / ".env.example", env_file)
        say("Created backend/.env (gitignored)")
    text = env_file.read_text(encoding="utf-8")
    missing = [(k, label) for k, label in KEYS if re.search(rf"^{k}=\s*$", text, re.MULTILINE)]
    if not missing:
        return
    if not (prompt and sys.stdin.isatty() and sys.stdout.isatty()):
        say("No keys in backend/.env. Paste them in the studio (Keys, top bar) or edit backend/.env.")
        return
    say("API keys. Paste each one, or press Enter to skip and paste it later in the studio (Keys, top bar).")
    for key, label in missing:
        value = getpass.getpass(f"    {label}: ").strip()
        if value:
            text = re.sub(rf"^{key}=\s*$", f"{key}={value}", text, count=1, flags=re.MULTILINE)
    env_file.write_text(text, encoding="utf-8")


def installed() -> bool:
    return VENV_PYTHON.exists() and (FRONTEND / "node_modules").exists()


def setup(prompt: bool = True) -> None:
    say("Backend: Python virtualenv in backend/.venv")
    if not VENV_PYTHON.exists():
        run([*find_python(), "-m", "venv", str(VENV)])
    run([str(VENV_PYTHON), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    run([str(VENV_PYTHON), "-m", "pip", "install", "--quiet",
         "-r", "requirements.txt", "-r", "requirements-dev.txt"], cwd=BACKEND)
    say("Frontend: Agent Studio dependencies")
    cmd, env = pnpm()
    run([*cmd, "install", "--frozen-lockfile"], cwd=FRONTEND, env=env)
    ask_keys(prompt)
    say("Setup done. Start everything with: make start   (or: python scripts/studio.py start)")


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def spawn(cmd: list[str], cwd: Path, log: Path, env: dict) -> subprocess.Popen:
    """A child in its own process group, so stopping it also stops what it started."""
    extra = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS
             else {"start_new_session": True})
    return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log.open("w", encoding="utf-8"),
                            stderr=subprocess.STDOUT, **extra)


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        os.killpg(proc.pid, signal.SIGTERM)


def wait_for(check, what: str, procs: dict[str, tuple[subprocess.Popen, Path]], timeout_s: float):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for name, (proc, log) in procs.items():
            if proc.poll() is not None:
                tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
                fail(f"the {name} stopped while starting. Last lines of {log.relative_to(ROOT)}:\n"
                     + "\n".join(tail))
        if result := check():
            return result
        time.sleep(0.5)
    fail(f"{what} did not start within {timeout_s:.0f} s")


def backend_up():
    try:
        with urllib.request.urlopen(f"http://localhost:{BACKEND_PORT}/api/agents", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def studio_url(log: Path):
    """Vite prints its URL; it moves to the next port when 5173 is taken."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", log.read_text(encoding="utf-8", errors="replace"))
    match = re.search(r"Local:\s+(http://localhost:\d+/?)", text)
    return match.group(1) if match else None


def start(open_browser: bool = True) -> None:
    if not installed():
        say("First run: installing everything")
        setup()
    if port_in_use(BACKEND_PORT):
        fail(f"port {BACKEND_PORT} is in use; is another backend already running? Stop it and try again.")
    LOGS.mkdir(exist_ok=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    backend_log, studio_log = LOGS / "backend.log", LOGS / "studio.log"
    procs: dict[str, tuple[subprocess.Popen, Path]] = {}
    try:
        say("Starting the voice agent backend (port 7860)")
        procs["backend"] = (spawn([str(VENV_PYTHON), "backend/bot.py"], ROOT, backend_log, env), backend_log)
        cmd, pnpm_env = pnpm()
        say("Starting Agent Studio")
        procs["studio"] = (spawn([*cmd, "dev"], FRONTEND, studio_log, {**pnpm_env, "BROWSER": "none", "NO_COLOR": "1"}), studio_log)
        wait_for(backend_up, "the backend", procs, 180)
        url = wait_for(lambda: studio_url(studio_log), "Agent Studio", procs, 60)
        say(f"Ready: {url}")
        print("    Agents in the sidebar: Clinic Scheduler (SF catalog) and National Scheduler.")
        print("    Keys: top bar, if you skipped them at setup. Logs: .studio/backend.log, .studio/studio.log")
        print("    Press Ctrl+C to stop both.", flush=True)
        if open_browser:
            webbrowser.open(url)
        while all(proc.poll() is None for proc, _ in procs.values()):
            time.sleep(1)
        name = next(n for n, (proc, _) in procs.items() if proc.poll() is not None)
        fail(f"the {name} stopped; see .studio/{name}.log")
    except KeyboardInterrupt:
        print()
        say("Stopping")
    finally:
        for proc, _ in procs.values():
            stop(proc)


def main() -> None:
    command, flags = (sys.argv[1] if len(sys.argv) > 1 else ""), set(sys.argv[2:])
    if command == "setup":
        setup(prompt="--no-keys" not in flags)
    elif command == "start":
        start(open_browser="--no-browser" not in flags)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
