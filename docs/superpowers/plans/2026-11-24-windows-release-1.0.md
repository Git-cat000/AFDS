# AFDS 1.0 Windows Release

**Intent:** Deliver AFDS Studio as a one-click Windows x64 install with a refined workspace and publish version 1.0.0 on the existing GitHub repository.

**Architecture:** A Windows launcher starts the desktop API on loopback and supervises an independent Worker. The API serves the compiled React workspace from the same origin. The setup program installs a private Python runtime and can download a pinned FFmpeg build directly from its distributor. SQLite and workspace files live outside the installation directory.

**Choices:** Keep the browser workspace to reuse the tested Studio and avoid shipping another browser engine. Use the official embeddable Python runtime to preserve normal imports, Alembic migrations, and approved Python scripts. Use a per-user install and a Start menu shortcut so setup requires neither administrator privileges nor a global Python or Node installation.

- [x] Verify dependencies, protect workspace data from setup/uninstall, and document the FFmpeg download option.
- [x] Refine the project home and the Studio visual system while preserving accessible controls and existing editing actions.
- [x] Build versioned sources and a Windows setup executable with a checksum. Test a clean installation in an isolated path, start the installed services, produce a Mock film, check persistence after restart and remove only the test program files with the uninstaller.
- [x] Run backend, Ruff, frontend and browser checks. Keep Windows local acceptance distinct from pending team production and real-model acceptance.

The [GitHub release](https://github.com/Git-cat000/AFDS/releases/tag/v1.0.0) is the distribution point for the installer and its checksum.
