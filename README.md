# ⚡ yaspeed

CLI speed test using **yandex.ru/internet** backend — an alternative to the blocked Speedtest CLI.

Works on **Windows / macOS / Linux** | Python **3.7+**

```
  ╔══════════════════════════════════════╗
  ║  ⚡ Yandex Internetometer CLI ⚡  ║
  ║     yandex.ru/internet backend      ║
  ╚══════════════════════════════════════╝

  IP: 1.2.3.4

● Измерение задержки…
  ✓ Пинг: 12.3 мс  (min 11.1 / max 14.7 / jitter 1.2 мс)  internetometer.s3.yandex.net

↓ Download…
  ↓ ██████████████████████████████  523.18 Мбит/с  10/10с

↑ Upload…
  ↑ ████████████████████████████░░  231.44 Мбит/с  10/10с

──────────────── Результаты ─────────────────
 ╭──────────────────────────────────────────╮
 │  🌐 IP          1.2.3.4                  │
 │  🖥  Сервер     internetometer.s3...      │
 │  ⏱  Пинг       12.3 мс  min 11.1/max 14.7│
 │  〰  Джиттер    1.2 мс   ✅ Stable        │
 │  ↓  Download   523.18 Мбит/с  🚀 Огонь   │
 │  ↑  Upload     231.44 Мбит/с  ✅ Отлично  │
 ╰──────────────────────────────────────────╯
```

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/yaspeed.git
cd yaspeed
pip install -r requirements.txt   # or just run — deps auto-install
```

No install needed — single file, runs anywhere.

## Usage

```bash
# Basic test
python yaspeed.py

# Bind to a specific source IP (multi-homing)
python yaspeed.py --source-ip 192.168.1.5

# Bind to a network interface
python yaspeed.py --interface eth0        # Linux
python yaspeed.py --interface en0         # macOS
python yaspeed.py --interface "Wi-Fi"     # Windows
python yaspeed.py --interface "Ethernet"  # Windows

# More threads and longer duration (for fast links)
python yaspeed.py --threads 8 --duration 20

# Download only / upload only
python yaspeed.py --no-upload
python yaspeed.py --no-download

# JSON output (for scripts / monitoring)
python yaspeed.py --json
python yaspeed.py --json > result.json

# Test a VPN tunnel
python yaspeed.py --interface tun0 --threads 6
```

## Options

| Option | Short | Default | Description |
|---|---|---|---|
| `--source-ip IP` | | — | Bind to specific source IP |
| `--interface IFACE` | `-i` | — | Bind to network interface |
| `--threads N` | `-t` | 4 | Parallel streams |
| `--duration SEC` | `-d` | 10 | Duration of each test (seconds) |
| `--ping-count N` | | 12 | Number of latency samples |
| `--no-download` | | — | Skip download test |
| `--no-upload` | | — | Skip upload test |
| `--json` | | — | Machine-readable JSON output |

## JSON output example

```json
{
  "ip": "1.2.3.4",
  "server": "internetometer.s3.yandex.net",
  "ping_ms": 12.3,
  "ping_min_ms": 11.1,
  "ping_max_ms": 14.7,
  "jitter_ms": 1.2,
  "download_mbps": 523.18,
  "upload_mbps": 231.44
}
```

## Interface binding notes

### Linux / macOS
Works out of the box via `fcntl`/`ifconfig`. Install `netifaces` for a more reliable experience:
```bash
pip install netifaces
```

### Windows
Uses `ipconfig` output to resolve interface IP. Pass the adapter name as shown in `ipconfig`:
```
python yaspeed.py --interface "Wi-Fi"
python yaspeed.py --interface "Ethernet 2"
```
Or install `netifaces`:
```bash
pip install netifaces
python yaspeed.py --interface "Wi-Fi"
```

## Requirements

- Python 3.7+
- `requests` ≥ 2.28
- `rich` ≥ 13.0
- *(optional)* `netifaces` ≥ 0.11 — cross-platform interface resolution

Dependencies are auto-installed on first run if missing.

## Why yaspeed?

Speedtest CLI (`speedtest-cli` / Ookla's `speedtest`) is blocked in Russia.  
Yandex Internetometer (`yandex.ru/internet`) is available and uses a solid infrastructure of test servers.  
**yaspeed** wraps the same API with a proper CLI experience: progress bars, parallel streams, latency/jitter, JSON output, and interface binding.

## License

MIT
