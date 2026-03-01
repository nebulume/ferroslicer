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

    @staticmethod
    def _curl_get(url: str, timeout: float = 5.0) -> Optional[Dict]:
        """GET via system curl — works when macOS Local Network privacy blocks Python sockets."""
        try:
            import subprocess, json as _json
            res = subprocess.run(
                ["/usr/bin/curl", "-sf", "--max-time", str(int(timeout) + 1),
                 "--connect-timeout", "5", url],
                capture_output=True, text=True, timeout=int(timeout) + 3,
            )
            if res.returncode == 0 and res.stdout.strip():
                return _json.loads(res.stdout)
        except Exception:
            pass
        return None

    @staticmethod
    def _curl_post(url: str, data=None, timeout: float = 30.0) -> Optional[Dict]:
        """POST via system curl for JSON or empty-body requests."""
        try:
            import subprocess, json as _json
            cmd = ["/usr/bin/curl", "-sf", "--max-time", str(int(timeout) + 1),
                   "--connect-timeout", "5", "-X", "POST"]
            if data:
                cmd += ["-H", "Content-Type: application/json",
                        "-d", _json.dumps(data)]
            cmd.append(url)
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=int(timeout) + 3)
            if res.returncode == 0:
                return _json.loads(res.stdout) if res.stdout.strip() else {}
        except Exception:
            pass
        return None

    def _get(self, path: str, timeout: float = 5.0) -> Optional[Dict]:
        url = f"{self._base}{path}"
        # Primary: requests
        try:
            import requests
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception:
            pass
        # Fallback: system curl. Bypasses macOS Local Network privacy blocking
        # that prevents Python sockets from reaching LAN hosts in a frozen .app.
        return self._curl_get(url, timeout)

    def _post(self, path: str, data=None, files=None, timeout: float = 30.0) -> Optional[Dict]:
        url = f"{self._base}{path}"
        try:
            import requests
            if files:
                r = requests.post(url, files=files, timeout=timeout)
            elif data:
                r = requests.post(url, json=data,
                                  headers={"Content-Type": "application/json"},
                                  timeout=timeout)
            else:
                r = requests.post(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception:
            pass
        # Fallback: curl (file uploads handled separately in upload_file)
        if not files:
            return self._curl_post(url, data=data, timeout=timeout)
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
        url = f"{self._base}/server/files/upload"

        # Primary: requests multipart upload
        try:
            import requests
            with open(gcode_path, "rb") as f:
                files = {
                    "file": (filename, f, "application/octet-stream"),
                    "root": (None, "gcodes"),
                }
                if subdir:
                    files["path"] = (None, subdir)
                r = requests.post(url, files=files, timeout=60)
                r.raise_for_status()
                return r.json().get("item", {}).get("path", filename)
        except Exception:
            pass

        # Fallback: curl multipart (works when macOS blocks Python sockets in .app)
        try:
            import subprocess, json as _json
            cmd = ["/usr/bin/curl", "-sf", "--max-time", "65", "--connect-timeout", "5",
                   "-F", f"file=@{gcode_path};type=application/octet-stream",
                   "-F", "root=gcodes"]
            if subdir:
                cmd += ["-F", f"path={subdir}"]
            cmd.append(url)
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=68)
            if res.returncode == 0 and res.stdout.strip():
                return _json.loads(res.stdout).get("item", {}).get("path", filename)
        except Exception:
            pass

        return None

    def start_print(self, filename: str) -> bool:
        """Tell Moonraker to start printing the given filename."""
        result = self._post(f"/printer/print/start?filename={filename}")
        return result is not None

    def set_temperatures(self, nozzle_c: float, bed_c: float) -> bool:
        """Set nozzle and bed target temperatures without waiting (M104 + M140)."""
        script = f"M104 S{int(nozzle_c)}\nM140 S{int(bed_c)}"
        result = self._post("/printer/gcode/script", data={"script": script})
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
