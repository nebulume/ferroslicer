"""
Moonraker REST API client for Klipper printer integration.

Moonraker typically runs on port 7125. If port 80 is specified (e.g. nginx proxy),
we try the Fluidd/Mainsail API paths. Auto-falls back between native and proxied paths.
"""

import json
import os
from pathlib import Path
from typing import Optional, Dict, Any


class MoonrakerClient:
    """Thin REST client for Moonraker / Klipper."""

    def __init__(self, host: str = "192.168.1.65", port: int = 80):
        self.host = host
        self.port = port
        self._base = f"http://{host}:{port}" if port != 80 else f"http://{host}"

    def _get(self, path: str, timeout: float = 5.0) -> Optional[Dict]:
        try:
            import requests
            r = requests.get(f"{self._base}{path}", timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            return None

    def _post(self, path: str, data=None, files=None, timeout: float = 30.0) -> Optional[Dict]:
        try:
            import requests
            if files:
                r = requests.post(f"{self._base}{path}", files=files, timeout=timeout)
            elif data:
                r = requests.post(
                    f"{self._base}{path}",
                    json=data,
                    headers={"Content-Type": "application/json"},
                    timeout=timeout,
                )
            else:
                r = requests.post(f"{self._base}{path}", timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            return None

    def check_connection(self) -> bool:
        """Returns True if Moonraker is reachable."""
        result = self._get("/printer/info", timeout=4.0)
        if result is not None:
            return True
        # Try alternate path for proxied setups
        result = self._get("/api/version", timeout=4.0)
        return result is not None

    def get_printer_status(self) -> Dict[str, Any]:
        """Return printer objects including print_stats."""
        result = self._get(
            "/printer/objects/query?print_stats&toolhead&heater_bed&extruder"
        )
        if result and "result" in result:
            return result["result"].get("status", {})
        return {}

    def upload_file(self, gcode_path: str, subdir: str = "") -> Optional[str]:
        """
        Upload a GCode file to Moonraker.
        Returns the filename as registered by Moonraker, or None on failure.
        """
        filename = Path(gcode_path).name
        try:
            import requests
            with open(gcode_path, "rb") as f:
                files = {
                    "file": (filename, f, "application/octet-stream"),
                    "path": (None, subdir) if subdir else None,
                    "root": (None, "gcodes"),
                }
                # Remove None entries
                files = {k: v for k, v in files.items() if v is not None}
                r = requests.post(
                    f"{self._base}/server/files/upload",
                    files=files,
                    timeout=60,
                )
                r.raise_for_status()
                data = r.json()
                return data.get("item", {}).get("path", filename)
        except Exception as e:
            return None

    def start_print(self, filename: str) -> bool:
        """Tell Moonraker to start printing the given filename."""
        result = self._post(f"/printer/print/start?filename={filename}")
        return result is not None

    def cancel_print(self) -> bool:
        result = self._post("/printer/print/cancel")
        return result is not None

    def pause_print(self) -> bool:
        result = self._post("/printer/print/pause")
        return result is not None

    def resume_print(self) -> bool:
        result = self._post("/printer/print/resume")
        return result is not None

    def get_print_state(self) -> str:
        """Returns one of: standby, printing, paused, complete, error, or 'unknown'."""
        status = self.get_printer_status()
        ps = status.get("print_stats", {})
        return ps.get("state", "unknown")

    def get_progress(self) -> float:
        """Returns print progress 0.0-1.0, or 0 if not printing."""
        status = self.get_printer_status()
        ps = status.get("print_stats", {})
        total = ps.get("total_duration", 0)
        done = ps.get("print_duration", 0)
        if total > 0:
            return min(1.0, done / total)
        return 0.0
