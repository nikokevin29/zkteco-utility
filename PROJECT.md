# ZKTeco Utility — Project Summary

Desktop app (Python/**PySide6** sejak v5.0.0, sebelumnya tkinter; single window) untuk mesin absen **ZKTeco eFace10** di CV RAJ.
Tarik log absensi via LAN → simpan SQLite → generate laporan Excel berformat → preview in-app.

**Konteks hardware penting:** eFace10 TIDAK punya baterai RTC. Mati listrik = jam mesin reset ke tahun 2000, absensi selama outage tercatat tanggal palsu. Sebagian besar kompleksitas app ini ada untuk menangani itu (anomaly recovery + auto clock sync).

## Files

| File | Isi |
|------|-----|
| `zkteco_app.py` | SEMUA logika app (~1800 baris, satu file, sengaja): config, SQLite, anomaly recovery, Excel generator, seluruh UI (PySide6) |
| `updater.py` | Auto-update dari GitHub Releases (`nikokevin29/zkteco-utility`). Download → rename old `_old.exe` → replace → restart |
| `recover_and_export.py` | Script standalone one-off: recovery anomali + export ke Desktop tanpa buka app. Duplikasi logika in-app (import dari zkteco_app), kandidat hapus kalau tak dipakai lagi |
| `test_zkteco_app.py` | 55 test unittest/pytest: anomaly detection/remap, gap-finder, DB CRUD, config, excel bytes, updater version compare. Run: `py -m pytest test_zkteco_app.py -q` |
| `build_windows.bat` / `build_linux.sh` | PyInstaller onefile. Output `dist\ZKTeco_Utility.exe` |
| `config.json` (runtime) | IP/port device, jam masuk/keluar, toleransi, user_map UID→nama, lang (en/id, hanya untuk label Excel) |
| `absensi.db` (runtime) | SQLite: `attendance` (timestamp UNIQUE), `users`, `pull_sessions`, `excel_snapshots` (xlsx blob di DB) |

## Struktur zkteco_app.py

- **Anomaly recovery** (top of file): `is_anomaly_ts`, `find_gap_start` (cari gap kalender terpanjang = outage), `remap_anomalies` (map hari-2000 → tanggal riil, jam di-rescale ke jendela kerja)
- **DB helpers**: fungsi `db_*` polos, satu koneksi per call
- **`generate_excel_bytes(rows, cfg)`**: laporan 3 jenis sheet (Kartu Absensi per bulan, Rekap, Log Detail) → bytes. Ada mini-dataframe class `_DF` internal (pengganti pandas, sengaja biar exe kecil) — kontainer, jangan diganti tanpa alasan
- **Dialogs**: `SettingsDialog`, `DeviceInfoDialog`, `UserManagerDialog` (add/rename/delete user di mesin)
- **`App(tk.Tk)`**: split panel. Kiri = koneksi + workflow 3 langkah (Pull → Filter → Preview) + log. Kanan = notebook (Report Viewer treeview, Pull History + saved reports)
- Semua aksi device jalan di thread via `self._run(fn)`; update UI selalu via `self.after(0, ...)`
- Device I/O via **pyzk** (`from zk import ZK`), port default 8088

## Perilaku kunci

- **RTC auto-sync**: `_check_clock` dipanggil saat Test Connection & Pull — skew >2 menit → `conn.set_time()` otomatis. Tidak ada tombol Set Time manual lagi (dihapus, by design)
- **Live Monitor** (`_toggle_live`/`_live_loop`, v4.6.0): koneksi sendiri di luar `_run` (tombol lain tetap aktif), `conn.live_capture()` loop dengan auto-reconnect 30s. Tiap punch: log + toast `_toast` (Toplevel pojok kanan bawah, 4s, tanpa dependency) + insert DB langsung (dedup via timestamp UNIQUE). Stop via flag `_live_want` + `end_live_capture`
- **Capacity warning** (`_check_capacity`, v4.6.0): `conn.read_sizes()` saat Test/Pull — log ≥80% penuh → popup suruh pull + clear. Device Info tampilkan `users/cap` & `records/cap`
- **Autostart** (`_apply_autostart`, v4.6.0): toggle di Settings → registry `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` via winreg, exe dijalankan dengan flag `--minimized` (window iconify). Hanya jalan dari exe (bukan .py). Toggle kedua `live_autostart`: live monitor nyala otomatis 1.5s setelah launch
- **UI PySide6** (v5.0.0): seluruh layer view di bawah divider `UI — PySide6` di zkteco_app.py — QSS stylesheet global (`QSS`), `_Bridge`/`self.ui(fn)` = satu-satunya cara update UI dari thread (pengganti `self.after(0,...)`), tabel via helper `_mk_table`/`_fill_table`, toast QLabel fade-in. Logika di atas divider TIDAK berubah dari era tkinter
- **System tray** (QSystemTrayIcon, tanpa pystray): klik X = sembunyi ke tray (live monitor & clock guard tetap jalan), menu tray Open Dashboard/Exit, `--minimized` langsung ke tray; `setQuitOnLastWindowClosed(False)`
- **Clock guard berkala** (v4.8.0): `_clock_tick` tiap 10 mnt (saat live monitor off) + `_check_clock(quiet=True)` di tiap (re)connect live monitor — jam mesin yang reset karena mati listrik otomatis dibetulkan ≤10 mnt tanpa buka app
- **Installer** (v4.8.0): `installer.iss` (Inno Setup) → `ISCC installer.iss` → `dist\ZKTeco_Utility_Setup.exe`, install per-user ke `%LOCALAPPDATA%\ZKTeco Utility` tanpa admin; uninstall TIDAK menghapus config/db. Path data frozen exe fix: `_BASE` = sebelah exe (bukan `_MEIPASS`)
- **Pull**: deteksi record tahun-2000 → auto-remap (anchor dari config atau auto gap-finder) → warning popup + backup CSV audit → insert DB (dedup via timestamp UNIQUE)
- **Cloud sync VST** (v5.2.0): Settings → Cloud Sync; POST ke `service.rejekiamerta.com/api/attendance/sync` (Bearer token per company); auto setelah Pull + tombol manual
- **Today dashboard + Daily tab** (v4.7.0): tab `🏠 Today` (default) = stat tiles Hadir/Telat/Belum Absen + tree punch hari ini, auto-refresh dari live monitor; tab `📅 Daily` = view harian langsung dari DB via `compute_daily_rows(rows, cfg)` (module-level, sengaja duplikat matematika telat dari `generate_excel_bytes` — jangan refactor generatornya). `_setup_style` = ttk theme clam + palet konsisten
- Report disimpan sebagai snapshot xlsx blob di DB, bisa di-load/export ulang dari tab History
- `⚡ All at Once` = pull + report sekaligus

## Build

```
cd D:\Developer\zkteco-utility
py -m pytest test_zkteco_app.py -q   # test dulu
build_windows.bat                     # atau pyinstaller command di dalamnya
```
Catatan: exe di `dist\` kekunci kalau app lagi jalan — kill `ZKTeco_Utility` dulu sebelum rebuild.

## Riwayat kurasi (Jul 2026)

- v4.6.0: Live Monitor + toast notif, capacity warning, autostart Windows, tombol koneksi ditata grid 3×2 uniform (Live sempat ketutup panel 420px)
- Ditambah: user add/rename/delete di device, tombol Restart device, RTC auto-sync
- Dihapus: `i18n.py` (dead code, `T()` tak pernah dipanggil), blok cleanup duplikat di `__init__`, step Set Time, popup restart bahasa yang menyesatkan, tab Staff Names di Settings (redundan — nama dikelola via Manage Users, device = source of truth; sync di-guard agar nama kosong dari device tidak menimpa `user_map`)
- Sengaja dibiarkan: `_DF` class (kecil, teruji, hindari pandas), `recover_and_export.py` (tool darurat offline)
