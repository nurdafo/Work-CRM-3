"""Build the existing Vite frontend before packaging the FastAPI function."""

from pathlib import Path
import os
import subprocess


root = Path(__file__).resolve().parent
npm = "npm.cmd" if os.name == "nt" else "npm"
subprocess.run([npm, "ci"], cwd=root / "web", check=True)
subprocess.run([npm, "run", "build"], cwd=root / "web", check=True)
