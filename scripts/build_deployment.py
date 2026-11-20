"""Build deployment targets with explicit upstream digests and local receipts."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def immutable_image(value):
    if not re.fullmatch(r"[a-zA-Z0-9._:/-]+@sha256:[a-f0-9]{64}", value):
        raise argparse.ArgumentTypeError(
            "Supply an approved registry image pinned by sha256 digest"
        )
    return value


def main():
    parser = argparse.ArgumentParser(
        description="Build AFDS web/runtime deployment images"
    )
    parser.add_argument("--nginx-image", required=True, type=immutable_image)
    parser.add_argument("--docker-cli-image", type=immutable_image)
    args = parser.parse_args()
    output = ROOT / "data/deployment"
    output.mkdir(parents=True, exist_ok=True)
    files = [
        ROOT / "deploy/Dockerfile",
        ROOT / "deploy/nginx.conf",
        ROOT / ".dockerignore",
        ROOT / "backend/requirements.lock",
        ROOT / "backend/alembic.ini",
    ]
    for name in ("backend/app", "backend/migrations", "frontend/src"):
        files.extend(
            path
            for path in (ROOT / name).rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
        )
    files.extend(
        path
        for path in (ROOT / "frontend").iterdir()
        if path.name
        in {
            "package.json",
            "package-lock.json",
            "index.html",
            "vite.config.ts",
            "tsconfig.json",
        }
    )
    source = hashlib.sha256()
    for path in sorted(files):
        source.update(
            path.relative_to(ROOT).as_posix().encode()
            + b"\0"
            + path.read_bytes()
            + b"\0"
        )
    receipt = {
        "source_sha256": source.hexdigest(),
        "upstream": vars(args),
        "images": {},
    }
    for target in ["backend", "web"] + (
        ["script-controller"] if args.docker_cli_image else []
    ):
        iid = output / f"{uuid4().hex}.iid"
        try:
            command = [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "--file",
                "deploy/Dockerfile",
                "--target",
                target,
                "--tag",
                "afds-" + target + ":deployment",
                "--iidfile",
                str(iid),
                "--build-arg",
                "AFDS_NGINX_IMAGE=" + args.nginx_image,
            ]
            if args.docker_cli_image:
                command += [
                    "--build-arg",
                    "AFDS_DOCKER_CLI_IMAGE=" + args.docker_cli_image,
                ]
            subprocess.run(command + ["."], cwd=ROOT, check=True)
            identity = iid.read_text().strip()
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", identity):
                raise ValueError("Docker did not return an immutable image identity")
            description = json.loads(
                subprocess.check_output(["docker", "image", "inspect", identity])
            )[0]
            if description["Id"] != identity or description["Os"] != "linux":
                raise ValueError("Built image identity does not match Linux receipt")
            receipt["images"][target] = identity
        finally:
            iid.unlink(missing_ok=True)
    temporary = output / "images.partial"
    temporary.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    temporary.replace(output / "images.json")
    print("Recorded immutable deployment identities in data/deployment/images.json")


if __name__ == "__main__":
    main()
