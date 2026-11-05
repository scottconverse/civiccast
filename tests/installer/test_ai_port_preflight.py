"""F10: setup warns when another program already holds the AI engine's port.

RC4 field run, Beelink (2026-11-04): a separately installed Ollama held
127.0.0.1:11434. The station now survives that (AI off, service healthy, the
supervisor log names the program), but setup said nothing, so the operator only
found out later from missing summaries/translation. The installer now checks the
port after stopping any old CivicCast service and warns -- it never refuses the
install, because the station runs fine without AI.
"""

from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC_TAURI = ROOT / "civiccast" / "apps" / "installer" / "src-tauri"
SCRIPT = SRC_TAURI / "ai-port-preflight.ps1"
HOOKS = SRC_TAURI / "nsis-hooks-bootstrap.nsh"
POWERSHELL = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / (
    r"System32\WindowsPowerShell\v1.0\powershell.exe"
)

windows_only = pytest.mark.skipif(os.name != "nt", reason="runs the real Windows preflight script")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _own_process_dir() -> str:
    # The listening socket below belongs to THIS process; ask Windows for its
    # image path (sys.executable can be a venv launcher, not the real image).
    out = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-Command", f"(Get-Process -Id {os.getpid()}).Path"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout.strip()
    return str(Path(out).parent)


@windows_only
def test_foreign_listener_is_named_and_flagged() -> None:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        ran = _run("-Port", str(port), "-OwnInstallRoot", r"C:\NoSuchCivicCastRoot")
    assert ran.returncode == 10, ran.stdout + ran.stderr
    assert f"port {port}" in ran.stdout
    assert "python" in ran.stdout.lower(), "the warning must name the program holding the port"
    assert f"pid {os.getpid()}" in ran.stdout


@windows_only
def test_free_port_passes_quietly() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    ran = _run("-Port", str(port), "-OwnInstallRoot", r"C:\NoSuchCivicCastRoot")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "free" in ran.stdout


@windows_only
def test_civiccast_own_listener_is_not_flagged() -> None:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        ran = _run("-Port", str(port), "-OwnInstallRoot", _own_process_dir())
    assert ran.returncode == 0, ran.stdout + ran.stderr


def _macro() -> str:
    source = HOOKS.read_text(encoding="utf-8")
    start = source.index("!macro CIVICCAST_AI_PORT_PREFLIGHT")
    return source[start : source.index("!macroend", start)]


def _real_macro(header: str) -> str:
    source = HOOKS.read_text(encoding="utf-8")
    start = source.index(header)
    return source[start : source.index("!macroend", start)] + "!macroend\n"


def test_preflight_runs_after_old_service_is_stopped_and_before_files_change() -> None:
    source = HOOKS.read_text(encoding="utf-8")
    start = source.index("!macro NSIS_HOOK_PREINSTALL")
    pre = source[start : source.index("!macroend", start)]
    call = pre.index("!insertmacro CIVICCAST_AI_PORT_PREFLIGHT")
    # The old station's own Ollama must be gone first, or an upgrade would
    # warn about CivicCast itself.
    assert pre.index("--civiccast-stop-native-service") < call
    assert pre.index('taskkill.exe /IM "CivicCast Native.exe"') < call
    assert call < pre.index("CIVICCAST_TRANSACTION_SNAPSHOT")


def test_preflight_only_warns_and_never_stops_setup() -> None:
    macro = _macro()
    for refusal in ("Quit", "SetErrorLevel", "CIVICCAST_FAIL", "Abort"):
        assert refusal not in macro, f"AI port preflight must be advisory; found {refusal}"
    # CIVICCAST_NOTICE logs + DetailPrints always and shows a dialog only when
    # NOT silent, so an unattended /S install never blocks on it.
    assert "!insertmacro CIVICCAST_NOTICE" in macro
    assert "MessageBox" not in macro, "dialogs must go through the silent-safe CIVICCAST_NOTICE"
    assert "CIVICCAST_STEP" in macro, "every outcome must reach install-progress.log"
    assert "11434" in macro


@windows_only
@pytest.mark.parametrize("incoming_errors", [False, True])
def test_macro_compiles_warns_and_preserves_caller_state(
    tmp_path: Path, incoming_errors: bool
) -> None:
    makensis = Path.home() / "AppData/Local/tauri/NSIS/Bin/makensis.exe"
    if not makensis.is_file():
        pytest.skip("Tauri NSIS compiler is unavailable")
    log = tmp_path / "step.log"
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        macro = (_macro() + "!macroend\n").replace("-Port 11434", f"-Port {port}")
        output = tmp_path / "ai-port.exe"
        script = tmp_path / "ai-port.nsi"
        script.write_text(
            "Unicode true\n!include LogicLib.nsh\nSilentInstall silent\n"
            f'RequestExecutionLevel user\nOutFile "{output}"\n'
            f'!define CIVICCAST_AI_PORT_PREFLIGHT_SCRIPT "{SCRIPT}"\n'
            "!macro CIVICCAST_STEP TEXT\n"
            f'  FileOpen $R8 "{log}" a\n  FileSeek $R8 0 END\n  FileWrite $R8 "${{TEXT}}$\\r$\\n"\n'
            "  FileClose $R8\n!macroend\n"
            + _real_macro("!macro CIVICCAST_NOTICE TEXT")
            + macro
            + '\nSection\nSetOutPath "$EXEDIR\\payload"\n'
            'StrCpy $0 "caller-zero"\nStrCpy $1 "caller-one"\n'
            + ("SetErrors\n" if incoming_errors else "ClearErrors\n")
            + "!insertmacro CIVICCAST_AI_PORT_PREFLIGHT\n"
            '${If} $OUTDIR != "$EXEDIR\\payload"\nSetErrorLevel 72\nQuit\n${EndIf}\n'
            '${If} $0 != "caller-zero"\nSetErrorLevel 73\nQuit\n${EndIf}\n'
            '${If} $1 != "caller-one"\nSetErrorLevel 74\nQuit\n${EndIf}\n'
            + ("${IfNot} ${Errors}\n" if incoming_errors else "${If} ${Errors}\n")
            + "SetErrorLevel 75\nQuit\n${EndIf}\nSetErrorLevel 0\nSectionEnd\n",
            encoding="utf-8",
        )
        compiled = subprocess.run(
            [str(makensis), "/V2", str(script)], capture_output=True, text=True, timeout=60
        )
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
        ran = subprocess.run([str(output), "/S"], capture_output=True, timeout=60)
    assert ran.returncode == 0, f"72=OUTDIR, 73/74=registers, 75=Errors; got {ran.returncode}"
    logged = log.read_text(encoding="utf-8", errors="replace")
    assert f"AI port check returned 10: CivicCast AI port preflight: port {port} is held" in logged
    assert "NOTICE: WARNING: another program is already using port 11434" in logged
