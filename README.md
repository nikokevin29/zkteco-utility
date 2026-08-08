# ZKTeco eFace10 Utility

<p align="center">
  <img src="app_icon.png" width="120" alt="ZKTeco x CV RAJ Logo"/>
</p>

<p align="center">
  <strong>Desktop utility for ZKTeco eFace10 face recognition attendance device.</strong><br/>
  Direct TCP connection — no ADMS required.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue"/>
  <img src="https://img.shields.io/badge/python-3.9%2B-green"/>
  <img src="https://img.shields.io/badge/license-MIT-brightgreen"/>
  <img src="https://img.shields.io/github/v/release/xbanana29/zkteco-utility"/>
</p>

---

## Features

| Feature | Description |
|---------|-------------|
| Direct TCP connection | Connects to device via TCP — no ADMS/cloud needed |
| Auto clock sync | RTC auto-synced to PC (eFace10 has no RTC battery) |
| Pull attendance | Fetch logs, dedupe, SQLite; anomaly recovery for year-2000 timestamps |
| Excel report | Attendance card, recap, log detail (+ flags: no-checkout, recovered) |
| Payroll CSV | Export UID/Nama/tanggal/masuk/keluar/telat/status for payroll systems |
| Izin / Cuti / Dinas | Manual leave calendar per employee (affects daily view & payroll) |
| Forced in/out mode | Optional: use device punch codes (0/4=in, 1/5=out) instead of first/last |
| Live monitor | Real-time punches + toast while app runs (incl. tray) |
| User management | View/add/rename/delete users on device |
| Device info | Firmware, serial, memory capacity warning |
| Clear device log | Free device memory after pull |
| Auto backup CSV | Optional raw backup on every pull |
| Cloud sync (VST) | Push employees + punches + leave ke VST-laravel (`/api/attendance/sync`) |
| Silent mode | Tray only; Windows login → auto pull + cloud sync (interval optional) |
| Auto update | GitHub Releases |
| Report language | English / Bahasa Indonesia (Excel headers) |
| Cross-platform | Windows, Linux, macOS |

### In/Out logic
**Default (First/Last):** all face taps that day collapse to earliest = check-in, latest = check-out.  
**Forced device punch:** uses machine status (0/4 = in, 1/5 = out).  
Single-tap days are flagged **NO_CHECKOUT** (no checkout).

---

## Download

### [Latest Release](https://github.com/xbanana29/zkteco-utility/releases/latest)

| Platform | File |
|----------|------|
| Windows  | `ZKTeco_Utility.exe` (~13 MB) |
| Linux    | `ZKTeco_Utility_Linux` |
| macOS    | `ZKTeco_Utility_macOS` |

---

## Quick Start

**Windows:** Download EXE, place in a dedicated folder (not Downloads), run.

**Linux / macOS:**
```bash
chmod +x ZKTeco_Utility_Linux
./ZKTeco_Utility_Linux
```

**From source:**
```bash
git clone https://github.com/xbanana29/zkteco-utility.git
cd zkteco-utility
pip install pyzk openpyxl PySide6
python zkteco_app.py
```

### Build release binaries (GitHub Actions)

Push tag `vX.Y.Z` (e.g. `v5.0.2`) → CI runs tests, builds Windows / Linux / macOS, and publishes a GitHub Release.

```bash
git tag v5.0.2
git push origin v5.0.2
```

Manual re-build (no release): Actions → **Test, Build & Release** → **Run workflow**.

---

## Configuration

`config.json` is auto-created on first run:

```json
{
  "ip": "10.10.11.55",
  "port": "8088",
  "lang": "en",
  "jam_masuk": "08:00",
  "jam_keluar": "16:00",
  "toleransi": 15,
  "auto_backup": true,
  "user_map": { "1": "NICHOLAS" }
}
```

Language can be switched (English / Bahasa Indonesia) from the header dropdown without restarting.

### Cloud Sync → VST Absensi

1. Di web **https://service.rejekiamerta.com** login admin → sidebar **Absensi → Absensi Karyawan**
2. Klik **Generate Token**, salin **API Base URL** + **API Token**
3. Di ZKTeco Utility → **Settings → Cloud Sync (VST Absensi)**:
   - centang **Enable sync**
   - paste URL (`https://service.rejekiamerta.com/api/attendance`)
   - paste token
   - (opsional) **Auto-sync after every Pull**
4. **Pull** dari mesin, atau tombol **☁ Sync to VST Cloud** (full DB)

Endpoint:
- `POST /api/attendance/sync` — body JSON `{ employees, punches, leaves }`
- `GET /api/attendance/employees` — daftar karyawan aktif company
- Auth: `Authorization: Bearer <token>` (per-company)

---

## Build from Source

```bash
# Windows
build_windows.bat

# Linux / macOS
chmod +x build_linux.sh && ./build_linux.sh
```

## Release a New Version

```bash
git commit -am "release: v4.2.0"
git tag v4.2.0
git push origin main --tags
# GitHub Actions auto-builds for all platforms
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `pyzk` | ZKTeco device protocol |
| `openpyxl` | Excel file generation |
| `tkinter` | GUI (bundled with Python) |
| `sqlite3` | Local database (bundled with Python) |

No pandas, no numpy — binary stays small (~13 MB on Windows).

---

## License

MIT — free to use, modify, and distribute. See [LICENSE](LICENSE).

---

*Built for CV Rejeki Amerta Jaya, Wangon, Banyumas.*
