"""Build the Windows x64 installer from the prebuilt frontend and locked wheels."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from zipfile import ZipFile

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import APP_VERSION

PYTHON_VERSION = "3.13.17"
PYTHON_HASH = "9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38"
DEV_DEPENDENCIES = {"pytest", "ruff", "pluggy", "iniconfig", "pygments"}


def sha256(path: Path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def python_archive(cache: Path):
    archive = cache / f"python-{PYTHON_VERSION}-embed-amd64.zip"
    if not archive.is_file():
        url = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{archive.name}"
        with (
            urllib.request.urlopen(url, timeout=90) as response,
            archive.open("xb") as target,
        ):
            shutil.copyfileobj(response, target)
    if sha256(archive) != PYTHON_HASH:
        raise RuntimeError(f"Python archive checksum mismatch: {archive}")
    return archive


def install_python(payload: Path, work: Path, cache: Path, wheelhouse: Path | None):
    runtime = payload / "runtime"
    with ZipFile(python_archive(cache)) as package:
        package.extractall(runtime)
    (runtime / "python313._pth").write_text(
        "python313.zip\n.\n../backend\nLib/site-packages\nimport site\n",
        encoding="ascii",
    )
    requirements = work / "requirements-runtime.txt"
    runtime_lines = [
        line
        for line in (ROOT / "backend/requirements.lock")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
        and not line.startswith("#")
        and line.partition("==")[0] not in DEV_DEPENDENCIES
    ]
    requirements.write_text("\n".join(runtime_lines) + "\n", encoding="utf-8")
    dependencies = work / "dependencies"
    pip = [
        sys.executable,
        "-m",
        "pip",
        "--cache-dir",
        str(ROOT / ".cache/pip"),
        "install",
        "--no-compile",
        "--only-binary=:all:",
        "--platform=win_amd64",
        "--python-version=3.13",
        "--implementation=cp",
        "--target",
        str(dependencies),
        "-r",
        str(requirements),
    ]
    if wheelhouse:
        pip.extend(["--no-index", "--find-links", str(wheelhouse)])
    subprocess.run(pip, check=True)
    # pip's command stubs record the builder's Python executable. The app uses modules.
    shutil.copytree(
        dependencies,
        runtime / "Lib/site-packages",
        ignore=lambda directory, files: (
            {"bin", "Scripts"}.intersection(files)
            if Path(directory) == dependencies
            else ()
        ),
    )


def copy_frontend_notices(payload: Path):
    root = ROOT / "frontend"
    packages = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))[
        "packages"
    ]
    notices = payload / "third-party" / "frontend"
    for relative, info in packages.items():
        if not relative or info.get("dev") or not (root / relative).is_dir():
            continue
        for file in (root / relative).iterdir():
            if file.is_file() and file.name.lower().startswith(
                ("licen", "copying", "notice")
            ):
                target = notices / relative / file.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)


def create_icon(path: Path):
    icon = Image.new("RGBA", (256, 256))
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((6, 6, 250, 250), radius=56, fill="#171c25")
    draw.rounded_rectangle((48, 96, 208, 194), radius=12, fill="#e9b471")
    draw.polygon(((50, 96), (41, 68), (194, 37), (203, 69)), fill="#e9b471")
    for x in (61, 112, 163):
        y = 65 - (x - 61) // 5
        draw.line(((x, y), (x + 25, y + 21)), fill="#171c25", width=9)
    draw.polygon(((112, 120), (112, 171), (153, 146)), fill="#171c25")
    icon.save(
        path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (128, 128), (256, 256)]
    )


def build(compiler: Path, wheelhouse: Path | None, release: Path):
    build_root = ROOT / "build/windows"
    build_root.mkdir(parents=True, exist_ok=True)
    cache = ROOT / "build/windows-tools"
    cache.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="stage-", dir=build_root))
    payload = work / "payload"
    payload.mkdir()
    exclusions = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise RuntimeError("Build the frontend with npm run build first.")
    shutil.copytree(ROOT / "frontend/dist", payload / "frontend/dist")
    shutil.copytree(ROOT / "backend/app", payload / "backend/app", ignore=exclusions)
    shutil.copytree(
        ROOT / "backend/migrations", payload / "backend/migrations", ignore=exclusions
    )
    shutil.copyfile(ROOT / "backend/alembic.ini", payload / "backend/alembic.ini")
    shutil.copyfile(ROOT / "docs/WINDOWS.md", payload / "安装与使用.md")
    install_python(payload, work, cache, wheelhouse)
    copy_frontend_notices(payload)
    icon = work / "AFDS.ico"
    create_icon(icon)
    version = work / "Version.cs"
    version.write_text(
        f'[assembly: System.Reflection.AssemblyVersion("{APP_VERSION}.0")]\n'
        f'[assembly: System.Reflection.AssemblyFileVersion("{APP_VERSION}.0")]\n',
        encoding="ascii",
    )
    csc = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run(
        [
            str(csc),
            "/nologo",
            "/target:winexe",
            "/platform:x64",
            "/optimize+",
            "/codepage:65001",
            "/reference:System.Drawing.dll",
            "/reference:System.Windows.Forms.dll",
            f"/out:{payload / 'AFDS.exe'}",
            f"/win32icon:{icon}",
            str(ROOT / "deploy/windows/Launcher.cs"),
            str(version),
        ],
        check=True,
    )
    subprocess.run(
        [
            str(payload / "runtime/python.exe"),
            "-B",
            "-c",
            "import app.desktop, uvicorn, numpy, opentimelineio; print('Windows runtime ready')",
        ],
        check=True,
    )
    release.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(compiler),
            f"/DAppVersion={APP_VERSION}",
            f"/DPayloadDir={payload}",
            f"/DLauncherIcon={icon}",
            f"/DReleaseDir={release}",
            str(ROOT / "deploy/windows/AFDS.iss"),
        ],
        check=True,
    )
    installer = release / f"AFDS-Studio-{APP_VERSION}-Windows-x64-Setup.exe"
    checksum = installer.with_suffix(".exe.sha256")
    checksum.write_text(f"{sha256(installer)}  {installer.name}\n", encoding="ascii")
    print(f"Installer: {installer}")
    print(f"SHA-256:   {checksum}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Build the Windows installer on Windows")
    build(
        args.iscc.resolve(),
        args.wheelhouse.resolve() if args.wheelhouse else None,
        args.output.resolve(),
    )


if __name__ == "__main__":
    main()
