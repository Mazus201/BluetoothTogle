import json
import logging
import subprocess
import sys
import ctypes
from pathlib import Path


DEVICE_NAME = "Fosi Audio BT20A PRO"
LOG_FILE = Path(__file__).with_name("bluetooth_log.txt")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)


def run_powershell(script):
    """Run PowerShell and raise a useful error instead of silently ignoring it."""
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(message or f"PowerShell exited with code {result.returncode}")
    return result.stdout.strip()


def find_device():
    # Select by stable PnP identity rather than Get-PnpDevice's changing list order.
    escaped_name = DEVICE_NAME.replace("'", "''")
    script = rf'''
$ErrorActionPreference = 'Stop'
$matches = @(Get-PnpDevice -Class Bluetooth |
    Where-Object {{ $_.FriendlyName -like '*{escaped_name}*' }} |
    ForEach-Object {{
        [pscustomobject]@{{
            Status = [string]$_.Status
            FriendlyName = [string]$_.FriendlyName
            InstanceId = [string]$_.InstanceId
        }}
    }})
ConvertTo-Json -InputObject $matches -Compress -Depth 3
'''
    output = run_powershell(script)
    if not output:
        return []
    devices = json.loads(output)
    if isinstance(devices, dict):
        devices = [devices]
    return devices


def choose_device(devices):
    if not devices:
        raise RuntimeError(
            f"Не найдено Bluetooth-устройство с именем «{DEVICE_NAME}». "
            "Проверьте имя в диспетчере устройств и строку DEVICE_NAME в скрипте."
        )

    # AVRCP is the media-control profile. Prefer it when Windows exposes profile
    # nodes separately; otherwise use a stable ID order, never the enumeration order.
    def rank(device):
        instance_id = device.get("InstanceId", "").lower()
        if "0000110e" in instance_id:
            profile_rank = 0
        elif "0000110f" in instance_id:
            profile_rank = 1
        else:
            profile_rank = 2
        return profile_rank, instance_id

    return sorted(devices, key=rank)[0]


def toggle():
    devices = find_device()
    device = choose_device(devices)
    instance_id = device["InstanceId"]
    status = device.get("Status", "")
    if not instance_id:
        raise RuntimeError("Windows вернула запись устройства без InstanceId.")

    # Use PowerShell's literal-string parameter binding; InstanceId is not code.
    operation = "Disable-PnpDevice" if status.lower() == "ok" else "Enable-PnpDevice"
    script = rf'''
$ErrorActionPreference = 'Stop'
{operation} -InstanceId '{instance_id.replace("'", "''")}' -Confirm:$false -ErrorAction Stop
'''
    run_powershell(script)
    logging.info(
        "%s %s (%s); previous status=%s; matching devices=%d",
        operation,
        device.get("FriendlyName", DEVICE_NAME),
        instance_id,
        status or "unknown",
        len(devices),
    )


def relaunch_as_admin():
    """Ask Windows for elevation, required by Disable/Enable-PnpDevice."""
    if ctypes.windll.shell32.IsUserAnAdmin():
        return False

    script_path = str(Path(__file__).resolve())
    working_directory = str(Path(__file__).resolve().parent)
    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        f'"{script_path}"',
        working_directory,
        0,
    )
    if result <= 32:
        raise RuntimeError(f"Windows не смогла запросить права администратора (код {result}).")
    return True


if __name__ == "__main__":
    try:
        if relaunch_as_admin():
            sys.exit(0)
        toggle()
    except Exception as error:
        logging.exception("Bluetooth toggle failed: %s", error)
        print(f"Bluetooth toggle failed: {error}", file=sys.stderr)
        sys.exit(1)
