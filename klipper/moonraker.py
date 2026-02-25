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
        """Return printer objects including print_stats and virtual_sdcard."""
        result = self._get(
            "/printer/objects/query"
            "?print_stats&virtual_sdcard&display_status&toolhead&heater_bed&extruder"
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
        """Returns print progress 0.0-1.0 from virtual_sdcard file position."""
        status = self.get_printer_status()
        # virtual_sdcard.progress is the authoritative GCode file-position percentage
        vsd = status.get("virtual_sdcard", {})
        prog = vsd.get("progress", None)
        if prog is not None:
            return float(prog)
        # Fallback: display_status.progress (set by M73 in GCode)
        ds = status.get("display_status", {})
        prog = ds.get("progress", None)
        if prog is not None:
            return float(prog)
        return 0.0

    def get_rich_status(self) -> Dict[str, Any]:
        """Return a single-call status dict with progress, temps, state, and elapsed time.

        Keys returned:
            state (str)        — printing / paused / complete / standby / error / unknown
            progress (float)   — 0.0–1.0 from virtual_sdcard file position
            nozzle_temp (float)
            nozzle_target (float)
            bed_temp (float)
            bed_target (float)
            print_duration (float) — seconds actively printing (excludes pauses)
            filename (str)
        """
        status = self.get_printer_status()
        ps  = status.get("print_stats", {})
        vsd = status.get("virtual_sdcard", {})
        ds  = status.get("display_status", {})
        ext = status.get("extruder", {})
        bed = status.get("heater_bed", {})

        # Progress: virtual_sdcard is most accurate; fall back to display_status
        progress = float(vsd.get("progress") or ds.get("progress") or 0.0)

        return {
            "state":          ps.get("state", "unknown"),
            "progress":       progress,
            "nozzle_temp":    float(ext.get("temperature", 0)),
            "nozzle_target":  float(ext.get("target", 0)),
            "bed_temp":       float(bed.get("temperature", 0)),
            "bed_target":     float(bed.get("target", 0)),
            "print_duration": float(ps.get("print_duration", 0)),
            "filename":       ps.get("filename", ""),
        }
