#!/usr/bin/env python3
"""
yaspeed — CLI speed test via yandex.ru/internet API
Supports: Windows / macOS / Linux | Python 3.7+
No external dependencies required — auto-installed on first run.

Usage:
  python yaspeed.py                        # full test
  python yaspeed.py --source-ip 1.2.3.4   # bind to specific IP
  python yaspeed.py --interface eth0       # bind to interface (Linux/macOS)
  python yaspeed.py --interface "Wi-Fi"    # bind to interface (Windows)
  python yaspeed.py -t 8 -d 15            # 8 threads, 15 sec
  python yaspeed.py --no-upload
  python yaspeed.py --json > result.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import socket
import statistics
import string
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

__version__ = "1.1.0"


# ─── auto-install dependencies ────────────────────────────────────────────────
def _ensure_deps() -> None:
    """Silently check and install missing dependencies.

    Skipped automatically when running as a frozen binary (PyInstaller),
    since all deps are bundled inside the executable.
    """
    # Running as a compiled binary — deps are already bundled, skip check
    if getattr(sys, "frozen", False):
        return

    missing: List[str] = []
    try:
        import requests  # noqa: F401
    except ImportError:
        missing.append("requests>=2.28")
    try:
        import rich  # noqa: F401
    except ImportError:
        missing.append("rich>=13.0")

    if not missing:
        return  # all good, no output

    print(f"[yaspeed] Installing: {', '.join(missing)} …", flush=True)

    base_cmd = [sys.executable, "-m", "pip", "install", "-q"] + missing

    def _run(extra: List[str] = []) -> bool:
        try:
            subprocess.check_call(
                base_cmd + extra,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    # Debian/Ubuntu system python3 may require --break-system-packages
    if platform.system() == "Linux":
        if _run(["--break-system-packages"]) or _run():
            return

    # macOS / Windows / generic Linux
    if _run() or _run(["--user"]):
        return

    print(
        "[yaspeed] Could not auto-install dependencies.\n"
        f"  Please run manually:  pip install {' '.join(missing)}",
        file=sys.stderr,
    )
    sys.exit(1)


_ensure_deps()

import requests  # noqa: E402
import requests.adapters  # noqa: E402
from rich import box  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

console = Console()

# ─── constants ────────────────────────────────────────────────────────────────
API_BASE        = "https://yandex.ru/internet/api/v0"
IP_ENDPOINT     = f"{API_BASE}/ip"
PROBES_ENDPOINT = f"{API_BASE}/get-probes"

HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer":       "https://yandex.ru/internet/",
    "Origin":        "https://yandex.ru",
    "Cache-Control": "no-cache, no-store",
    "Pragma":        "no-cache",
}

_SYSTEM = platform.system()   # "Windows" | "Darwin" | "Linux"


# ─── helpers ─────────────────────────────────────────────────────────────────
def _rid() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=16))


def _add_rid(url: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}rid={_rid()}&_={int(time.time() * 1000)}"


def _fmt_speed(mbps: float) -> str:
    return f"{mbps / 1000:.2f} Гбит/с" if mbps >= 1000 else f"{mbps:.2f} Мбит/с"


def _bar(fraction: float, width: int = 30) -> str:
    filled = int(min(fraction, 1.0) * width)
    return "█" * filled + "░" * (width - filled)


# ─── cross-platform interface → IP resolution ─────────────────────────────────
def resolve_interface_ip(interface: str) -> str:
    """Return the IPv4 address bound to *interface*.

    Resolution order:
      1. netifaces (optional package — most reliable, all platforms)
      2. Linux  — fcntl/ioctl
      3. macOS  — ifconfig
      4. Windows — ipconfig
    """
    # 1) netifaces — optional but preferred
    try:
        import netifaces  # type: ignore
        addrs = netifaces.ifaddresses(interface)
        ipv4  = addrs.get(netifaces.AF_INET, [])
        if ipv4:
            return ipv4[0]["addr"]
        raise RuntimeError(f"No IPv4 address on interface '{interface}'")
    except ImportError:
        pass

    # 2) Linux: fcntl + ioctl
    if _SYSTEM == "Linux":
        try:
            import fcntl, struct
            SIOCGIFADDR = 0x8915
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                res = fcntl.ioctl(
                    s.fileno(),
                    SIOCGIFADDR,
                    struct.pack("256s", interface[:15].encode()),
                )
                return socket.inet_ntoa(res[20:24])
            finally:
                s.close()
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

    # 3) macOS: ifconfig
    if _SYSTEM == "Darwin":
        try:
            out = subprocess.check_output(
                ["ifconfig", interface], text=True, stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                parts = line.split()
                if "inet" in parts:
                    return parts[parts.index("inet") + 1]
        except Exception:
            pass

    # 4) Windows: ipconfig — case-insensitive adapter name match
    if _SYSTEM == "Windows":
        try:
            out = subprocess.check_output(
                ["ipconfig"], text=True, stderr=subprocess.DEVNULL,
                encoding="cp866", errors="replace",
            )
            current_adapter: Optional[str] = None
            for line in out.splitlines():
                stripped = line.strip()
                if not line.startswith(" ") and stripped.endswith(":"):
                    current_adapter = stripped[:-1]
                elif current_adapter and interface.lower() in current_adapter.lower():
                    if "IPv4" in stripped or "IP-адрес" in stripped:
                        parts = stripped.split(":")
                        if len(parts) >= 2:
                            ip = parts[-1].strip()
                            if ip and not ip.startswith("169"):
                                return ip
        except Exception:
            pass

    raise RuntimeError(
        f"Cannot resolve IP for interface '{interface}' on {_SYSTEM}.\n"
        "  Tip: pip install netifaces  — gives cross-platform interface support\n"
        "  Or use:  --source-ip <IP>  to pass the IP directly."
    )


# ─── source-address HTTP adapter ─────────────────────────────────────────────
class SourceAddressAdapter(requests.adapters.HTTPAdapter):
    """HTTPAdapter that binds outgoing connections to a specific source IP."""

    def __init__(self, source_ip: str, **kwargs):
        self._source_address = (source_ip, 0)
        super().__init__(**kwargs)

    def send(self, request, **kwargs):
        import urllib3.util.connection as _conn

        original = _conn.create_connection

        def patched(address, *args, **kw):
            kw["source_address"] = self._source_address
            return original(address, *args, **kw)

        _conn.create_connection = patched
        try:
            return super().send(request, **kwargs)
        finally:
            _conn.create_connection = original


# ─── core tester ─────────────────────────────────────────────────────────────
class YaSpeed:
    def __init__(
        self,
        source_ip:  Optional[str] = None,
        interface:  Optional[str] = None,
        threads:    int = 4,
        duration:   int = 10,
        ping_count: int = 12,
    ) -> None:
        self.threads    = threads
        self.duration   = duration
        self.ping_count = ping_count

        self._bytes:   int  = 0
        self._lock          = threading.Lock()
        self._running       = False
        self._payload       = os.urandom(1024 * 1024)  # 1 MB random data for upload

        # Resolve interface name → source IP (if requested)
        if interface:
            try:
                source_ip = resolve_interface_ip(interface)
                console.print(
                    f"  [yellow]Interface [bold]{interface}[/bold] → {source_ip}[/yellow]"
                )
            except RuntimeError as exc:
                console.print(f"[yellow]⚠ {exc}[/yellow]")

        self._source_ip = source_ip
        self._session   = self._build_session(source_ip)

    # ── session ──────────────────────────────────────────────────────────────
    def _build_session(self, source_ip: Optional[str]) -> requests.Session:
        s = requests.Session()
        s.headers.update(HEADERS)
        if source_ip:
            adapter = SourceAddressAdapter(source_ip)
            s.mount("https://", adapter)
            s.mount("http://",  adapter)
        return s

    # ── meta requests ─────────────────────────────────────────────────────────
    def fetch_ip(self) -> str:
        try:
            r = self._session.get(IP_ENDPOINT, timeout=5)
            return r.text.strip().strip('"')
        except Exception:
            return "—"

    def fetch_config(self) -> dict:
        r = self._session.get(
            PROBES_ENDPOINT,
            params={"t": int(time.time() * 1000)},
            timeout=8,
        )
        r.raise_for_status()
        return r.json()

    # ── latency ───────────────────────────────────────────────────────────────
    def _ping_once(self, url: str) -> float:
        t0 = time.perf_counter()
        self._session.get(_add_rid(url), timeout=3)
        return (time.perf_counter() - t0) * 1000

    def measure_latency(
        self, probes: List[dict]
    ) -> Tuple[str, float, float, float, float]:
        """Pick the fastest probe, measure ping N times.

        Returns: (host, avg_ms, min_ms, max_ms, jitter_ms)
        """
        best_url: Optional[str] = None
        best_lat = float("inf")

        for probe in probes:
            url = probe.get("url", "")
            try:
                lat = self._ping_once(url)
                if lat < best_lat:
                    best_lat, best_url = lat, url
            except Exception:
                pass

        if not best_url:
            raise RuntimeError("Cannot reach any latency probe server")

        host    = urlparse(best_url).netloc
        samples: List[float] = []

        for _ in range(self.ping_count):
            try:
                samples.append(self._ping_once(best_url))
                time.sleep(0.05)
            except Exception:
                pass

        if not samples:
            raise RuntimeError("No latency samples collected")

        # Trim 10% outliers from each end for a more stable average
        samples.sort()
        trim    = max(1, len(samples) // 10)
        trimmed = samples[trim:-trim] if len(samples) > 2 * trim else samples

        avg    = statistics.mean(trimmed)
        jitter = statistics.stdev(trimmed) if len(trimmed) > 1 else 0.0
        return host, avg, min(trimmed), max(trimmed), jitter

    # ── transfer workers ──────────────────────────────────────────────────────
    def _dl_worker(self, url: str) -> None:
        """Download worker — streams data until _running is False."""
        while self._running:
            try:
                with self._session.get(_add_rid(url), stream=True, timeout=10) as r:
                    if r.status_code != 200:
                        time.sleep(0.2)
                        continue
                    for chunk in r.iter_content(65536):
                        if not self._running:
                            break
                        with self._lock:
                            self._bytes += len(chunk)
            except Exception:
                time.sleep(0.1)

    def _ul_worker(self, url: str, size_bytes: int = 52_428_800) -> None:
        """Upload worker — sends chunks until _running is False or size reached."""
        def _gen():
            sent = 0
            while sent < size_bytes and self._running:
                chunk = min(len(self._payload), size_bytes - sent)
                yield self._payload[:chunk]
                with self._lock:
                    self._bytes += chunk
                sent += chunk

        while self._running:
            try:
                self._session.post(_add_rid(url), data=_gen(), timeout=20)
            except Exception:
                time.sleep(0.1)

    # ── progress bar runner ───────────────────────────────────────────────────
    def _run_test(self, icon: str, color: str, worker, *worker_args) -> float:
        """Spawn N workers, show a live progress bar, return average Mbps."""
        self._bytes   = 0
        self._running = True

        pool = ThreadPoolExecutor(max_workers=self.threads)
        for _ in range(self.threads):
            pool.submit(worker, *worker_args)

        start = time.monotonic()
        try:
            while True:
                elapsed = time.monotonic() - start
                if elapsed >= self.duration:
                    break

                with self._lock:
                    current = self._bytes

                avg_mbps = current * 8 / 1_000_000 / elapsed if elapsed > 0 else 0
                bar      = _bar(elapsed / self.duration)

                print(
                    f"\r  {icon} {bar}  {avg_mbps:>8.2f} Мбит/с"
                    f"  {elapsed:.0f}/{self.duration}с   ",
                    end="",
                    flush=True,
                )
                time.sleep(0.2)

        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            pool.shutdown(wait=False)

        elapsed = time.monotonic() - start
        print()  # newline after progress bar

        return self._bytes * 8 / 1_000_000 / elapsed if elapsed > 0 else 0.0

    # ── probe selection ───────────────────────────────────────────────────────
    @staticmethod
    def _pick_probe(
        config:  dict,
        section: str,
        host:    str,
        hint:    str = "",
    ) -> Tuple[Optional[str], int]:
        """Return the best (url, size) probe for a given test section."""
        probes     = config.get(section, {}).get("probes", [])
        candidates = [p for p in probes if host in p.get("url", "")] or probes
        if hint:
            for p in candidates:
                if hint in p.get("url", ""):
                    return p["url"], int(p.get("size", 0))
        if candidates:
            return candidates[0]["url"], int(candidates[0].get("size", 0))
        return None, 0

    # ── main run ──────────────────────────────────────────────────────────────
    def run(
        self,
        skip_upload:   bool = False,
        skip_download: bool = False,
        output_json:   bool = False,
    ) -> dict:
        result: dict = {}

        if not output_json:
            _print_banner(self._source_ip)

        # Resolve public IP
        with console.status("[cyan]Определяю IP…[/cyan]"):
            ip = self.fetch_ip()
        if not output_json:
            console.print(f"  [cyan]IP:[/cyan] [bold]{ip}[/bold]")
        result["ip"] = ip

        # Fetch probe config from Yandex
        with console.status("[cyan]Получаю серверы…[/cyan]"):
            try:
                config = self.fetch_config()
            except Exception as exc:
                console.print(f"[red]Config error: {exc}[/red]")
                return {}

        # ── latency ─────────────────────────────────────────────────────────
        lat_probes = config.get("latency", {}).get("probes", [])
        if not output_json:
            console.print("\n[bold yellow]● Измерение задержки…[/bold yellow]")

        try:
            host, ping, mn, mx, jitter = self.measure_latency(lat_probes)
        except RuntimeError as exc:
            console.print(f"[red]{exc}[/red]")
            return {}

        if not output_json:
            console.print(
                f"  [green]✓[/green] Пинг: [bold]{ping:.1f} мс[/bold]"
                f"  (min {mn:.1f} / max {mx:.1f} / jitter {jitter:.1f} мс)"
                f"  [dim]{host}[/dim]"
            )
        result.update(
            server=host,
            ping_ms=round(ping, 2),
            ping_min_ms=round(mn, 2),
            ping_max_ms=round(mx, 2),
            jitter_ms=round(jitter, 2),
        )

        # ── download ─────────────────────────────────────────────────────────
        if not skip_download:
            dl_url, _ = self._pick_probe(config, "download", host)
            if dl_url:
                if not output_json:
                    console.print("\n[bold cyan]↓ Download…[/bold cyan]")
                mbps = self._run_test("↓", "cyan", self._dl_worker, dl_url)
                if not output_json:
                    console.print(
                        f"  [green]✓[/green] Download: [bold cyan]{_fmt_speed(mbps)}[/bold cyan]"
                    )
                result["download_mbps"] = round(mbps, 2)
            else:
                console.print("[yellow]⚠ No download probe URL found[/yellow]")

        # ── upload ───────────────────────────────────────────────────────────
        if not skip_upload:
            ul_url, ul_size = self._pick_probe(config, "upload", host, "52428800")
            if not ul_url:
                ul_url, ul_size = self._pick_probe(config, "upload", host)
            if ul_url:
                if not output_json:
                    console.print("\n[bold magenta]↑ Upload…[/bold magenta]")
                mbps = self._run_test(
                    "↑", "magenta",
                    self._ul_worker, ul_url, ul_size or 52_428_800,
                )
                if not output_json:
                    console.print(
                        f"  [green]✓[/green] Upload: [bold magenta]{_fmt_speed(mbps)}[/bold magenta]"
                    )
                result["upload_mbps"] = round(mbps, 2)
            else:
                console.print("[yellow]⚠ No upload probe URL found[/yellow]")

        if not output_json:
            _print_results(result)

        return result


# ─── UI helpers ───────────────────────────────────────────────────────────────
def _print_banner(source_ip: Optional[str]) -> None:
    console.print("[bold white]  ╔══════════════════════════════════════╗[/bold white]")
    console.print("[bold white]  ║[/bold white]  [bold cyan]⚡ Yandex Internetometer CLI ⚡[/bold cyan]  [bold white]║[/bold white]")
    console.print("[bold white]  ║[/bold white]     [dim]yandex.ru/internet backend[/dim]      [bold white]║[/bold white]")
    console.print("[bold white]  ╚══════════════════════════════════════╝[/bold white]")
    if source_ip:
        console.print(f"\n  [yellow]Source IP: {source_ip}[/yellow]")
    console.print()


def _quality(mbps: float) -> str:
    if mbps >= 500: return "🚀 Огонь"
    if mbps >= 100: return "✅ Отлично"
    if mbps >= 50:  return "👍 Хорошо"
    if mbps >= 10:  return "🆗 Норм"
    return "🐌 Медленно"


def _print_results(result: dict) -> None:
    console.print()
    console.rule("[bold white]Результаты[/bold white]")

    t = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    t.add_column("k", style="dim",        justify="right")
    t.add_column("v", style="bold white",  justify="left")
    t.add_column("i", style="dim",         justify="left")

    if "ip"     in result: t.add_row("🌐 IP",     result["ip"],     "")
    if "server" in result: t.add_row("🖥  Сервер", result["server"], "")

    if "ping_ms" in result:
        t.add_row(
            "⏱  Пинг",
            f"[yellow]{result['ping_ms']:.1f} мс[/yellow]",
            f"min {result['ping_min_ms']:.1f} / max {result['ping_max_ms']:.1f}",
        )
    if "jitter_ms" in result:
        j = result["jitter_ms"]
        c     = "green" if j < 5 else "yellow" if j < 20 else "red"
        label = "✅ Stable" if j < 2 else "👍 Good" if j < 5 else "⚠ Unstable" if j < 20 else "🔴 Bad"
        t.add_row("〰  Джиттер", f"[{c}]{j:.1f} мс[/{c}]", label)

    if "download_mbps" in result:
        mbps = result["download_mbps"]
        t.add_row("↓  Download", f"[bold cyan]{_fmt_speed(mbps)}[/bold cyan]", _quality(mbps))
    if "upload_mbps" in result:
        mbps = result["upload_mbps"]
        t.add_row("↑  Upload",   f"[bold magenta]{_fmt_speed(mbps)}[/bold magenta]", _quality(mbps))

    console.print(t)
    console.rule()


# ─── CLI ──────────────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="yaspeed",
        description="CLI speed test via yandex.ru/internet  |  Win / macOS / Linux",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python yaspeed.py
  python yaspeed.py --source-ip 192.168.1.5
  python yaspeed.py --interface eth0
  python yaspeed.py --interface "Wi-Fi"        # Windows
  python yaspeed.py -t 8 -d 15                # 8 threads, 15 sec
  python yaspeed.py --no-upload
  python yaspeed.py --json > result.json
""",
    )
    p.add_argument(
        "--version", "-V",
        action="version",
        version=f"yaspeed {__version__}",
    )

    net = p.add_argument_group("Network / interface")
    net.add_argument(
        "--source-ip", metavar="IP",
        help="Bind outgoing traffic to a specific source IP",
    )
    net.add_argument(
        "--interface", "-i", metavar="IFACE",
        help="Use a specific network interface  (eth0 / en0 / Wi-Fi / Ethernet …)",
    )

    perf = p.add_argument_group("Test parameters")
    perf.add_argument(
        "--threads",    "-t", type=int, default=4,  metavar="N",
        help="Parallel streams (default: 4)",
    )
    perf.add_argument(
        "--duration",   "-d", type=int, default=10, metavar="SEC",
        help="Duration of each test in seconds (default: 10)",
    )
    perf.add_argument(
        "--ping-count",       type=int, default=12, metavar="N",
        help="Number of ping samples (default: 12)",
    )

    out = p.add_argument_group("Output")
    out.add_argument("--no-download", action="store_true", help="Skip download test")
    out.add_argument("--no-upload",   action="store_true", help="Skip upload test")
    out.add_argument(
        "--json", action="store_true",
        help="Output result as JSON (for scripting / monitoring)",
    )
    return p


def main() -> None:
    ns = _build_parser().parse_args()
    tester = YaSpeed(
        source_ip  = ns.source_ip,
        interface  = ns.interface,
        threads    = ns.threads,
        duration   = ns.duration,
        ping_count = ns.ping_count,
    )
    try:
        result = tester.run(
            skip_upload   = ns.no_upload,
            skip_download = ns.no_download,
            output_json   = ns.json,
        )
    except KeyboardInterrupt:
        print("\n\n[Interrupted]")
        sys.exit(0)

    if ns.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
