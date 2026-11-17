"""Build the fixed sandbox source and save its immutable local image allowlist."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    from app.config import Settings

    settings = Settings()
    directory = settings.data_dir / "sandbox"
    directory.mkdir(parents=True, exist_ok=True)
    identity_file = directory / f"{uuid4().hex}.iid"
    try:
        subprocess.run(
            [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "--file",
                "deploy/sandbox/Dockerfile",
                "--tag",
                "afds-sandbox:0.1.0",
                "--iidfile",
                str(identity_file),
                ".",
            ],
            cwd=ROOT,
            check=True,
        )
        image = identity_file.read_text().strip()
        if not image.startswith("sha256:") or len(image) != 71:
            raise ValueError("Docker did not return an immutable image identity")
        destination = directory / "image.json"
        temporary = directory / f"{uuid4().hex}.partial"
        try:
            temporary.write_text(
                json.dumps({"image_id": image, "policy_version": "afds-container-v1"}),
                encoding="utf-8",
            )
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        print(f"Sandbox image allowlisted as {image}")
    finally:
        identity_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
