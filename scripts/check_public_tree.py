"""Reject obvious local data and credential patterns before public packaging."""

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_NAMES = {"ha_access.json", "fetch_out.json", "adbkey", "adbkey.pub", "secrets.yaml"}
FORBIDDEN_NAMES |= {
    "history-live-readings.json", "history-private-cache.json", "history-physical-events.json",
}
FORBIDDEN_SUFFIXES = {".apk", ".apks", ".dex", ".so", ".pcap", ".pcapng", ".pem", ".key"}
PATTERNS = {
    "JWT token": re.compile(rb"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    "private HA remote hostname": re.compile(rb"https?://[a-z0-9]{20,}\.ui\.nabu\.casa", re.I),
    "Windows user path": re.compile(rb"[A-Z]:[\\/]+Users[\\/]+", re.I),
}


def main() -> int:
    """Inspect only repository-visible files, without printing matched secrets."""
    result = subprocess.run(
        ["git", "ls-files", "-c", "-o", "--exclude-standard", "-z"],
        cwd=ROOT, check=True, capture_output=True,
    )
    failures = []
    paths = sorted(set(result.stdout.decode().strip("\0").split("\0")))
    for name in paths:
        path = ROOT / name
        if not path.is_file():
            continue
        if (
            path.name in FORBIDDEN_NAMES or path.suffix in FORBIDDEN_SUFFIXES
            or ".storage" in path.parts or "eufy_lock_ble_captures" in path.parts
        ):
            failures.append((name, "local/private file type"))
        if path.suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            data = path.read_bytes()
            for label, pattern in PATTERNS.items():
                if pattern.search(data):
                    failures.append((name, label))
    for name, label in failures:
        print(f"FAIL {name}: {label}")
    if failures:
        return 1
    print(f"PASS: {len(paths)} repository-visible files; no blocked local data patterns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
