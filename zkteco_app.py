#!/usr/bin/env python3
"""
ZKTeco eFace10 Utility v4.3 — CV RAJ
Split panel: kiri workflow, kanan viewer + history
Semua data disimpan di SQLite, tidak ada file temp eksternal
"""

import csv, os, threading, calendar, sqlite3, json, sys, time
from datetime import datetime, date, timedelta

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QLabel, QPushButton, QLineEdit,
    QComboBox, QCheckBox, QRadioButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFormLayout, QGroupBox, QTabWidget, QSplitter, QTableWidget, QTableWidgetItem,
    QHeaderView, QPlainTextEdit, QFrame, QMessageBox, QFileDialog, QProgressBar,
    QSystemTrayIcon, QMenu, QAbstractItemView, QSizePolicy, QGraphicsOpacityEffect)
from PySide6.QtCore import Qt, QObject, Signal, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QIcon, QAction, QColor, QFont

APP_VERSION = "5.0.0"
# Frozen exe: data lives next to the exe, NOT next to __file__ (which points
# into the throwaway _MEIxxxx extraction dir on onefile builds).
_BASE = (os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
         else os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(_BASE, "config.json")
DB_FILE     = os.path.join(_BASE, "absensi.db")

BULAN_ID = ["Januari","Februari","Maret","April","Mei","Juni",
            "Juli","Agustus","September","Oktober","November","Desember"]
HARI_ID  = ["Senin","Selasa","Rabu","Kamis","Jumat","Sabtu","Minggu"]

DEFAULT_CONFIG = {
    "ip": "10.10.11.55", "port": "8088",
    "lang": "en", "theme": "light",
    "jam_masuk": "08:00", "jam_keluar": "16:00",
    "toleransi": 15, "auto_backup": False,
    "autostart": False, "live_autostart": False,
    "anomaly_recover": True, "anomaly_anchor": "",
    "user_map": {
        "1":"NICHOLAS","2":"SERLI","3":"TIA","4":"MISRO",
        "5":"LISA","6":"TUR","7":"SLAMET","8":"ARI",
        "9":"REFA","10":"SUKUR","11":"PUGUH"
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# ANOMALY (clock-reset) DETECTION & RECOVERY
# The eFace10 has NO RTC battery (UPS-only power backup). On a power loss the
# device clock resets to year 2000, so punches made during the outage get
# stamped with bogus year-2000 dates instead of the real date. These helpers
# detect those records and remap them back onto real calendar dates.
# ─────────────────────────────────────────────────────────────────────────────
ANOMALY_YEAR = 2000   # timestamp.year <= this  → clock-reset anomaly record

def is_anomaly_ts(ts):
    return ts is None or ts.year <= ANOMALY_YEAR

def _sec_of_day(ts):
    return ts.hour*3600 + ts.minute*60 + ts.second

def find_gap_start(normal_recs, fallback=None):
    """Start date of the LONGEST run of missing days (the outage). A power outage
    long enough to matter shows up as the biggest gap in the calendar, so the
    anomaly records are remapped starting there."""
    dates = sorted({r['timestamp'].date() for r in normal_recs if r.get('timestamp')})
    if not dates:
        return fallback or date(2026, 6, 11)
    dset = set(dates); d = dates[0]
    best_start = None; best_len = 0
    while d <= dates[-1]:
        if d not in dset and (d - timedelta(days=1)) in dset:
            s = d; n = 0
            while d not in dset:
                d += timedelta(days=1); n += 1
            if n > best_len:
                best_len = n; best_start = s
        else:
            d += timedelta(days=1)
    return best_start or (dates[-1] + timedelta(days=1))

def remap_anomalies(anomaly_recs, anchor_date, jam_masuk='08:00', jam_keluar='16:00'):
    """Remap year-2000 records onto consecutive real dates starting at anchor_date
    (one fake day → one real day). Times within each day are linearly rescaled into
    a plausible work window [check-in − 30 min .. check-out + 60 min] so recovered
    rows look like normal days. Who-was-present and the punch order are REAL; the
    exact minute is approximate because the original clock was corrupted.
    Returns new dicts with corrected 'timestamp' plus 'recovered'=True and 'orig_ts'."""
    try:
        hi, mi = (int(x) for x in jam_masuk.split(':'))
        ho, mo = (int(x) for x in jam_keluar.split(':'))
    except Exception:
        hi, mi, ho, mo = 8, 0, 16, 0
    win_s = hi*3600 + mi*60 - 30*60
    win_e = ho*3600 + mo*60 + 60*60
    from collections import defaultdict
    byfake = defaultdict(list)
    for r in anomaly_recs:
        if r.get('timestamp'):
            byfake[r['timestamp'].date()].append(r)
    out = []; used = set()
    for i, fday in enumerate(sorted(byfake)):
        real = anchor_date + timedelta(days=i)
        recs = byfake[fday]
        secs = [_sec_of_day(r['timestamp']) for r in recs]
        t0, t1 = min(secs), max(secs); span = (t1 - t0) or 1
        base = datetime(real.year, real.month, real.day)
        for r in sorted(recs, key=lambda x: x['timestamp']):
            frac = (_sec_of_day(r['timestamp']) - t0) / span
            nts = base + timedelta(seconds=int(win_s + frac*(win_e - win_s)))
            while nts in used:               # keep timestamps unique (DB constraint)
                nts += timedelta(seconds=1)
            used.add(nts)
            nr = dict(r); nr['timestamp'] = nts
            nr['recovered'] = True; nr['orig_ts'] = r['timestamp']
            out.append(nr)
    return out

# ── Cross-platform opener ─────────────────────────────────────────────────────
import subprocess as _sp
def _open_path(path):
    try:
        if sys.platform == 'win32': os.startfile(path)
        elif sys.platform == 'darwin': _sp.Popen(['open', path])
        else: _sp.Popen(['xdg-open', path])
    except Exception: pass

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding='utf-8') as f: cfg = json.load(f)
            for k,v in DEFAULT_CONFIG.items():
                if k not in cfg: cfg[k] = v
            return cfg
        except: pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid INTEGER, nama TEXT,
        timestamp TEXT UNIQUE,
        punch INTEGER, pulled_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        uid INTEGER PRIMARY KEY, nama TEXT,
        card_id TEXT, updated_at TEXT
    )''')
    # Pull sessions — history tarikan dari mesin
    c.execute('''CREATE TABLE IF NOT EXISTS pull_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pulled_at TEXT,
        record_count INTEGER,
        new_count INTEGER,
        device_ip TEXT,
        note TEXT
    )''')
    # Excel snapshots — disimpan sebagai blob di DB
    c.execute('''CREATE TABLE IF NOT EXISTS excel_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER,
        created_at TEXT,
        label TEXT,
        filter_year INTEGER,
        filter_month INTEGER,
        data BLOB
    )''')
    conn.commit(); conn.close()

def db_insert_attendance(rows):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ins = 0
    for r in rows:
        ts = r['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(r['timestamp'],'strftime') else str(r['timestamp'])
        try:
            c.execute('INSERT INTO attendance (uid,nama,timestamp,punch,pulled_at) VALUES (?,?,?,?,?)',
                      (r['uid'], r['nama'], ts, r['punch'], now))
            ins += 1
        except sqlite3.IntegrityError: pass
    conn.commit(); conn.close()
    return ins

def db_query_attendance(year=None, month=None):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    q = "SELECT uid,nama,timestamp,punch FROM attendance WHERE strftime('%Y',timestamp)>'2000'"
    args = []
    if year:  q += " AND strftime('%Y',timestamp)=?";  args.append(str(year))
    if month: q += " AND strftime('%m',timestamp)=?";  args.append(f"{month:02d}")
    q += " ORDER BY timestamp"
    c.execute(q, args)
    rows = [{'uid':r[0],'nama':r[1],
             'timestamp':datetime.strptime(r[2],'%Y-%m-%d %H:%M:%S'),'punch':r[3]}
            for r in c.fetchall()]
    conn.close(); return rows

def db_count():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM attendance WHERE strftime('%Y',timestamp)>'2000'")
    n = c.fetchone()[0]; conn.close(); return n

def db_upsert_users(users):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for u in users:
        c.execute('INSERT OR REPLACE INTO users (uid,nama,card_id,updated_at) VALUES (?,?,?,?)',
                  (u['uid'], u['nama'], u.get('card_id',''), now))
    conn.commit(); conn.close()

def db_add_pull_session(record_count, new_count, device_ip, note=''):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute('INSERT INTO pull_sessions (pulled_at,record_count,new_count,device_ip,note) VALUES (?,?,?,?,?)',
              (now, record_count, new_count, device_ip, note))
    sid = c.lastrowid
    conn.commit(); conn.close()
    return sid

def db_get_pull_sessions():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT id,pulled_at,record_count,new_count,device_ip FROM pull_sessions ORDER BY id DESC')
    rows = c.fetchall(); conn.close()
    return rows

def db_delete_pull_session(sid):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('DELETE FROM pull_sessions WHERE id=?', (sid,))
    c.execute('DELETE FROM excel_snapshots WHERE session_id=?', (sid,))
    conn.commit(); conn.close()

def db_save_excel_snapshot(session_id, label, year, month, data_bytes):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute('INSERT INTO excel_snapshots (session_id,created_at,label,filter_year,filter_month,data) VALUES (?,?,?,?,?,?)',
              (session_id, now, label, year, month or 0, data_bytes))
    sid = c.lastrowid
    conn.commit(); conn.close()
    return sid

def db_get_excel_snapshots():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''SELECT e.id, e.label, e.created_at, e.filter_year, e.filter_month,
                        p.pulled_at, p.record_count
                 FROM excel_snapshots e
                 LEFT JOIN pull_sessions p ON e.session_id=p.id
                 ORDER BY e.id DESC''')
    rows = c.fetchall(); conn.close()
    return rows

def db_load_excel_snapshot(snap_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT data,label FROM excel_snapshots WHERE id=?', (snap_id,))
    row = c.fetchone(); conn.close()
    return row  # (bytes, label)

def db_delete_excel_snapshot(snap_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('DELETE FROM excel_snapshots WHERE id=?', (snap_id,))
    conn.commit(); conn.close()

def compute_daily_rows(rows, cfg):
    """Per-(nama, tanggal) daily summary straight from attendance rows.
    # ponytail: mirrors generate_excel_bytes math; kept separate so the
    # heavily-tested excel generator stays untouched"""
    user_map = {int(k): v for k, v in cfg.get('user_map', {}).items()}
    std_in = datetime.strptime(cfg.get('jam_masuk', '08:00'), '%H:%M')
    tol_dt = std_in + timedelta(minutes=int(cfg.get('toleransi', 15)))
    raw = {}
    for r in rows:
        ts = r['timestamp']
        if isinstance(ts, str):
            ts = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S')
        if ts.year <= ANOMALY_YEAR:
            continue
        nama = user_map.get(r['uid'], r.get('nama') or f"UID:{r['uid']}")
        raw.setdefault((nama, ts.date()), []).append(ts)
    out = []
    for (nama, tgl), taps in sorted(raw.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        taps.sort()
        masuk = taps[0]
        keluar = taps[-1] if len(taps) > 1 else None
        t = datetime.strptime(masuk.strftime('%H:%M'), '%H:%M')
        telat = int((t - std_in).total_seconds() // 60) if t > tol_dt else 0
        durasi = int((keluar - masuk).total_seconds() // 60) if keluar else 0
        out.append({'nama': nama, 'tanggal': tgl,
                    'masuk': masuk.strftime('%H:%M'),
                    'keluar': keluar.strftime('%H:%M') if keluar else '-',
                    'telat': telat, 'durasi': durasi,
                    'weekend': tgl.weekday() >= 5})
    return out

# ─────────────────────────────────────────────────────────────────────────────
# EXCEL GENERATOR — returns bytes instead of saving to file
# ─────────────────────────────────────────────────────────────────────────────
def generate_excel_bytes(rows, cfg):
    """Generate Excel and return as bytes (not saved to disk)."""
    import io
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise RuntimeError("openpyxl tidak terinstall.")

    jam_masuk_std  = cfg.get('jam_masuk','08:00')
    jam_keluar_std = cfg.get('jam_keluar','16:00')
    toleransi      = int(cfg.get('toleransi', 15))
    user_map       = {int(k):v for k,v in cfg.get('user_map',{}).items()}
    lang           = cfg.get('lang','en')

    def F(h): return PatternFill('solid', fgColor=h)
    def Bs(st='thin', co='FFB0B0B0'):
        s=Side(style=st,color=co); return Border(left=s,right=s,top=s,bottom=s)
    def Bm(co='FF888888'):
        s=Side(style='medium',color=co); return Border(left=s,right=s,top=s,bottom=s)
    def fnt(sz=9, bold=False, color='FF000000', italic=False):
        return Font(name='Arial', size=sz, bold=bold, color=color, italic=italic)
    C  = Alignment(horizontal='center', vertical='center', wrap_text=False)
    CW = Alignment(horizontal='center', vertical='center', wrap_text=True)
    L  = Alignment(horizontal='left',   vertical='center')
    R  = Alignment(horizontal='right',  vertical='center')

    CH='FF1A56DB'; CS='FF3B82F6'; CA='FF1E40AF'
    GBG='FFD1FAE5'; GTX='FF065F46'
    RBG='FFFEE2E2'; RTX='FF991B1B'
    YBG='FFFEF9C3'; YTX='FF854D0E'
    OBG='FFFED7AA'; OTX='FF9A3412'
    R1='FFFFFFFF'; R2='FFF0F4FF'; STR='FFE8F0FE'; BD='FFCBD5E1'; HT='FFFFFFFF'

    # month names
    MNAMES = (["","January","February","March","April","May","June",
               "July","August","September","October","November","December"]
              if lang=='en' else
              ["","Januari","Februari","Maret","April","Mei","Juni",
               "Juli","Agustus","September","Oktober","November","Desember"])
    DNAMES = (["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
              if lang=='en' else
              ["Sn","Sl","Rb","Km","Jm","Sb","Mg"])
    DFULL  = (["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
              if lang=='en' else
              ["Senin","Selasa","Rabu","Kamis","Jumat","Sabtu","Minggu"])

    # ── build data ────────────────────────────────────────────────────────────
    def _parse_ts(t):
        if isinstance(t, datetime): return t
        if isinstance(t, str):
            for fmt in ('%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M'):
                try: return datetime.strptime(t, fmt)
                except: pass
        return None

    _raw = {}
    for r in rows:
        ts = _parse_ts(r['timestamp'])
        if ts is None or ts.year <= 2000: continue
        nama = user_map.get(r['uid'], r.get('nama', f"UID:{r['uid']}"))
        tgl  = ts.date()
        _raw.setdefault((nama, tgl), []).append(ts)

    if not _raw: raise RuntimeError("No data for selected period.")

    class _Row:
        __slots__ = ['nama','tanggal','masuk','keluar','tap','tap_total',
                     'jam_masuk','jam_keluar','terlambat','lembur']

    daily_list = []
    for (nama, tgl), taps in sorted(_raw.items()):
        taps_s = sorted(taps)
        row = _Row()
        row.nama       = nama;   row.tanggal   = tgl
        row.masuk      = taps_s[0]; row.keluar = taps_s[-1]
        row.tap        = 2 if len(taps_s)>1 else 1
        row.tap_total  = len(taps_s)
        row.jam_masuk  = taps_s[0].strftime('%H:%M')
        row.jam_keluar = taps_s[-1].strftime('%H:%M') if len(taps_s)>1 else '-'
        row.terlambat  = 0; row.lembur = 0
        daily_list.append(row)

    class _DF:
        def __init__(self, lst): self._lst = lst
        def __iter__(self): return iter(self._lst)
        def __len__(self): return len(self._lst)
        def min_date(self): return min(r.tanggal for r in self._lst)
        def max_date(self): return max(r.tanggal for r in self._lst)
        def names(self): return sorted(set(r.nama for r in self._lst))
        def months(self): return sorted(set(r.tanggal.strftime('%Y-%m') for r in self._lst))
        def filter_month(self,m): return _DF([r for r in self._lst if r.tanggal.strftime('%Y-%m')==m])
        def filter_name(self,n):  return _DF([r for r in self._lst if r.nama==n])
        def filter_date(self,d):  return _DF([r for r in self._lst if r.tanggal==d])
        def filter_late(self):    return _DF([r for r in self._lst if r.terlambat>0])
        def sort_by(self,*keys):  return _DF(sorted(self._lst, key=lambda r: tuple(getattr(r,k) for k in keys)))

    daily = _DF(daily_list)

    std_in  = datetime.strptime(jam_masuk_std,'%H:%M')
    tol_dt  = std_in + timedelta(minutes=toleransi)
    std_out = datetime.strptime(jam_keluar_std,'%H:%M')

    for row in daily:
        try:
            t = datetime.strptime(row.jam_masuk,'%H:%M')
            row.terlambat = int((t-std_in).total_seconds()//60) if t>tol_dt else 0
        except: row.terlambat = 0
        try:
            if row.jam_keluar=='-': row.lembur=0
            else:
                t = datetime.strptime(row.jam_keluar,'%H:%M')
                row.lembur = int((t-std_out).total_seconds()//60) if t>std_out else 0
        except: row.lembur=0

    months    = daily.months()
    all_names = daily.names()
    wb = Workbook()
    wb.remove(wb.active)

    # ── KARTU ABSENSI per BULAN ───────────────────────────────────────────────
    for month in months:
        yr2,mo2   = int(month[:4]),int(month[5:])
        _,days_in = calendar.monthrange(yr2,mo2)
        bln_label = f"{MNAMES[mo2]} {yr2}"
        mdata     = daily.filter_month(month)

        ws = wb.create_sheet(f'{MNAMES[mo2][:3]}{yr2}')
        ws.sheet_view.showGridLines=False
        ws.page_setup.orientation='landscape'; ws.page_setup.fitToPage=True; ws.page_setup.fitToWidth=1

        TOT_COL=days_in+3; LAT_COL=days_in+4; OT_COL=days_in+5; AVG_COL=days_in+6
        LC=get_column_letter(days_in+6)

        ws.merge_cells(f'A1:{LC}1')
        ws['A1']=('CV REJEKI AMERTA JAYA — ATTENDANCE CARD' if lang=='en'
                  else 'CV REJEKI AMERTA JAYA — KARTU ABSENSI KARYAWAN')
        ws['A1'].font=fnt(13,True,HT); ws['A1'].fill=F(CH); ws['A1'].alignment=C
        ws.row_dimensions[1].height=22

        ws.merge_cells(f'A2:{LC}2')
        _std = ('Check-in' if lang=='en' else 'Masuk Std')
        _tol = ('Tolerance' if lang=='en' else 'Toleransi')
        ws['A2']=f"{bln_label.upper()}  |  {_std}: {jam_masuk_std}  |  {_tol}: {toleransi} min"
        ws['A2'].font=fnt(9,False,HT,italic=True); ws['A2'].fill=F(CS); ws['A2'].alignment=C
        ws.row_dimensions[2].height=15

        ws.merge_cells(f'A3:{LC}3')
        _printed = 'Printed:' if lang=='en' else 'Dicetak:'
        ws['A3']=f"{_printed} {datetime.now().strftime('%d %B %Y %H:%M')}"
        ws['A3'].font=fnt(8,False,'FF555555',italic=True); ws['A3'].fill=F('FFF8FAFF'); ws['A3'].alignment=R
        ws.row_dimensions[3].height=13

        ws.column_dimensions['A'].width=4; ws.column_dimensions['B'].width=15
        for d in range(1,days_in+1): ws.column_dimensions[get_column_letter(d+2)].width=5
        ws.column_dimensions[get_column_letter(TOT_COL)].width=5.5
        ws.column_dimensions[get_column_letter(LAT_COL)].width=9
        ws.column_dimensions[get_column_letter(OT_COL)].width=8
        ws.column_dimensions[get_column_letter(AVG_COL)].width=7

        HDR=4; ws.row_dimensions[HDR].height=28
        def hc(col,val,bg=CA):
            c=ws.cell(row=HDR,column=col,value=val)
            c.font=fnt(8,True,HT); c.fill=F(bg); c.alignment=CW; c.border=Bs('thin','FF93C5FD')
        hc(1,'No'); hc(2,'Name' if lang=='en' else 'Nama')
        for d in range(1,days_in+1):
            dt=datetime(yr2,mo2,d); dn=DNAMES[dt.weekday()]
            c=ws.cell(row=HDR,column=d+2,value=f'{d}\n{dn}')
            c.font=fnt(7,True,HT); c.alignment=CW; c.border=Bs('thin','FF93C5FD')
            if   dt.weekday()==6: c.fill=F('FF991B1B')
            elif dt.weekday()==5: c.fill=F('FF92400E')
            else:                 c.fill=F(CA)
        _lat_lbl='Late\n(min)' if lang=='en' else 'Terlambat\n(mnt)'
        _ot_lbl ='OT\n(min)'   if lang=='en' else 'Lembur\n(mnt)'
        _avg_lbl='Avg\nIn'     if lang=='en' else 'Rata\nMasuk'
        hc(TOT_COL,'∑\nHadir','FF065F46')
        hc(LAT_COL,_lat_lbl,'FF7C2D12')
        hc(OT_COL, _ot_lbl, 'FF1E40AF')
        hc(AVG_COL,_avg_lbl,'FF1E3A5F')

        for idx,name in enumerate(all_names,1):
            r=HDR+idx; ws.row_dimensions[r].height=17
            sub=mdata.filter_name(name)
            c=ws.cell(row=r,column=1,value=idx)
            c.font=fnt(8); c.fill=F(STR); c.alignment=C; c.border=Bs('thin',BD)
            c=ws.cell(row=r,column=2,value=name)
            c.font=fnt(9,True,'FF1E3A5F'); c.fill=F(STR); c.alignment=L; c.border=Bm('FF93C5FD')

            hadir=0; masuk_list=[]; total_late=0; total_ot=0
            for d in range(1,days_in+1):
                col=d+2; dt=datetime(yr2,mo2,d)
                dr=sub.filter_date(date(yr2,mo2,d))
                if len(dr)>0:
                    jam=dr._lst[0].jam_masuk; late=int(dr._lst[0].terlambat); ot=int(dr._lst[0].lembur)
                    c=ws.cell(row=r,column=col,value=jam)
                    masuk_list.append(jam); hadir+=1; total_late+=late; total_ot+=ot
                    if   late>0:          c.font=fnt(7,True,OTX); c.fill=F(OBG)
                    elif dt.weekday()==6: c.font=fnt(7,True,'FF7C3AED'); c.fill=F('FFEDE9FE')
                    elif dt.weekday()==5: c.font=fnt(7,True,'FF92400E'); c.fill=F(YBG)
                    else:                 c.font=fnt(7,False,GTX); c.fill=F(GBG)
                else:
                    c=ws.cell(row=r,column=col,value='')
                    if   dt.weekday()==6: c.fill=F('FFFCE7F3')
                    elif dt.weekday()==5: c.fill=F(YBG)
                    else:                 c.fill=F(RBG)
                    c.font=fnt(7)
                c.alignment=C; c.border=Bs('thin',BD)

            pct=round(hadir/days_in*100)
            c=ws.cell(row=r,column=TOT_COL,value=hadir)
            c.font=fnt(9,True,GTX if pct>=80 else RTX); c.fill=F(GBG if pct>=80 else RBG)
            c.alignment=C; c.border=Bm()
            c=ws.cell(row=r,column=LAT_COL,value=total_late if total_late else '-')
            c.font=fnt(9,total_late>0,OTX if total_late>0 else 'FF888888')
            c.fill=F(OBG if total_late>0 else R1); c.alignment=C; c.border=Bs('thin',BD)
            c=ws.cell(row=r,column=OT_COL,value=total_ot if total_ot else '-')
            c.font=fnt(9,total_ot>0,'FF1D4ED8' if total_ot>0 else 'FF888888')
            c.fill=F('FFE0EAFF' if total_ot>0 else R1); c.alignment=C; c.border=Bs('thin',BD)
            try:
                if masuk_list:
                    ts2=sum(datetime.strptime(t,'%H:%M').hour*3600+datetime.strptime(t,'%H:%M').minute*60 for t in masuk_list)
                    av=ts2//len(masuk_list); avg=f"{av//3600:02d}:{(av%3600)//60:02d}"
                else: avg='-'
            except: avg='-'
            c=ws.cell(row=r,column=AVG_COL,value=avg)
            c.font=fnt(8,False,'FF1E3A5F'); c.fill=F('FFE0EAFF'); c.alignment=C; c.border=Bs('thin',BD)

        # total harian
        rt=HDR+len(all_names)+1; ws.row_dimensions[rt].height=15
        ws.merge_cells(f'A{rt}:B{rt}')
        _tot_lbl='DAILY TOTAL' if lang=='en' else 'TOTAL HADIR HARIAN'
        c=ws.cell(row=rt,column=1,value=_tot_lbl)
        c.font=fnt(8,True,HT); c.fill=F(CA); c.alignment=C; c.border=Bs('thin',BD)
        for d in range(1,days_in+1):
            col=d+2; cnt=len(set(r.nama for r in mdata.filter_date(date(yr2,mo2,d))))
            c=ws.cell(row=rt,column=col,value=cnt if cnt else '')
            c.font=fnt(8,True,GTX if cnt else 'FFAAAAAA')
            c.fill=F(GBG if cnt else 'FFF1F5F9'); c.alignment=C; c.border=Bs('thin',BD)
        for col in (TOT_COL,LAT_COL,OT_COL,AVG_COL):
            c=ws.cell(row=rt,column=col,value=''); c.fill=F(CA); c.border=Bs('thin',BD)

        # legenda
        rl=rt+2; ws.row_dimensions[rl].height=13
        _legs = ([('On time',GBG,GTX),('Late',OBG,OTX),('Saturday',YBG,'FF92400E'),
                  ('Sunday','FFEDE9FE','FF7C3AED'),('Absent',RBG,RTX)]
                 if lang=='en' else
                 [('Tepat waktu',GBG,GTX),('Terlambat',OBG,OTX),('Sabtu',YBG,'FF92400E'),
                  ('Minggu','FFEDE9FE','FF7C3AED'),('Tidak hadir',RBG,RTX)])
        cl=1
        for lb,bg,tc in _legs:
            ws.merge_cells(start_row=rl,start_column=cl,end_row=rl,end_column=cl+2)
            c=ws.cell(row=rl,column=cl,value=f'■ {lb}')
            c.font=fnt(8,False,tc); c.fill=F(bg); c.alignment=L; cl+=3

    # ── REKAP ─────────────────────────────────────────────────────────────────
    wr=wb.create_sheet('Recap' if lang=='en' else 'Rekap'); wr.sheet_view.showGridLines=False
    for i,w in enumerate([5,15,15,8,8,10,8,12,10,10],1):
        wr.column_dimensions[get_column_letter(i)].width=w
    wr.merge_cells('A1:J1')
    _rh=('ATTENDANCE RECAP — CV REJEKI AMERTA JAYA' if lang=='en'
         else 'REKAP ABSENSI — CV REJEKI AMERTA JAYA')
    wr['A1']=_rh; wr['A1'].font=fnt(13,True,HT); wr['A1'].fill=F(CH); wr['A1'].alignment=C
    wr.row_dimensions[1].height=24
    wr.merge_cells('A2:J2')
    _tmin=daily.min_date().strftime('%d %B %Y'); _tmax=daily.max_date().strftime('%d %B %Y')
    _std_lbl='Std Check-in' if lang=='en' else 'Jam Masuk Std'
    _tol_lbl='Tolerance' if lang=='en' else 'Toleransi'
    wr['A2']=f"Period: {_tmin} - {_tmax}  |  {_std_lbl}: {jam_masuk_std}  |  {_tol_lbl}: {toleransi} min"
    wr['A2'].font=fnt(9,False,'FF444444',italic=True); wr['A2'].alignment=C
    wr['A2'].fill=F('FFF0F4FF'); wr.row_dimensions[2].height=15

    _rhdrs=(['No','Name','Month','Days','Present','Absent','% Present','Late (min)','OT (min)','Status']
            if lang=='en' else
            ['No','Nama','Bulan','Hari','Hadir','Tdk Hadir','% Hadir','Terlambat (mnt)','Lembur (mnt)','Status'])
    for i,h in enumerate(_rhdrs,1):
        c=wr.cell(row=3,column=i,value=h)
        c.font=fnt(9,True,HT); c.fill=F(CA); c.alignment=CW; c.border=Bs('thin','FF93C5FD')
    wr.row_dimensions[3].height=22
    r=4; no=1
    for name in all_names:
        sub=daily.filter_name(name)
        for month in months:
            yr2,mo2=int(month[:4]),int(month[5:])
            _,days_in=calendar.monthrange(yr2,mo2)
            md=sub.filter_month(month)
            if len(md)==0: continue
            hadir=len(md); tidak=days_in-hadir; pct=round(hadir/days_in*100)
            tl=sum(r2.terlambat for r2 in md); to=sum(r2.lembur for r2 in md)
            if lang=='en': status='Good' if pct>=90 else ('Fair' if pct>=75 else 'Poor')
            else:          status='Baik' if pct>=90 else ('Cukup' if pct>=75 else 'Kurang')
            bg=F(R1) if r%2==1 else F(R2)
            vals=[no,name,f"{MNAMES[mo2]} {yr2}",days_in,hadir,tidak,f"{pct}%",
                  tl if tl else '-',to if to else '-',status]
            for i,v in enumerate(vals,1):
                c=wr.cell(row=r,column=i,value=v)
                c.font=fnt(9,i==2); c.fill=bg; c.alignment=C if i!=2 else L; c.border=Bs('thin',BD)
            pc=wr.cell(row=r,column=7); sc=wr.cell(row=r,column=10)
            if pct>=90:
                for x in (pc,sc): x.font=fnt(9,True,GTX); x.fill=F(GBG)
            elif pct>=75:
                for x in (pc,sc): x.font=fnt(9,True,YTX); x.fill=F(YBG)
            else:
                for x in (pc,sc): x.font=fnt(9,True,RTX); x.fill=F(RBG)
            lc=wr.cell(row=r,column=8)
            if tl>0: lc.font=fnt(9,True,OTX); lc.fill=F(OBG)
            wr.row_dimensions[r].height=17; r+=1; no+=1

    # ── LOG DETAIL ────────────────────────────────────────────────────────────
    wd=wb.create_sheet('Log Detail'); wd.sheet_view.showGridLines=False
    for i,w in enumerate([5,14,12,10,10,10,8,10,10],1):
        wd.column_dimensions[get_column_letter(i)].width=w
    wd.merge_cells('A1:I1')
    _lh=('ATTENDANCE LOG DETAIL — CV REJEKI AMERTA JAYA' if lang=='en'
         else 'LOG DETAIL ABSENSI — CV REJEKI AMERTA JAYA')
    wd['A1']=_lh; wd['A1'].font=fnt(12,True,HT); wd['A1'].fill=F(CH); wd['A1'].alignment=C
    wd.row_dimensions[1].height=22
    _lhdrs=(['No','Name','Date','Day','Check-in','Check-out','Taps','Late','OT']
            if lang=='en' else
            ['No','Nama','Tanggal','Hari','Jam Masuk','Jam Keluar','Tap','Terlambat','Lembur'])
    for i,h in enumerate(_lhdrs,1):
        c=wd.cell(row=2,column=i,value=h)
        c.font=fnt(9,True,HT); c.fill=F(CA); c.alignment=C; c.border=Bs('thin','FF93C5FD')
    wd.row_dimensions[2].height=18
    ds=daily.sort_by('tanggal','nama')
    for i,row in enumerate(ds):
        r3=i+3; tgl=row.tanggal
        dn=DFULL[tgl.weekday()]; bg=F(R1) if i%2==0 else F(R2)
        late=int(row.terlambat); ot=int(row.lembur)
        tap_info=f"{row.tap_total}x" if hasattr(row,'tap_total') else '-'
        vals=[i+1,row.nama,tgl.strftime('%d/%m/%Y'),dn,
              row.jam_masuk,row.jam_keluar,tap_info,
              f"{late} min" if late>0 else '-',
              f"{ot} min"   if ot>0   else '-']
        for col,v in enumerate(vals,1):
            c=wd.cell(row=r3,column=col,value=v)
            c.font=fnt(9,col==2)
            if col==8 and late>0: c.font=fnt(9,True,OTX); c.fill=F(OBG)
            elif col==9 and ot>0: c.font=fnt(9,True,'FF1D4ED8'); c.fill=F('FFE0EAFF')
            elif col==7 and hasattr(row,'tap_total') and row.tap_total>2:
                c.font=fnt(9,True,'FF6B21A8'); c.fill=F('FFEDE9FE')
            else: c.fill=bg
            c.alignment=C if col!=2 else L; c.border=Bs('thin',BD)
        wd.row_dimensions[r3].height=15

    ms=[s for s in wb.sheetnames if s not in ('Recap','Rekap','Log Detail')]
    wb._sheets=[wb[s] for s in ms+[s for s in ('Recap','Rekap','Log Detail') if s in wb.sheetnames]]

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def parse_excel_for_preview(data_bytes, sheet_idx=0):
    """Parse Excel bytes and return (headers, rows) for Treeview display."""
    import io
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data_bytes), read_only=True, data_only=True)
        ws = wb.worksheets[sheet_idx]
        all_rows = list(ws.iter_rows(values_only=True))
        if not all_rows: return [], []
        # skip merged title rows (usually rows 1-3), find header row
        # header = first row where most cells are non-None
        hdr_idx = 0
        for i, row in enumerate(all_rows):
            non_none = sum(1 for c in row if c is not None)
            if non_none >= 3:
                hdr_idx = i
                break
        headers = [str(c) if c is not None else '' for c in all_rows[hdr_idx]]
        # remove empty trailing cols
        while headers and not headers[-1]: headers.pop()
        n_cols = len(headers)
        data_rows = []
        for row in all_rows[hdr_idx+1:]:
            r = [str(c) if c is not None else '' for c in row[:n_cols]]
            if any(c for c in r): data_rows.append(r)
        wb.close()
        return headers, data_rows
    except Exception as e:
        return ['Error'], [[str(e)]]


# ─────────────────────────────────────────────────────────────────────────────
# UI — PySide6 (Qt). Logic layer above is UI-agnostic; everything below is view.
# ─────────────────────────────────────────────────────────────────────────────
ACCENT = '#1A56DB'

THEMES = {
    'light': dict(bg='#F1F5F9', card='#FFFFFF', border='#E2E8F0', input_border='#CBD5E1',
                  text='#0F172A', subtext='#475569', input_bg='#FFFFFF',
                  alt='#F8FAFC', sel='#DBEAFE', sel_text='#0F172A',
                  head='#F1F5F9', head_text='#334155', disabled='#94A3B8',
                  info='#1e40af'),
    'dark':  dict(bg='#0B1220', card='#1E293B', border='#334155', input_border='#475569',
                  text='#E2E8F0', subtext='#94A3B8', input_bg='#0F172A',
                  alt='#243247', sel='#1e40af', sel_text='#FFFFFF',
                  head='#16223A', head_text='#CBD5E1', disabled='#64748B',
                  info='#93C5FD'),
}

def build_qss(theme):
    t = THEMES[theme]
    return f"""
* {{ font-family: 'Segoe UI'; font-size: 9pt; }}
QMainWindow, QDialog {{ background: {t['bg']}; }}
QLabel, QCheckBox, QRadioButton {{ color: {t['text']}; font-weight: 400; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px; height: 14px; background: {t['input_bg']};
    border: 2px solid {t['input_border']};
}}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {ACCENT}; border-color: {ACCENT};
}}
QWidget#hdr QLabel {{ color: white; }}
QWidget#hdr QPushButton {{
    background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.45); color: white;
}}
QWidget#hdr QPushButton:hover {{ background: white; color: {ACCENT}; }}
QGroupBox {{
    background: {t['card']}; border: 1px solid {t['border']}; border-radius: 10px;
    margin-top: 9px; padding: 10px 8px 8px 8px; font-weight: 600; color: {ACCENT};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}
QPushButton {{
    background: {t['card']}; border: 1px solid {t['input_border']}; border-radius: 6px;
    padding: 5px 8px; color: {t['text']};
}}
QPushButton:hover {{ background: {ACCENT}; border-color: {ACCENT}; color: white; }}
QPushButton:pressed {{ background: #1e40af; color: white; }}
QPushButton:disabled {{ background: {t['bg']}; color: {t['disabled']}; border-color: {t['border']}; }}
QPushButton[accent="true"] {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton[accent="true"]:hover {{ background: #1e40af; }}
QLineEdit, QComboBox {{
    background: {t['input_bg']}; border: 1px solid {t['input_border']}; border-radius: 6px;
    padding: 4px 8px; color: {t['text']};
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {t['card']}; color: {t['text']};
    selection-background-color: {ACCENT}; selection-color: white; }}
QTabWidget::pane {{ background: {t['card']}; border: 1px solid {t['border']}; border-radius: 10px; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {t['subtext']}; padding: 7px 16px; margin-right: 4px;
    border-top-left-radius: 8px; border-top-right-radius: 8px;
}}
QTabBar::tab:selected {{ background: {t['card']}; color: {ACCENT}; font-weight: 600;
    border: 1px solid {t['border']}; border-bottom: none; }}
QTabBar::tab:hover:!selected {{ color: {ACCENT}; }}
QTableWidget {{
    background: {t['card']}; border: none; gridline-color: {t['bg']};
    alternate-background-color: {t['alt']}; color: {t['text']};
    selection-background-color: {t['sel']}; selection-color: {t['sel_text']};
}}
QHeaderView::section {{
    background: {t['head']}; color: {t['head_text']}; font-weight: 600; border: none;
    border-bottom: 2px solid {t['border']}; padding: 6px 4px;
}}
QPlainTextEdit {{ background: #0F172A; color: #CBD5E1; border-radius: 8px;
    font-family: Consolas; font-size: 8pt; border: none; padding: 6px; }}
QSplitter::handle {{ background: {t['border']}; width: 3px; }}
QStatusBar {{ background: {t['head']}; color: {t['head_text']}; }}
QMessageBox QLabel {{ color: {t['text']}; }}
QMenu {{ background: {t['card']}; color: {t['text']}; border: 1px solid {t['border']}; }}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}
QProgressBar {{ background: {t['border']}; border-radius: 6px; text-align: center;
    height: 14px; color: {t['text']}; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}
"""

def _icon():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app_icon.png')
    return QIcon(p) if os.path.exists(p) else QIcon()

def _fill_table(tbl, rows, row_colors=None):
    """rows = list of value-tuples; row_colors = list of hex or None per row."""
    tbl.setRowCount(len(rows))
    for r, vals in enumerate(rows):
        for c, v in enumerate(vals):
            it = QTableWidgetItem(str(v))
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            if row_colors and row_colors[r]:
                it.setBackground(QColor(row_colors[r]))
            tbl.setItem(r, c, it)

def _mk_table(headers, widths=None, stretch_col=None):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.verticalHeader().setDefaultSectionSize(26)
    if widths:
        for i, w in enumerate(widths):
            if w: t.setColumnWidth(i, w)
    if stretch_col is not None:
        t.horizontalHeader().setSectionResizeMode(stretch_col, QHeaderView.Stretch)
    return t


# ─────────────────────────────────────────────────────────────────────────────
# DIALOGS
# ─────────────────────────────────────────────────────────────────────────────
class SettingsDialog(QDialog):
    def __init__(self, parent, cfg, on_save):
        super().__init__(parent)
        self.setWindowTitle('Settings'); self.setModal(True)
        self.cfg = cfg; self.on_save = on_save
        lay = QVBoxLayout(self)
        form = QFormLayout()
        fields = [('IP Address','ip'),('Port','port'),
                  ('Standard Check-in (HH:MM)','jam_masuk'),
                  ('Standard Check-out (HH:MM)','jam_keluar'),
                  ('Late Tolerance (minutes)','toleransi'),
                  ('Recovery anchor date (YYYY-MM-DD, blank=auto)','anomaly_anchor')]
        self.edits = {}
        for label, key in fields:
            e = QLineEdit(str(cfg.get(key,'')))
            form.addRow(label, e); self.edits[key] = e
        lay.addLayout(form)
        self.checks = {}
        for label, key, dflt in [
                ('Auto backup CSV after pull','auto_backup',False),
                ('Auto-recover clock-reset (year 2000) records','anomaly_recover',True),
                ('Start with Windows (minimized to tray)','autostart',False),
                ('Auto-start Live Monitor + punch notifications','live_autostart',False)]:
            cb = QCheckBox(label); cb.setChecked(bool(cfg.get(key,dflt)))
            lay.addWidget(cb); self.checks[key] = cb
        # staff names are managed via "Manage Users" (device is the source of truth)
        btns = QHBoxLayout()
        ok = QPushButton('💾 Save'); ok.setProperty('accent', True); ok.clicked.connect(self._save)
        cancel = QPushButton('Cancel'); cancel.clicked.connect(self.reject)
        btns.addStretch(); btns.addWidget(ok); btns.addWidget(cancel)
        lay.addLayout(btns)

    def _save(self):
        for key, e in self.edits.items():
            v = e.text().strip()
            if key == 'toleransi':
                try: v = int(v)
                except ValueError: pass
            self.cfg[key] = v
        for key, cb in self.checks.items():
            self.cfg[key] = cb.isChecked()
        save_config(self.cfg)
        self.on_save(self.cfg)
        self.accept()


class DeviceInfoDialog(QDialog):
    def __init__(self, parent, info_dict):
        super().__init__(parent)
        self.setWindowTitle('Device Info'); self.setModal(True)
        lay = QVBoxLayout(self)
        hdr = QLabel('Device Info — eFace10')
        hdr.setStyleSheet(f'background:{ACCENT}; color:white; font-weight:600; '
                          'padding:8px 12px; border-radius:6px;')
        lay.addWidget(hdr)
        form = QFormLayout()
        for k, v in info_dict.items():
            lbl = QLabel(f'<b>{k}:</b>'); val = QLabel(str(v))
            form.addRow(lbl, val)
        lay.addLayout(form)
        close = QPushButton('Close'); close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignCenter)


class UserManagerDialog(QDialog):
    def __init__(self, parent, users, app=None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle('Users on Device'); self.setModal(True)
        self.resize(460, 480)
        lay = QVBoxLayout(self)
        hdr = QLabel('Users registered on eFace10')
        hdr.setStyleSheet(f'background:{ACCENT}; color:white; font-weight:600; '
                          'padding:8px 12px; border-radius:6px;')
        lay.addWidget(hdr)
        self.table = _mk_table(['UID','Name','Card ID'], [70,180,120], stretch_col=1)
        self.table.itemSelectionChanged.connect(self._on_select)
        lay.addWidget(self.table)
        ef = QHBoxLayout()
        ef.addWidget(QLabel('UID:'))
        self.uid_edit = QLineEdit(); self.uid_edit.setFixedWidth(60); ef.addWidget(self.uid_edit)
        ef.addWidget(QLabel('Name:'))
        self.name_edit = QLineEdit(); ef.addWidget(self.name_edit)
        save = QPushButton('💾 Add / Rename'); save.clicked.connect(self._save_user); ef.addWidget(save)
        dele = QPushButton('🗑 Delete'); dele.clicked.connect(self._delete_user); ef.addWidget(dele)
        lay.addLayout(ef)
        note = QLabel('Face/fingerprint enrollment is done on the device itself.')
        note.setStyleSheet('color:#888; font-size:8pt;'); lay.addWidget(note)
        bf = QHBoxLayout()
        self.total_lbl = QLabel(); bf.addWidget(self.total_lbl); bf.addStretch()
        close = QPushButton('Close'); close.clicked.connect(self.accept); bf.addWidget(close)
        lay.addLayout(bf)
        self._fill(users)

    def _fill(self, users):
        _fill_table(self.table, [(u['uid'], u['nama'], u.get('card_id','')) for u in users])
        self.total_lbl.setText(f'Total: {len(users)} users')

    def _on_select(self):
        r = self.table.currentRow()
        if r >= 0:
            self.uid_edit.setText(self.table.item(r,0).text())
            self.name_edit.setText(self.table.item(r,1).text())

    def _save_user(self):
        uid = self.uid_edit.text().strip(); name = self.name_edit.text().strip()
        if not uid.isdigit() or not name:
            QMessageBox.warning(self,'Invalid','UID must be a number and name cannot be empty.'); return
        if self.app: self.app.device_user_save(uid, name, self)

    def _delete_user(self):
        uid = self.uid_edit.text().strip()
        if not uid.isdigit():
            QMessageBox.warning(self,'Invalid','Select a user or enter a UID first.'); return
        if QMessageBox.question(self,'Delete',
                f'Delete user {uid} from the DEVICE?\n'
                'Face/fingerprint templates on the device are removed too.') != QMessageBox.Yes:
            return
        if self.app: self.app.device_user_delete(uid, self)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN WINDOW
# ─────────────────────────────────────────────────────────────────────────────
class _Bridge(QObject):
    call = Signal(object)   # thread-safe "run this on the UI thread"


class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'ZKTeco eFace10 Utility v{APP_VERSION} — CV RAJ')
        self.setMinimumSize(1100, 680)
        self.setWindowIcon(_icon())
        self._bridge = _Bridge()
        self._bridge.call.connect(lambda f: f())
        self.ui = self._bridge.call.emit   # usable from any thread
        self.cfg = load_config()
        self._cache = []
        self._current_snap_bytes = None   # Excel bytes in memory
        self._current_snap_id = None
        self._toasts = []
        init_db()
        self._build_ui()
        self._apply_theme(self.cfg.get('theme', 'light'))
        self._update_status()
        self._refresh_history()
        self._refresh_today()
        try:
            from updater import cleanup_old_exe
            cleanup_old_exe()
        except ImportError: pass
        self._tray_setup()
        if self.cfg.get('live_autostart', False):
            QTimer.singleShot(1500, self._toggle_live)
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._clock_tick)
        self._clock_timer.start(600_000)   # background clock guard, every 10 min

    # ── System tray ───────────────────────────────────────────────────────────
    def _tray_setup(self):
        self._tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable(): return
        self._tray = QSystemTrayIcon(_icon(), self)
        self._tray.setToolTip('ZKTeco Utility')
        menu = QMenu()
        a_open = QAction('Open Dashboard', menu); a_open.triggered.connect(self._tray_open)
        a_exit = QAction('Exit', menu); a_exit.triggered.connect(self._tray_exit)
        menu.addAction(a_open); menu.addSeparator(); menu.addAction(a_exit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda reason: self._tray_open() if reason == QSystemTrayIcon.Trigger else None)
        self._tray.show()

    def closeEvent(self, ev):
        if self._tray:   # keep running: live monitor + clock guard stay on
            ev.ignore(); self.hide()
        else:
            ev.accept()

    def _tray_open(self):
        self.showNormal(); self.raise_(); self.activateWindow()

    def _tray_exit(self):
        self._live_want = False
        if self._tray: self._tray.hide()
        QApplication.quit()

    def _clock_tick(self):
        """Every 10 min: make sure the device clock matches the PC (power-loss guard).
        # ponytail: skipped while live monitor runs — its reconnect cycle checks instead"""
        def check():
            conn = None
            try:
                conn = self._get_conn()
                self._check_clock(conn, quiet=True)
            except Exception: pass   # device off/unreachable — silent, retry next tick
            finally:
                try:
                    if conn: conn.disconnect()
                except Exception: pass
        if not getattr(self, '_live_want', False):
            threading.Thread(target=check, daemon=True).start()

    # ── UI construction ───────────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # Header
        hdr = QWidget(); hdr.setObjectName('hdr'); hdr.setStyleSheet(f'background:{ACCENT};')
        hl = QHBoxLayout(hdr); hl.setContentsMargins(14,8,14,8)
        title = QLabel('ZKTeco eFace10 Utility  ·  CV RAJ')
        title.setStyleSheet('color:white; font-size:12pt; font-weight:700;')
        hl.addWidget(title); hl.addStretch()
        self.lang_cb = QComboBox(); self.lang_cb.addItems(['en','id'])
        self.lang_cb.setCurrentText(self.cfg.get('lang','en'))
        self.lang_cb.currentTextChanged.connect(self._on_lang_change)
        self.lang_cb.setFixedWidth(56)
        hl.addWidget(QLabel('🌐')); hl.addWidget(self.lang_cb)
        self.theme_btn = QPushButton('🌙')
        self.theme_btn.setFixedWidth(36); self.theme_btn.setToolTip('Dark / light mode')
        self.theme_btn.clicked.connect(self._toggle_theme)
        hl.addWidget(self.theme_btn)
        ver = QLabel(f'v{APP_VERSION}'); ver.setStyleSheet('color:#93C5FD;')
        hl.addWidget(ver)
        b_upd = QPushButton('⬆ Update'); b_upd.clicked.connect(self._check_update); hl.addWidget(b_upd)
        b_set = QPushButton('⚙ Settings'); b_set.clicked.connect(self._open_settings); hl.addWidget(b_set)
        root.addWidget(hdr)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        # ══ LEFT PANEL ════════════════════════════════════════════════════════
        left = QWidget(); left.setMinimumWidth(380); left.setMaximumWidth(460)
        ll = QVBoxLayout(left); ll.setContentsMargins(8,8,4,8); ll.setSpacing(8)

        # Connection card
        gc = QGroupBox('Device Connection')
        gl = QGridLayout(gc)
        gl.addWidget(QLabel('IP:'), 0, 0)
        self.ip_edit = QLineEdit(self.cfg['ip']); gl.addWidget(self.ip_edit, 0, 1)
        gl.addWidget(QLabel('Port:'), 0, 2)
        self.port_edit = QLineEdit(str(self.cfg['port'])); self.port_edit.setFixedWidth(64)
        gl.addWidget(self.port_edit, 0, 3)
        self.conn_lbl = QLabel('● Not connected'); self.conn_lbl.setStyleSheet('color:#888; font-size:8pt;')
        gl.addWidget(self.conn_lbl, 0, 4)
        btns = [('🔌 Test Connection', lambda: self._run(self._do_test)),
                ('ℹ Device Info',      lambda: self._run(self._do_info)),
                ('📡 Live Monitor',    self._toggle_live),
                ('👤 Manage Users',    lambda: self._run(self._do_users)),
                ('♻ Restart Device',   self._confirm_restart)]
        for i, (txt, cmd) in enumerate(btns):   # 2 columns so labels never clip at 380px
            b = QPushButton(txt); b.clicked.connect(cmd)
            gl.addWidget(b, 1 + i//2, (i%2)*2, 1, 2)
            if txt.startswith('📡'): self.btn_live = b
        ll.addWidget(gc)

        # Workflow card
        gw = QGroupBox('Workflow')
        wl = QGridLayout(gw)
        self.btn_pull = QPushButton('📥 1 · Pull Data'); self.btn_pull.setProperty('accent', True)
        self.btn_pull.clicked.connect(lambda: self._run(self._do_pull))
        wl.addWidget(self.btn_pull, 0, 0, 1, 2)
        wl.addWidget(QLabel('Month:'), 1, 0)
        self.bulan_cb = QComboBox()
        self.bulan_cb.addItems(['All','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'])
        wl.addWidget(self.bulan_cb, 1, 1)
        wl.addWidget(QLabel('Year:'), 2, 0)
        now = datetime.now()
        self.tahun_cb = QComboBox()
        self.tahun_cb.addItems([str(y) for y in range(now.year-3, now.year+2)])
        self.tahun_cb.setCurrentText(str(now.year))
        wl.addWidget(self.tahun_cb, 2, 1)
        srcrow = QHBoxLayout()
        srcrow.addWidget(QLabel('Source:'))
        self.src_db = QRadioButton('Database'); self.src_db.setChecked(True)
        self.src_cache = QRadioButton('Cache')
        srcrow.addWidget(self.src_db); srcrow.addWidget(self.src_cache); srcrow.addStretch()
        wl.addLayout(srcrow, 3, 0, 1, 2)
        self.btn_report = QPushButton('📊 2 · Preview Report'); self.btn_report.setProperty('accent', True)
        self.btn_report.clicked.connect(lambda: self._run(self._do_report))
        wl.addWidget(self.btn_report, 4, 0, 1, 2)
        sc = QHBoxLayout()
        self.btn_all = QPushButton('⚡ All at Once'); self.btn_all.clicked.connect(lambda: self._run(self._do_all))
        b_clear = QPushButton('🗑 Clear Device Log'); b_clear.clicked.connect(self._confirm_clear)
        sc.addWidget(self.btn_all); sc.addWidget(b_clear)
        wl.addLayout(sc, 5, 0, 1, 2)
        self.data_lbl = QLabel('...'); self.data_lbl.setStyleSheet('color:#1e40af; font-size:8pt;')
        wl.addWidget(self.data_lbl, 6, 0, 1, 2, alignment=Qt.AlignCenter)
        ll.addWidget(gw)

        # Log card
        glog = QGroupBox('Log')
        loglay = QVBoxLayout(glog)
        self.log_box = QPlainTextEdit(); self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(2000)
        loglay.addWidget(self.log_box)
        ll.addWidget(glog, 1)
        split.addWidget(left)

        # ══ RIGHT PANEL — tabs ════════════════════════════════════════════════
        right = QWidget()
        rl = QVBoxLayout(right); rl.setContentsMargins(4,8,8,8)
        self.tabs = QTabWidget()
        rl.addWidget(self.tabs)
        split.addWidget(right)
        split.setStretchFactor(1, 1)

        # Tab 0: Today
        tab_today = QWidget(); tl = QVBoxLayout(tab_today)
        trow = QHBoxLayout()
        cap = QLabel('Absensi hari ini'); cap.setStyleSheet('font-weight:600;')
        trow.addWidget(cap); trow.addStretch()
        b_ref = QPushButton('🔄 Refresh'); b_ref.clicked.connect(self._refresh_today)
        trow.addWidget(b_ref)
        tl.addLayout(trow)
        tiles = QHBoxLayout()
        self.tile_hadir = self._mktile(tiles, 'Hadir', '#D1FAE5', '#065F46')
        self.tile_telat = self._mktile(tiles, 'Telat', '#FED7AA', '#9A3412')
        self.tile_absen = self._mktile(tiles, 'Belum Absen', '#FEE2E2', '#991B1B')
        tl.addLayout(tiles)
        self.today_tbl = _mk_table(['Nama','Jam Masuk','Status'], [180,100,110], stretch_col=0)
        tl.addWidget(self.today_tbl, 1)
        self.tabs.addTab(tab_today, '🏠 Today')
        self.tabs.currentChanged.connect(lambda i: self._refresh_today() if i == 0 else None)

        # Tab 1: Report Viewer
        tab_view = QWidget(); vl = QVBoxLayout(tab_view)
        vt = QHBoxLayout()
        vt.addWidget(QLabel('Sheet:'))
        self.sheet_cb = QComboBox(); self.sheet_cb.setMinimumWidth(160)
        self.sheet_cb.activated.connect(self._on_sheet_change)
        vt.addWidget(self.sheet_cb)
        b_save = QPushButton('💾 Save to File'); b_save.clicked.connect(self._save_excel); vt.addWidget(b_save)
        b_rref = QPushButton('🔄 Refresh'); b_rref.clicked.connect(lambda: self._run(self._do_report)); vt.addWidget(b_rref)
        vt.addStretch()
        self.snap_lbl = QLabel('No report loaded'); self.snap_lbl.setStyleSheet('color:#64748B; font-size:8pt;')
        vt.addWidget(self.snap_lbl)
        vl.addLayout(vt)
        self.report_tbl = _mk_table([])
        vl.addWidget(self.report_tbl, 1)
        self.tabs.addTab(tab_view, '📊 Report Viewer')

        # Tab 2: Pull History
        tab_hist = QWidget(); hlv = QVBoxLayout(tab_hist)
        gh1 = QGroupBox('Pull Sessions'); g1 = QVBoxLayout(gh1)
        self.sess_tbl = _mk_table(['ID','Pull Date','Records','New','Device IP'], [46,160,80,80,120], stretch_col=1)
        g1.addWidget(self.sess_tbl)
        sb = QHBoxLayout()
        b_ldp = QPushButton('📊 Load to Preview'); b_ldp.clicked.connect(self._load_session_to_preview)
        b_dls = QPushButton('🗑 Delete Session'); b_dls.clicked.connect(self._delete_session)
        sb.addWidget(b_ldp); sb.addWidget(b_dls); sb.addStretch()
        g1.addLayout(sb)
        hlv.addWidget(gh1, 1)
        gh2 = QGroupBox('Saved Reports (in database)'); g2 = QVBoxLayout(gh2)
        self.snap_tbl = _mk_table(['ID','Report Label','Created','Period'], [46,220,160,90], stretch_col=1)
        self.snap_tbl.itemDoubleClicked.connect(lambda _: self._load_snapshot())
        g2.addWidget(self.snap_tbl)
        nb2 = QHBoxLayout()
        b_lr = QPushButton('📂 Load Report'); b_lr.clicked.connect(self._load_snapshot)
        b_ex = QPushButton('💾 Export to File'); b_ex.clicked.connect(self._export_snapshot)
        b_dl = QPushButton('🗑 Delete'); b_dl.clicked.connect(self._delete_snapshot)
        nb2.addWidget(b_lr); nb2.addWidget(b_ex); nb2.addWidget(b_dl); nb2.addStretch()
        g2.addLayout(nb2)
        hlv.addWidget(gh2, 1)
        self.tabs.addTab(tab_hist, '📋 Pull History')

        # Tab 3: Daily view (straight from DB)
        tab_daily = QWidget(); dl = QVBoxLayout(tab_daily)
        drow = QHBoxLayout()
        drow.addWidget(QLabel('Bulan:'))
        self.daily_bulan = QComboBox(); self.daily_bulan.addItems([str(i) for i in range(1,13)])
        self.daily_bulan.setCurrentText(str(now.month)); drow.addWidget(self.daily_bulan)
        drow.addWidget(QLabel('Tahun:'))
        self.daily_tahun = QComboBox()
        self.daily_tahun.addItems([str(y) for y in range(now.year-3, now.year+2)])
        self.daily_tahun.setCurrentText(str(now.year)); drow.addWidget(self.daily_tahun)
        b_dload = QPushButton('🔍 Load'); b_dload.setProperty('accent', True)
        b_dload.clicked.connect(self._refresh_daily); drow.addWidget(b_dload)
        drow.addStretch()
        dl.addLayout(drow)
        self.daily_tbl = _mk_table(['Nama','Tanggal','Masuk','Keluar','Telat (mnt)','Durasi'],
                                   [150,95,70,70,90,90], stretch_col=0)
        dl.addWidget(self.daily_tbl, 1)
        self.tabs.addTab(tab_daily, '📅 Daily')

        # Status bar
        self.statusBar().showMessage('Ready.')
        self._btns = [self.btn_pull, self.btn_report, self.btn_all]

    def _mktile(self, parent_layout, caption, bg, fg):
        f = QFrame()
        f.setStyleSheet(f'background:{bg}; border-radius:10px;')
        v = QVBoxLayout(f); v.setContentsMargins(16,10,16,10)
        num = QLabel('0'); num.setAlignment(Qt.AlignCenter)
        num.setStyleSheet(f'color:{fg}; font-size:22pt; font-weight:700; background:transparent;')
        capl = QLabel(caption); capl.setAlignment(Qt.AlignCenter)
        capl.setStyleSheet(f'color:{fg}; background:transparent;')
        v.addWidget(num); v.addWidget(capl)
        parent_layout.addWidget(f)
        return num

    # ── Theme ─────────────────────────────────────────────────────────────────
    def _toggle_theme(self):
        new = 'dark' if self.cfg.get('theme','light') == 'light' else 'light'
        self.cfg['theme'] = new; save_config(self.cfg)
        self._apply_theme(new)

    def _apply_theme(self, theme):
        QApplication.instance().setStyleSheet(build_qss(theme))
        self.theme_btn.setText('☀' if theme == 'dark' else '🌙')
        self.data_lbl.setStyleSheet(f"color:{THEMES[theme]['info']}; font-size:8pt;")

    # ── Dashboards ────────────────────────────────────────────────────────────
    def _refresh_today(self):
        today = date.today()
        rows = [r for r in db_query_attendance(today.year, today.month)
                if r['timestamp'].date() == today]
        drows = compute_daily_rows(rows, self.cfg)
        data, colors = [], []
        for d in drows:
            status = f"Telat {d['telat']}m" if d['telat'] else 'Hadir'
            data.append((d['nama'], d['masuk'], status))
            colors.append('#FED7AA' if d['telat'] else '#D1FAE5')
        _fill_table(self.today_tbl, data, colors)
        telat = sum(1 for d in drows if d['telat'])
        hadir = len(drows) - telat
        absen = max(len(self.cfg.get('user_map',{})) - len(drows), 0)
        self.tile_hadir.setText(str(hadir))
        self.tile_telat.setText(str(telat))
        self.tile_absen.setText(str(absen))

    def _refresh_daily(self):
        y = int(self.daily_tahun.currentText()); m = int(self.daily_bulan.currentText())
        drows = compute_daily_rows(db_query_attendance(y, m), self.cfg)
        data, colors = [], []
        for d in drows:
            dur = f"{d['durasi']//60}j {d['durasi']%60}m" if d['durasi'] else '-'
            data.append((d['nama'], d['tanggal'].strftime('%d-%m-%Y'), d['masuk'],
                         d['keluar'], d['telat'] or '-', dur))
            colors.append('#FEF9C3' if d['weekend'] else ('#FED7AA' if d['telat'] else None))
        _fill_table(self.daily_tbl, data, colors)
        self._log(f'Daily view: {len(drows)} baris untuk {m}/{y}')

    # ── UI helpers ────────────────────────────────────────────────────────────
    def _log(self, msg):
        def do():
            ts = datetime.now().strftime('%H:%M:%S')
            self.log_box.appendPlainText(f'[{ts}] {msg}')
            self.statusBar().showMessage(msg)
        self.ui(do)   # safe from any thread

    def _get_conn(self):
        from zk import ZK
        return ZK(self.ip_edit.text().strip(), port=int(self.port_edit.text()),
                  timeout=15, password=0, force_udp=False, ommit_ping=False).connect()

    def _set_buttons(self, enabled):
        for b in self._btns: b.setEnabled(enabled)

    def _run(self, fn):
        self._set_buttons(False)
        threading.Thread(target=self._worker, args=(fn,), daemon=True).start()

    def _worker(self, fn):
        try: fn()
        except Exception as e:
            self._log(f'[ERROR] {e}')
            self.ui(lambda e=e: QMessageBox.critical(self, 'Error', str(e)))
        finally:
            self.ui(lambda: self._set_buttons(True))
            self.ui(self._update_status)

    def _update_status(self):
        n = db_count()
        self.data_lbl.setText(f'DB: {n:,} records  |  Cache: {len(self._cache):,}')

    def _open_settings(self):
        def on_save(new_cfg):
            self.cfg = new_cfg
            self.ip_edit.setText(new_cfg['ip'])
            self.port_edit.setText(str(new_cfg['port']))
            self._apply_autostart(new_cfg.get('autostart', False))
            self._log('✓ Settings saved')
        SettingsDialog(self, self.cfg, on_save).exec()

    def _apply_autostart(self, enable):
        """Register/unregister the exe in HKCU Run so it launches at Windows login."""
        if sys.platform != 'win32': return
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Run', 0, winreg.KEY_SET_VALUE)
            if enable:
                if not getattr(sys, 'frozen', False):
                    self._log('⚠ Autostart only works from the built .exe, not from .py'); return
                winreg.SetValueEx(key, 'ZKTeco_Utility', 0, winreg.REG_SZ,
                                  f'"{sys.executable}" --minimized')
                self._log('✓ Autostart ON — app will start minimized at Windows login')
            else:
                try: winreg.DeleteValue(key, 'ZKTeco_Utility')
                except FileNotFoundError: pass
                self._log('✓ Autostart OFF')
            key.Close()
        except Exception as e:
            self._log(f'[ERROR] Autostart: {e}')

    def _toast(self, msg):
        """Notification bottom-right with fade-in; shows even while hidden to tray."""
        t = QLabel(msg)
        t.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        t.setStyleSheet(f'background:{ACCENT}; color:white; font-size:10pt; font-weight:600; '
                        'padding:12px 18px; border-radius:10px;')
        t.adjustSize()
        scr = QApplication.primaryScreen().availableGeometry()
        t.move(scr.right() - t.width() - 16, scr.bottom() - t.height() - 16)
        eff = QGraphicsOpacityEffect(t); t.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b'opacity', t)
        anim.setDuration(250); anim.setStartValue(0); anim.setEndValue(1)
        anim.setEasingCurve(QEasingCurve.OutCubic); anim.start()
        t.show()
        self._toasts.append(t)   # keep a ref so it isn't GC'd
        QTimer.singleShot(4000, lambda: (t.close(), self._toasts.remove(t) if t in self._toasts else None))

    def _on_lang_change(self, lang):
        self.cfg['lang'] = lang; save_config(self.cfg)
        # lang only affects generated reports — applies on next Preview, no restart needed
        self._log(f'✓ Report language: {"English" if lang == "en" else "Indonesia"}')

    def _confirm_clear(self):
        if QMessageBox.question(self, 'Confirm',
                'Delete ALL attendance log from device memory?\n\n'
                'Data already in local database will NOT be deleted.') == QMessageBox.Yes:
            self._run(self._do_clear)

    # ── Preview / Viewer ──────────────────────────────────────────────────────
    def _load_excel_to_viewer(self, data_bytes, label=''):
        try:
            from openpyxl import load_workbook
            import io
            wb = load_workbook(io.BytesIO(data_bytes), read_only=True, data_only=True)
            sheet_names = wb.sheetnames
            wb.close()
            self._current_snap_bytes = data_bytes
            self.sheet_cb.clear(); self.sheet_cb.addItems(sheet_names)
            self.snap_lbl.setText(label or 'Report loaded')
            self._render_sheet(data_bytes, 0)
        except Exception as e:
            self._log(f'[ERROR] Cannot load preview: {e}')

    def _render_sheet(self, data_bytes, sheet_idx):
        headers, rows = parse_excel_for_preview(data_bytes, sheet_idx)
        def show():
            self.report_tbl.setColumnCount(len(headers))
            self.report_tbl.setHorizontalHeaderLabels([h.replace('\n',' ') for h in headers])
            _fill_table(self.report_tbl, rows)
            self.report_tbl.resizeColumnsToContents()
            for i in range(len(headers)):   # cap width so day-matrix sheets stay compact
                if self.report_tbl.columnWidth(i) > 200: self.report_tbl.setColumnWidth(i, 200)
        self.ui(show)

    def _on_sheet_change(self, idx):
        if not self._current_snap_bytes: return
        threading.Thread(target=lambda: self._render_sheet(self._current_snap_bytes, idx),
                         daemon=True).start()

    def _save_excel(self):
        if not self._current_snap_bytes:
            QMessageBox.warning(self, 'No Report', 'Generate a report first.')
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Save Excel',
            f'Absensi_CVRAJ_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx', 'Excel (*.xlsx)')
        if path:
            with open(path, 'wb') as f: f.write(self._current_snap_bytes)
            self._log(f'✓ Saved to {path}')
            _open_path(os.path.dirname(path))

    # ── History ───────────────────────────────────────────────────────────────
    def _refresh_history(self):
        sess = db_get_pull_sessions()
        _fill_table(self.sess_tbl,
                    [(sid, pulled_at, f'{rec:,}', f'+{new_:,}', ip)
                     for sid, pulled_at, rec, new_, ip in sess])
        snaps = db_get_excel_snapshots()
        _fill_table(self.snap_tbl,
                    [(sid, label, created_at, f'{yr}/{mo:02d}' if mo else str(yr))
                     for sid, label, created_at, yr, mo, _, _ in snaps])

    def _sel_id(self, tbl):
        r = tbl.currentRow()
        return int(tbl.item(r, 0).text()) if r >= 0 else None

    def _load_session_to_preview(self):
        sid = self._sel_id(self.sess_tbl)
        if sid is None:
            QMessageBox.warning(self, 'Select', 'Select a pull session first.'); return
        conn = sqlite3.connect(DB_FILE); c = conn.cursor()
        c.execute('SELECT pulled_at FROM pull_sessions WHERE id=?', (sid,))
        row = c.fetchone(); conn.close()
        if not row: return
        pulled_at = row[0]
        conn = sqlite3.connect(DB_FILE); c = conn.cursor()
        c.execute('SELECT uid,nama,timestamp,punch FROM attendance WHERE pulled_at=? ORDER BY timestamp', (pulled_at,))
        att_rows = [{'uid': r[0], 'nama': r[1],
                     'timestamp': datetime.strptime(r[2], '%Y-%m-%d %H:%M:%S'), 'punch': r[3]}
                    for r in c.fetchall()]
        conn.close()
        if not att_rows:
            QMessageBox.information(self, 'Empty', 'No attendance records found for this session.'); return
        self._run(lambda: self._generate_and_show(att_rows, f'Session #{sid} ({pulled_at[:10]})', sid))

    def _generate_and_show(self, rows, label, session_id=None):
        self._log(f'Generating report for {label} ...')
        yr = int(self.tahun_cb.currentText())
        bln = self.bulan_cb.currentText()
        mo = (['All','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'].index(bln)
              if bln != 'All' else None)
        if mo: rows = [r for r in rows if r['timestamp'].month == mo and r['timestamp'].year == yr]
        data = generate_excel_bytes(rows, self.cfg)
        period = f'{bln} {yr}' if bln != 'All' else f'All {yr}'
        snap_id = db_save_excel_snapshot(session_id or 0, f'{label} — {period}', yr, mo, data)
        self._current_snap_id = snap_id
        self.ui(lambda: self._load_excel_to_viewer(data, f'{label} — {period}'))
        self.ui(self._refresh_history)
        self._log(f'✓ Report ready — {len(rows)} records | Saved to DB (snapshot #{snap_id})')

    def _load_snapshot(self):
        snap_id = self._sel_id(self.snap_tbl)
        if snap_id is None:
            QMessageBox.warning(self, 'Select', 'Select a report first.'); return
        row = db_load_excel_snapshot(snap_id)
        if not row: return
        data, label = row
        self._current_snap_id = snap_id
        self._load_excel_to_viewer(data, label)
        self.tabs.setCurrentIndex(1)
        self._log(f'✓ Loaded snapshot #{snap_id}: {label}')

    def _export_snapshot(self):
        snap_id = self._sel_id(self.snap_tbl)
        if snap_id is None:
            if self._current_snap_bytes: self._save_excel(); return
            QMessageBox.warning(self, 'Select', 'Select a report to export.'); return
        row = db_load_excel_snapshot(snap_id)
        if not row: return
        data, label = row
        safe = label.replace(' ', '_').replace('/', '_').replace(':', '')[:40]
        path, _ = QFileDialog.getSaveFileName(self, 'Export Excel', f'{safe}.xlsx', 'Excel (*.xlsx)')
        if path:
            with open(path, 'wb') as f: f.write(data)
            self._log(f'✓ Exported to {path}')
            _open_path(os.path.dirname(path))

    def _delete_session(self):
        sid = self._sel_id(self.sess_tbl)
        if sid is None: return
        if QMessageBox.question(self, 'Delete',
                f'Delete pull session #{sid}?\nAll saved reports for this session will also be deleted.') == QMessageBox.Yes:
            db_delete_pull_session(sid)
            self._refresh_history()
            self._log(f'✓ Session #{sid} deleted')

    def _delete_snapshot(self):
        snap_id = self._sel_id(self.snap_tbl)
        if snap_id is None: return
        if QMessageBox.question(self, 'Delete', f'Delete report snapshot #{snap_id}?') == QMessageBox.Yes:
            db_delete_excel_snapshot(snap_id)
            self._refresh_history()
            self._log(f'Snapshot #{snap_id} deleted')

    # ── Device actions ────────────────────────────────────────────────────────
    def _do_test(self):
        ip = self.ip_edit.text().strip()
        self._log(f'Testing connection to {ip}:{self.port_edit.text()} ...')
        conn = self._get_conn()
        try:
            fw = conn.get_firmware_version()
            self._log(f'✓ Connected! Firmware: {fw}')
            self.ui(lambda: self.conn_lbl.setStyleSheet('color:#16a34a; font-size:8pt;'))
            self.ui(lambda: self.conn_lbl.setText(f'● {ip}'))
            self._check_clock(conn)
            self._check_capacity(conn)
        finally: conn.disconnect()

    def _do_info(self):
        self._log('Fetching device info ...')
        conn = self._get_conn()
        try:
            conn.read_sizes()
            info = {'Serial Number': conn.get_serialnumber(), 'Firmware': conn.get_firmware_version(),
                    'Device Time': str(conn.get_time()),
                    'Users': f'{conn.users} / {conn.users_cap}',
                    'Logs': f'{conn.records:,} / {conn.rec_cap:,}',
                    'Local DB': f'{db_count():,} records'}
            self._log('✓ Device info received')
            self.ui(lambda: DeviceInfoDialog(self, info).exec())
        finally: conn.disconnect()

    def _do_users(self):
        self._log('Fetching users from device ...')
        conn = self._get_conn()
        try:
            users = conn.get_users()
            ulist = [{'uid': int(u.user_id), 'nama': u.name, 'card_id': getattr(u, 'card', '') or ''} for u in users]
            db_upsert_users(ulist)
            for u in ulist:
                if u['nama']: self.cfg['user_map'][str(u['uid'])] = u['nama']  # empty device name must not clobber config
            save_config(self.cfg)
            self._log(f'✓ {len(ulist)} users found and synced to config')
            self.ui(lambda: UserManagerDialog(self, ulist, app=self).exec())
        finally: conn.disconnect()

    def _device_user_op(self, op, dlg, done_msg):
        """Run op(conn) on the device, then re-fetch users into the dialog."""
        conn = self._get_conn()
        try:
            op(conn)
            users = conn.get_users()
            ulist = [{'uid': int(u.user_id), 'nama': u.name, 'card_id': getattr(u, 'card', '') or ''} for u in users]
            db_upsert_users(ulist)
            for u in ulist:
                if u['nama']: self.cfg['user_map'][str(u['uid'])] = u['nama']  # empty device name must not clobber config
            save_config(self.cfg)
            self._log(done_msg)
            self.ui(lambda: dlg._fill(ulist))
        finally: conn.disconnect()

    def device_user_save(self, uid, name, dlg):
        def op(conn):
            ex = next((u for u in conn.get_users() if str(u.user_id) == uid), None)
            if ex:  # rename, keep everything else (privilege, card, password)
                conn.set_user(uid=ex.uid, name=name, privilege=ex.privilege,
                              password=ex.password or '', group_id=ex.group_id or '',
                              user_id=uid, card=ex.card or 0)
            else:
                conn.set_user(name=name, user_id=uid)
        self._run(lambda: self._device_user_op(op, dlg, f'✓ User {uid} = {name} saved to device'))

    def device_user_delete(self, uid, dlg):
        self._run(lambda: self._device_user_op(
            lambda conn: conn.delete_user(user_id=uid), dlg, f'✓ User {uid} deleted from device'))

    def _confirm_restart(self):
        if QMessageBox.question(self, 'Restart',
                'Restart the device now?\nIt will be offline for ~1 minute.') == QMessageBox.Yes:
            self._run(self._do_restart)

    # ── Live monitor ──────────────────────────────────────────────────────────
    # Holds its own connection open; not routed through _run so the other
    # buttons stay usable while monitoring.
    def _toggle_live(self):
        if getattr(self, '_live_want', False):
            self._live_want = False
            c = getattr(self, '_live_conn', None)
            if c: c.end_live_capture = True   # loop in _live_loop exits
            return
        self._live_want = True
        threading.Thread(target=self._live_loop, daemon=True).start()

    def _live_loop(self):
        self.ui(lambda: self.btn_live.setText('⏹ Stop Live'))
        self._log('📡 Live monitor ON — punches appear here as they happen')
        while self._live_want:   # reconnect loop — survives device/network drops
            conn = None
            try:
                conn = self._get_conn()
                self._live_conn = conn
                self._check_clock(conn, quiet=True)   # power-loss guard on every (re)connect
                clock_at = time.time()
                um = {int(k): v for k, v in self.cfg.get('user_map', {}).items()}
                for att in conn.live_capture():
                    if att is None:   # idle timeout tick
                        if not self._live_want: break
                        if time.time() - clock_at > 600:   # ponytail: re-sync via reconnect cycle
                            conn.end_live_capture = True; break
                        continue
                    name = um.get(int(att.user_id), f'UID:{att.user_id}')
                    hhmm = att.timestamp.strftime('%H:%M') if att.timestamp else '?'
                    self._log(f'👆 {name} — {att.timestamp}')
                    self.ui(lambda n=name, h=hhmm: self._toast(f'👆 {n} absen — {h}'))
                    # save immediately; UNIQUE timestamp makes later pulls dedup for free
                    db_insert_attendance([{'uid': int(att.user_id), 'nama': name,
                                           'timestamp': att.timestamp, 'punch': att.punch}])
                    self.ui(self._update_status)
                    self.ui(self._refresh_today)
            except Exception as e:
                if self._live_want: self._log(f'⚠ Live monitor: {e} — reconnecting in 30s')
            finally:
                self._live_conn = None
                try:
                    if conn: conn.disconnect()
                except Exception: pass
            for _ in range(30):   # wait, but stay responsive to Stop
                if not self._live_want: break
                time.sleep(1)
        self.ui(lambda: self.btn_live.setText('📡 Live Monitor'))
        self._log('📡 Live monitor OFF')

    def _check_capacity(self, conn):
        """Warn when the device log memory is nearly full."""
        try:
            conn.read_sizes()
            if conn.rec_cap and conn.records / conn.rec_cap >= 0.8:
                pct = round(conn.records / conn.rec_cap * 100)
                self._log(f'⚠ Device log {pct}% full ({conn.records:,}/{conn.rec_cap:,})')
                self.ui(lambda: QMessageBox.warning(self, 'Log Hampir Penuh',
                    f'Memori log mesin {pct}% penuh ({conn.records:,} dari {conn.rec_cap:,}).\n\n'
                    f'Pull Data dulu, lalu jalankan "Clear Device Log" — kalau penuh, '
                    f'absensi baru tidak akan tersimpan.'))
        except Exception as e:
            self._log(f'  (capacity check skipped: {e})')

    def _do_restart(self):
        self._log('Restarting device ...')
        conn = self._get_conn()
        conn.restart()   # device drops the link; no disconnect() after this
        self._log('✓ Restart command sent — device back in ~1 minute')

    def _do_pull(self):
        self._log('Pulling attendance data from device ...')
        conn = self._get_conn()
        try:
            self._check_clock(conn)
            self._check_capacity(conn)
            atts = conn.get_attendance()
            if not atts: self._log('⚠ No data on device.'); return
            um = {int(k): v for k, v in self.cfg.get('user_map', {}).items()}
            recs = [{'uid': int(a.user_id), 'nama': um.get(int(a.user_id), f'UID:{a.user_id}'),
                     'timestamp': a.timestamp, 'punch': a.punch} for a in atts if a.timestamp]
            anomaly = [r for r in recs if is_anomaly_ts(r['timestamp'])]
            normal  = [r for r in recs if not is_anomaly_ts(r['timestamp'])]
            if anomaly and self.cfg.get('anomaly_recover', True):
                cfg_anchor = str(self.cfg.get('anomaly_anchor', '') or '').strip()
                anchor = None
                if cfg_anchor:
                    try: anchor = datetime.strptime(cfg_anchor, '%Y-%m-%d').date()
                    except Exception: anchor = None
                if anchor is None: anchor = find_gap_start(normal)
                remapped = remap_anomalies(anomaly, anchor,
                                           self.cfg.get('jam_masuk', '08:00'),
                                           self.cfg.get('jam_keluar', '16:00'))
                n_days = len(set(r['timestamp'].date() for r in anomaly))
                last = anchor + timedelta(days=max(0, n_days - 1))
                self._log(f'⚠ {len(anomaly)} ANOMALY records detected (clock reset to year {ANOMALY_YEAR}).')
                self._log(f'  → recovered onto {anchor.strftime("%d %b")} .. {last.strftime("%d %b %Y")} '
                          f'({len(remapped)} punches)')
                self._backup_anomaly_csv(remapped)
                self.ui(lambda: QMessageBox.warning(self, 'Anomaly Recovered',
                    f'{len(anomaly)} record dengan jam ter-reset (tahun {ANOMALY_YEAR}) ditemukan '
                    f'dan DIPULIHKAN.\n\n'
                    f'Dipetakan ke tanggal:\n{anchor.strftime("%d %b %Y")}  s/d  {last.strftime("%d %b %Y")}\n\n'
                    f'Penyebab: mesin tanpa baterai RTC. Pastikan UPS tidak habis.\n\n'
                    f'Backup mentah (jam asli vs hasil remap) disimpan di folder aplikasi.'))
                self._cache = normal + remapped
            else:
                if anomaly:
                    self._log(f'⚠ {len(anomaly)} anomaly records ignored (recovery disabled in Settings).')
                self._cache = normal
            new = db_insert_attendance(self._cache)
            sid = db_add_pull_session(len(self._cache), new, self.ip_edit.text().strip())
            self._log(f'✓ {len(atts)} records pulled  |  {new} new saved to database')
            self._log(f'  Pull session #{sid} recorded')
            if self.cfg.get('auto_backup', False):
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                path = os.path.join(_BASE, f'backup_raw_{ts}.csv')
                with open(path, 'w', newline='', encoding='utf-8') as f:
                    w = csv.writer(f); w.writerow(['UserID','Name','Timestamp','Status','Recovered'])
                    for r in self._cache:
                        w.writerow([r['uid'], r['nama'],
                                    r['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if r['timestamp'] else '',
                                    'Check-In' if r['punch'] == 0 else 'Check-Out',
                                    'Y' if r.get('recovered') else ''])
                self._log(f'  Backup CSV → {path}')
            self.ui(self._refresh_history)
            self.ui(self._refresh_today)
        finally: conn.disconnect()

    def _check_clock(self, conn, quiet=False):
        """Auto-sync device RTC to the PC clock when it has drifted."""
        try:
            dev = conn.get_time(); pc = datetime.now()
            skew = abs((dev - pc).total_seconds())
            if skew > 120:
                mins = int(skew // 60)
                conn.set_time(datetime.now())
                self._log(f'⚠ Device clock was off by ~{mins} min (was {dev}) — auto-synced to PC time')
            elif not quiet:
                self._log(f'✓ Device clock OK ({dev})')
            return skew
        except Exception as e:
            if not quiet: self._log(f'  (clock check skipped: {e})')
            return None

    def _backup_anomaly_csv(self, remapped):
        """Save original (corrupted) vs remapped timestamps for audit."""
        try:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            path = os.path.join(_BASE, f'anomaly_recovered_{ts}.csv')
            with open(path, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f); w.writerow(['UID','Name','OriginalTimestamp','RemappedTimestamp','Status'])
                for r in sorted(remapped, key=lambda x: x['timestamp']):
                    w.writerow([r['uid'], r['nama'],
                                r['orig_ts'].strftime('%Y-%m-%d %H:%M:%S'),
                                r['timestamp'].strftime('%Y-%m-%d %H:%M:%S'),
                                'Check-In' if r['punch'] == 0 else 'Check-Out'])
            self._log(f'  Raw anomaly backup → {path}')
        except Exception as e:
            self._log(f'  (anomaly backup failed: {e})')

    def _do_clear(self):
        self._log('Clearing log from device memory ...')
        conn = self._get_conn()
        try:
            conn.clear_attendance()
            self._log('✓ Device attendance log cleared')
        finally: conn.disconnect()

    def _do_report(self):
        yr = int(self.tahun_cb.currentText())
        bln = self.bulan_cb.currentText()
        src = 'cache' if self.src_cache.isChecked() else 'database'
        _month_list = ['All','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
        mo = _month_list.index(bln) if bln != 'All' else None
        if src == 'cache':
            if not self._cache: raise RuntimeError('No cache. Pull data first.')
            rows = self._cache
        else:
            rows = db_query_attendance(yr, mo)
            if not rows: raise RuntimeError(f'No data in database for {bln} {yr}.')
        label = f"{'DB' if src == 'database' else 'Cache'} {bln} {yr}"
        self._generate_and_show(rows, label)
        self.ui(lambda: self.tabs.setCurrentIndex(1))

    def _do_all(self):
        self._do_pull()   # clock auto-syncs inside pull
        self._do_report()

    # ── Updater ───────────────────────────────────────────────────────────────
    def _check_update(self):
        try:
            from updater import get_latest_release, is_newer
        except ImportError:
            QMessageBox.information(self, 'Update', 'Updater module not found.'); return
        self._log('Checking for updates on GitHub...')
        def worker():
            info = get_latest_release()
            if info is None:
                self._log('Cannot reach GitHub. Check internet.')
                return
            latest = info['version']; dl_url = info['download_url']; body = info.get('body', '') or ''
            if not is_newer(latest, APP_VERSION):
                self._log(f'Already up to date (v{APP_VERSION})')
                self.ui(lambda: QMessageBox.information(self, 'Up to Date',
                    f'You have the latest version.\nCurrent: v{APP_VERSION}'))
                return
            self.ui(lambda: self._prompt_update(latest, dl_url, body))
        threading.Thread(target=worker, daemon=True).start()

    def _prompt_update(self, version, dl_url, body):
        changelog = body[:500] if body else '(no changelog)'
        msg = (f'New version available: {version}\n'
               f'Current: v{APP_VERSION}\n\n'
               f'Changelog:\n{changelog}\n\n'
               f'Download and install now?\n'
               f'App will restart automatically.')
        if QMessageBox.question(self, 'Update Available', msg) == QMessageBox.Yes:
            self._do_download(dl_url, version)

    def _do_download(self, url, version):
        try:
            from updater import download_and_replace
        except ImportError:
            QMessageBox.critical(self, 'Error', 'Updater module not found.'); return
        self._log(f'Starting download of v{version} ...')
        dlg = QDialog(self)
        dlg.setWindowTitle(f'Updating to {version}'); dlg.setModal(True)
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowCloseButtonHint)
        v = QVBoxLayout(dlg)
        t1 = QLabel('ZKTeco eFace10 Utility'); t1.setStyleSheet('font-size:11pt; font-weight:700;')
        t2 = QLabel(f'Updating to {version}'); t2.setStyleSheet('color:#555;')
        t3 = QLabel('Do not close this window.'); t3.setStyleSheet('color:#888; font-size:8pt;')
        v.addWidget(t1, alignment=Qt.AlignCenter); v.addWidget(t2, alignment=Qt.AlignCenter)
        v.addWidget(t3, alignment=Qt.AlignCenter)
        pbar = QProgressBar(); pbar.setRange(0, 100); pbar.setFixedWidth(360); v.addWidget(pbar)
        pct_lbl = QLabel('0%'); pct_lbl.setStyleSheet(f'color:{ACCENT}; font-weight:700;')
        v.addWidget(pct_lbl, alignment=Qt.AlignCenter)
        step_lbl = QLabel('Connecting to GitHub...'); step_lbl.setStyleSheet('color:#555; font-size:8pt;')
        v.addWidget(step_lbl, alignment=Qt.AlignCenter)
        dlg.show()

        def on_progress(pct):
            self.ui(lambda: (pbar.setValue(pct), pct_lbl.setText(f'{pct}%')))
        def on_status(msg):
            self.ui(lambda: step_lbl.setText(msg))
            self._log(f'  {msg}')
        def on_done():
            self.ui(lambda: (pbar.setValue(100), pct_lbl.setText('100%'),
                             step_lbl.setText('Done! Restarting in 2 seconds...')))
            self._log(f'Update {version} installed successfully')
            self.ui(lambda: QTimer.singleShot(2000, lambda: self._finish_update(dlg)))
        def on_error(msg):
            self.ui(dlg.close)
            self._log(f'[ERROR] Update failed: {msg}')
            self.ui(lambda: QMessageBox.critical(self, 'Update Failed',
                f'Failed to install update:\n\n{msg}\n\n'
                f'Download manually:\ngithub.com/xbanana29/zkteco-utility/releases'))

        download_and_replace(url,
            on_progress=on_progress,
            on_status=on_status,
            on_done=on_done,
            on_error=on_error)

    def _finish_update(self, dlg):
        try: dlg.close()
        except Exception: pass
        try:
            from updater import restart_app
            restart_app()
        except Exception:
            QApplication.quit()


def main():
    qapp = QApplication(sys.argv)   # theme stylesheet applied by App._apply_theme
    qapp.setQuitOnLastWindowClosed(False)   # closing to tray must not quit
    win = App()
    if '--minimized' in sys.argv and win._tray:
        pass   # start hidden in tray
    else:
        win.show()
    sys.exit(qapp.exec())


if __name__ == '__main__':
    main()
