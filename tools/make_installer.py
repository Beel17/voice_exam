"""Build setup.exe: installs the voice exam service on a Windows PC, as a Windows service.

    uv run python -m tools.make_bundle --out build/installer/voice-exam
    uv run python -m tools.make_installer      # needs Inno Setup 6 (jrsoftware.org)

Takes the program from a built bundle (its Python, libraries, models and WinSW) and writes
dist/voice-exam-setup-<version>.exe. Installing it, which needs administrator rights:
puts the program in Program Files; puts everything that changes in ProgramData\\VoiceExamVoiceOnly
(the configuration and device token, written on first install and kept from then on;
sessions; logs); installs and starts the Windows service; adds Start-menu shortcuts to
the exam page, the status page, the device token and the logs. Uninstalling stops and
removes the service and the program, and keeps ProgramData\\VoiceExamVoiceOnly: it may hold
answers not yet delivered to the platform. Installing again over it is an upgrade.
"""

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Final

from tools.make_bundle import SERVICE_EXE, SERVICE_ID, service_xml

ROOT = Path(__file__).parents[1]
ISCC: Final = Path.home() / "AppData" / "Local" / "Programs" / "Inno Setup 6" / "ISCC.exe"
# Identifies this product to Windows across versions, so a new setup.exe upgrades it.
APP_ID: Final = "{3B7E1D94-6A2C-4F58-8D03-91C5E7A2B6F4}"
DATA: Final = r"{commonappdata}\VoiceExamVoiceOnly"  # Inno Setup's name for C:\ProgramData
PAGE: Final = "http://127.0.0.1:8775/"
PROGRAM_PARTS: Final = ("python", "lib", "tools", "models")


def script(bundle: Path, stage: Path, version: str) -> str:
    """The Inno Setup script."""
    files = "\n".join(
        f'Source: "{bundle / part}\\*"; DestDir: "{{app}}\\{part}"; '
        'Excludes: "__pycache__,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs'
        for part in PROGRAM_PARTS
    )
    service = f'"{{app}}\\{SERVICE_EXE}"'
    # Inno Setup doubles a quote inside a quoted value.
    setup_args = f'"-E -s -m tools.setup_centre --data ""{DATA}"""'
    token_file = f'"""{DATA}\\DEVICE-TOKEN.txt"""'
    return rf"""; Written by tools/make_installer.py; do not edit by hand.
[Setup]
AppId={{{APP_ID}
AppName=Voice Exam (voice only)
AppVersion={version}
AppVerName=Voice Exam (voice only) {version}
DefaultDirName={{autopf}}\Voice Exam (voice only)
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={ROOT / "dist"}
OutputBaseFilename=voice-exam-voice-only-setup-{version}
; The models are already compressed: fast compression, nothing to gain from more.
Compression=lzma2/fast
SolidCompression=no
WizardStyle=modern
SetupLogging=yes
UninstallDisplayName=Voice Exam (voice only)
CloseApplications=no

[Dirs]
Name: "{DATA}"

[Files]
{files}
Source: "{bundle / SERVICE_EXE}"; DestDir: "{{app}}"; Flags: ignoreversion
Source: "{stage / "voice-exam-service.xml"}"; DestDir: "{{app}}"; Flags: ignoreversion
Source: "{ROOT / "config" / "centre.example.toml"}"; DestDir: "{{app}}\config"; Flags: ignoreversion

[INI]
Filename: "{{app}}\Voice exam page.url"; Section: "InternetShortcut"; Key: "URL"; String: "{PAGE}"
Filename: "{{app}}\Voice exam status.url"; Section: "InternetShortcut"; Key: "URL"; String: "{PAGE}status"

[Icons]
Name: "{{autoprograms}}\Voice Exam (voice only)\Voice exam page"; Filename: "{{app}}\Voice exam page.url"
Name: "{{autoprograms}}\Voice Exam (voice only)\Voice exam status"; Filename: "{{app}}\Voice exam status.url"
Name: "{{autoprograms}}\Voice Exam (voice only)\Device token"; Filename: "{DATA}\DEVICE-TOKEN.txt"
Name: "{{autoprograms}}\Voice Exam (voice only)\Logs"; Filename: "{DATA}\logs"

[Run]
Filename: "{{app}}\python\python.exe"; Parameters: {setup_args}; WorkingDir: "{{app}}"; StatusMsg: "Setting up this PC..."; Flags: runhidden waituntilterminated
Filename: {service}; Parameters: "install"; StatusMsg: "Installing the service..."; Flags: runhidden waituntilterminated
Filename: {service}; Parameters: "start"; StatusMsg: "Starting the service..."; Flags: runhidden waituntilterminated
Filename: "{PAGE}status"; Description: "Open the status page (the service takes up to a minute to be ready)"; Flags: postinstall shellexec skipifsilent nowait
Filename: "notepad.exe"; Parameters: {token_file}; Description: "Show the device token, to enter in this PC's exam page"; Flags: postinstall skipifsilent nowait unchecked

[UninstallRun]
Filename: {service}; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopService"
Filename: {service}; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"

[UninstallDelete]
; The program folder only (Python's caches are made while running); ProgramData is kept.
Type: filesandordirs; Name: "{{app}}"

[Code]
// An upgrade replaces files the running service holds open: stop it first.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  if FileExists(ExpandConstant('{{app}}\{SERVICE_EXE}')) then
    Exec(ExpandConstant('{{app}}\{SERVICE_EXE}'), 'stop', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--bundle", type=Path, default=ROOT / "build" / "installer" / "voice-exam")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("setup.exe is a Windows installer: build it on Windows")
    bundle: Path = args.bundle.resolve()
    for part in (*PROGRAM_PARTS, SERVICE_EXE):
        if not (bundle / part).exists():
            raise SystemExit(f"{bundle} is not a built bundle (no {part}): run make_bundle first")
    if not ISCC.is_file():
        raise SystemExit(f"Inno Setup 6 not found at {ISCC}")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    stage = ROOT / "build" / "installer"
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "voice-exam-service.xml").write_text(
        service_xml(r"%ProgramData%\VoiceExamVoiceOnly\logs", r"%ProgramData%\VoiceExamVoiceOnly\centre.toml"),
        encoding="utf-8",
    )
    iss = stage / "voice-exam.iss"
    iss.write_text(script(bundle, stage, version), encoding="utf-8")
    print(f"compiling {iss.name} (several minutes: the models are 1.3 GB) ...", flush=True)
    subprocess.run([str(ISCC), "/Q", str(iss)], check=True)
    setup = ROOT / "dist" / f"voice-exam-voice-only-setup-{version}.exe"
    print(f"done: {setup} ({setup.stat().st_size / 2**30:.2f} GB); service {SERVICE_ID}")


if __name__ == "__main__":
    main()
