"""Build the trusted Linux runtime and record its immutable image identity."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    from app import APP_VERSION
    from app.config import Settings

    directory = Settings().data_dir / "worker"
    directory.mkdir(parents=True, exist_ok=True)
    identity_file = directory / f"{uuid4().hex}.iid"
    temporary = directory / f"{uuid4().hex}.partial"
    try:
        subprocess.run(
            [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "--target",
                "backend",
                "--file",
                "deploy/Dockerfile",
                "--tag",
                f"afds-runtime:{APP_VERSION}",
                "--build-arg",
                f"AFDS_APP_VERSION={APP_VERSION}",
                "--iidfile",
                str(identity_file),
                ".",
            ],
            cwd=ROOT,
            check=True,
        )
        identity = identity_file.read_text().strip()
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", identity):
            raise ValueError("Docker did not return an immutable runtime identity")
        description = json.loads(
            subprocess.check_output(["docker", "image", "inspect", identity])
        )[0]
        if (
            description["Id"] != identity
            or description["Os"] != "linux"
            or description["Config"].get("Labels", {}).get("io.afds.runtime")
            != "afds-worker-v1"
        ):
            raise ValueError("The runtime image identity or platform differs")
        temporary.write_text(
            json.dumps(
                {
                    "image_id": identity,
                    "app_version": APP_VERSION,
                    "protocol": "afds-worker-v1",
                }
            ),
            encoding="utf-8",
        )
        os.replace(temporary, directory / "image.json")
        print(f"AFDS Linux runtime built as {identity}")
    finally:
        identity_file.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
