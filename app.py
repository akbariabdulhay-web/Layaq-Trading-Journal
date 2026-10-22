"""Layaq Trading Journal Super App — local-first Windows trading journal."""
import csv
import hashlib
import hmac
import json
import secrets
import datetime as dt
import os
import shutil
import sqlite3
import sys
import uuid
import zipfile
from pathlib import Path
from contextlib import contextmanager, closing
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

APP_NAME = 'Layaq Trading Journal Super App'
BASE = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'LayaqTradingJournal'
IMAGES = BASE / 'images'
DB = BASE / 'journal.sqlite3'
BACKUPS = BASE / 'backups'
AUTH = BASE / 'access.json'
BASE.mkdir(parents=True, exist_ok=True)
IMAGES.mkdir(exist_ok=True)
BACKUPS.mkdir(exist_ok=True)

BG, PANEL, ENTRY, TEXT, MUTED, ACCENT, GREEN, RED = '#101622', '#1c2534', '#263248', '#eef3fb', '#9aabc1', '#4aa3ff', '#42ce9b', '#ff687c'
IMAGE_FIELDS = [f'{stage.lower()}_{tf.lower()}' for stage in ('Before', 'After') for tf in ('HTF', 'MTF', 'LTF')]
COLUMNS = ['date', 'symbol', 'market', 'side', 'session', 'strategy', 'entry', 'exit', 'stop', 'target', 'size', 'risk', 'fees', 'pnl', 'pnl_high', 'pnl_low', 'emotion', 'setup', 'review']


@contextmanager
def connect():
    """Commit or roll back and ALWAYS close; SQLite's own with does not close."""
    cn = sqlite3.connect(DB, timeout=20)
    try:
        cn.row_factory = sqlite3.Row
        cn.execute('PRAGMA foreign_keys=ON')
        cn.execute('PRAGMA journal_mode=WAL')
        yield cn
        cn.commit()
    except Exception:
        cn.rollback()
        raise
    finally:
        cn.close()


def setup():
    with connect() as cn:
        cn.execute('''CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, symbol TEXT NOT NULL,
            market TEXT, side TEXT, session TEXT, strategy TEXT, entry REAL,
            exit REAL, stop REAL, target REAL, size REAL, risk REAL, fees REAL,
            pnl REAL, pnl_high REAL, pnl_low REAL, emotion TEXT, setup TEXT, review TEXT, created_at TEXT NOT NULL)''')
        cn.execute('''CREATE TABLE IF NOT EXISTS trade_images (
            trade_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
            slot TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY(trade_id, slot))''')
        existing = {r[1] for r in cn.execute('PRAGMA table_info(trades)')}
        for name in ('pnl_high', 'pnl_low'):
            if name not in existing: cn.execute(f'ALTER TABLE trades ADD COLUMN {name} REAL')
        cn.execute('CREATE INDEX IF NOT EXISTS ix_trades_date ON trades(date)')
        cn.execute('CREATE INDEX IF NOT EXISTS ix_trades_symbol ON trades(symbol)')



def password_digest(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), 310000).hex()


def auth_record():
    if not AUTH.exists():
        return None
    return json.loads(AUTH.read_text(encoding='utf-8'))


def check_secret(secret, record, prefix):
    salt, digest = record.get(prefix + '_salt'), record.get(prefix + '_digest')
    return bool(salt and digest and hmac.compare_digest(password_digest(secret, salt), digest))


def write_auth(record):
    tmp = AUTH.with_suffix('.tmp')
    tmp.write_text(json.dumps(record, indent=2), encoding='utf-8')
    os.replace(tmp, AUTH)


def set_secret(record, secret, prefix):
    salt = secrets.token_hex(16)
    record[prefix + '_salt'] = salt
    record[prefix + '_digest'] = password_digest(secret, salt)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry('1380x840')
        self.minsize(900, 620)
        self.configure(bg=BG)
        self._style()
        self._toast_job = None
        self._toast_frame = None
        self._pulse_job = None
        self._show_access_screen()

    def _show_access_screen(self):
        self.auth_frame = tk.Frame(self, bg=BG)
        self.auth_frame.pack(fill='both', expand=True)
        pane = tk.Frame(self.auth_frame, bg=PANEL, padx=30, pady=26)
        pane.place(relx=.5, rely=.5, anchor='center')
        tk.Label(pane, text='LAYAQ  /  TRADING JOURNAL', bg=PANEL, fg=TEXT,
                 font=('Segoe UI Semibold', 19)).pack(pady=(0, 12))
        self.login_feedback = tk.StringVar(value='')
        self.login_password = tk.StringVar()
        self.login_confirm = tk.StringVar()
        self.login_master = tk.StringVar()
        try:
            record = auth_record()
        except (OSError, ValueError) as exc:
            tk.Label(pane,text=f'Cannot read security file: {exc}',fg=RED,bg=PANEL).pack()
            return
        if record is None:
            tk.Label(pane,text='FIRST-TIME SECURITY SETUP',fg=ACCENT,bg=PANEL).pack(pady=6)
            tk.Label(pane,text='Choose your login password (at least 8 characters).',fg=TEXT,bg=PANEL).pack()
            self._secret_entry(pane, 'Password', self.login_password)
            self._secret_entry(pane, 'Confirm password', self.login_confirm)
            ttk.Button(pane,text='Create security keys',style='Accent.TButton',command=self._create_credentials).pack(fill='x',pady=(12,4))
        elif not record.get('enabled',True):
            self.auth_frame.destroy()
            self._open_main()
            return
        else:
            tk.Label(pane,text='Unlock using your password or master key',fg=MUTED,bg=PANEL).pack(pady=6)
            self._secret_entry(pane, 'Password / Master key', self.login_password)
            ttk.Button(pane,text='Unlock',style='Accent.TButton',command=self._unlock).pack(fill='x',pady=9)
            self.bind('<Return>',lambda _e:self._unlock())
        tk.Label(pane,textvariable=self.login_feedback,fg=RED,bg=PANEL,wraplength=390).pack(pady=6)

    def _secret_entry(self,parent,label,variable):
        tk.Label(parent,text=label,fg=TEXT,bg=PANEL).pack(anchor='w',pady=(7,2))
        entry=ttk.Entry(parent,textvariable=variable,show='*',width=42)
        entry.pack(fill='x')
        return entry

    def _create_credentials(self):
        password=self.login_password.get()
        if len(password)<8 or password!=self.login_confirm.get():
            self.login_feedback.set('Password must have 8+ characters, and both entries must match.')
            return
        master=secrets.token_hex(16).upper()
        record={'enabled': True}
        set_secret(record,password,'password')
        set_secret(record,master,'master')
        try:write_auth(record)
        except OSError as exc:
            self.login_feedback.set(str(exc));return
        self.auth_frame.destroy()
        self._show_master_once(master)

    def _show_master_once(self,master):
        self.master_frame=tk.Frame(self,bg=BG)
        self.master_frame.pack(fill='both',expand=True)
        pane=tk.Frame(self.master_frame,bg=PANEL,padx=30,pady=30)
        pane.place(relx=.5,rely=.5,anchor='center')
        tk.Label(pane,text='SAVE YOUR MASTER RECOVERY KEY',bg=PANEL,fg=ACCENT,
                 font=('Segoe UI Semibold',19)).pack(pady=8)
        tk.Label(pane,text='This key can unlock the app and reset your forgotten password.\nIt is shown only once. Store it in a safe place OFF this PC.',
                 bg=PANEL,fg=TEXT,justify='center').pack(pady=9)
        e=ttk.Entry(pane,width=42,font=('Consolas',15))
        e.insert(0,master);e.configure(state='readonly');e.pack(pady=12)
        acknowledged=tk.BooleanVar(value=False)
        ttk.Checkbutton(pane,text='I have safely copied my master key',variable=acknowledged).pack(pady=8)
        def proceed():
            if not acknowledged.get():
                messagebox.showwarning('Master key','Save and acknowledge your recovery key before continuing.',parent=self)
                return
            self.master_frame.destroy();self._open_main()
        ttk.Button(pane,text='Continue to journal',command=proceed,style='Accent.TButton').pack(pady=8)

    def _unlock(self):
        key=self.login_password.get()
        try:record=auth_record()
        except (OSError,ValueError) as exc:
            self.login_feedback.set(str(exc));return
        if record and (check_secret(key,record,'password') or check_secret(key,record,'master')):
            self.unbind('<Return>')
            self.auth_frame.destroy()
            self._open_main()
        else:
            self.login_feedback.set('Incorrect password or recovery key.')
            self.login_password.set('')

    def _open_main(self):
        setup()
        self.trade_id = None
        self.pending_images = {}
        self.active_preview = None
        self._layout()
        self.refresh()

    def _verify_admin(self):
        key=simpledialog.askstring('Security verification','Enter your password or master recovery key:',show='*',parent=self)
        if key is None:return False
        try:rec=auth_record()
        except (OSError,ValueError):rec=None
        if rec and (check_secret(key,rec,'password') or check_secret(key,rec,'master')):return True
        messagebox.showerror('Access denied','Incorrect password / recovery key.',parent=self)
        return False

    def _change_password(self):
        if not self._verify_admin():return
        pw=simpledialog.askstring('Change password','New password (at least 8 characters):',show='*',parent=self)
        if pw is None:return
        if len(pw)<8:
            messagebox.showwarning('Invalid','Use at least 8 characters.',parent=self);return
        again=simpledialog.askstring('Confirm','Repeat new password:',show='*',parent=self)
        if again!=pw:
            messagebox.showwarning('Mismatch','Passwords do not match.',parent=self);return
        rec=auth_record()
        set_secret(rec,pw,'password');write_auth(rec)
        messagebox.showinfo('Updated','Login password changed. Your master key still works.',parent=self)

    def _toggle_password(self):
        if not self._verify_admin():return
        rec=auth_record(); rec['enabled']=not rec.get('enabled',True);write_auth(rec)
        self._refresh_security_label()
        messagebox.showinfo('Login settings', 'Password prompt is now '+('ON' if rec['enabled'] else 'OFF')+'. Changes apply on next launch.',parent=self)

    def _refresh_security_label(self):
        rec=auth_record()
        self.security_status.set('Login password: '+('ON' if rec.get('enabled',True) else 'OFF'))

    def _make_settings(self):
        box=tk.Frame(self.settings_tab,bg=PANEL,padx=24,pady=24)
        box.pack(anchor='nw',fill='x',padx=25,pady=25)
        ttk.Label(box,text='Security settings',font=('Segoe UI Semibold',19)).pack(anchor='w',pady=(0,10))
        self.security_status=tk.StringVar()
        ttk.Label(box,textvariable=self.security_status).pack(anchor='w',pady=8)
        ttk.Button(box,text='Change login password',command=self._change_password).pack(anchor='w',pady=8)
        ttk.Button(box,text='Turn login ON / OFF',command=self._toggle_password).pack(anchor='w',pady=8)
        ttk.Label(box,text='To recover a forgotten password, use your master key at login, then change it here.\n'
                        'Your master key is only shown at first setup. It is not stored in readable form.\n'
                        'This is an application lock, not full database encryption.',
                  style='Muted.TLabel').pack(anchor='w',pady=15)
        self._refresh_security_label()

    def _style(self):
        s = ttk.Style(self)
        s.theme_use('clam')
        s.configure('.', font=('Segoe UI', 10), background=BG, foreground=TEXT)
        s.configure('TFrame', background=BG)
        s.configure('Panel.TFrame', background=PANEL)
        s.configure('TLabel', background=BG, foreground=TEXT)
        s.configure('Muted.TLabel', background=BG, foreground=MUTED)
        s.configure('TButton', background=ENTRY, foreground=TEXT, padding=(10, 7), borderwidth=0)
        s.map('TButton', background=[('active', '#344766')])
        s.configure('Accent.TButton', background=ACCENT, foreground='#071426')
        s.configure('TEntry', fieldbackground=ENTRY, foreground=TEXT, insertcolor=TEXT, padding=6)
        s.configure('TCombobox', fieldbackground=ENTRY, background=ENTRY, foreground=TEXT, padding=5)
        s.map('TCombobox', fieldbackground=[('readonly', ENTRY)], foreground=[('readonly', TEXT)])
        s.configure('Treeview', background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=30, borderwidth=0)
        s.map('Treeview', background=[('selected', '#245483')], foreground=[('selected', 'white')])
        s.configure('Treeview.Heading', background=ENTRY, foreground=TEXT, padding=8)
        s.configure('TNotebook', background=BG, borderwidth=0)
        s.configure('TNotebook.Tab', padding=(16, 9), background=ENTRY, foreground=TEXT)
        s.map('TNotebook.Tab', background=[('selected', PANEL)])

    def _layout(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill='x', padx=22, pady=(16, 6))
        tk.Label(header, text='LAYAQ  /  TRADING JOURNAL', font=('Segoe UI Semibold', 23), bg=BG, fg=TEXT).pack(side='left')
        tk.Label(header, text='SUPER APP   •   OFFLINE', font=('Segoe UI', 10), bg=BG, fg=GREEN).pack(side='left', padx=18)
        ttk.Button(header, text='Save Trade', style='Accent.TButton', command=self.save_trade).pack(side='right', padx=4)
        ttk.Button(header, text='+ New Trade', style='Accent.TButton', command=self.new_trade).pack(side='right', padx=4)
        ttk.Button(header, text='Backup', command=self.backup).pack(side='right', padx=4)
        ttk.Button(header, text='Restore', command=self.restore).pack(side='right', padx=4)
        ttk.Button(header, text='Export CSV', command=self.export_csv).pack(side='right', padx=4)
        self.activity_line = tk.Canvas(self, height=3, bg=BG, highlightthickness=0)
        self.activity_line.pack(fill='x', padx=22)
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True, padx=20, pady=10)
        self.dashboard = ttk.Frame(self.tabs)
        self.history = ttk.Frame(self.tabs)
        self.editor = ttk.Frame(self.tabs)
        self.settings_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.dashboard, text='Dashboard')
        self.tabs.add(self.history, text='Trade History')
        self.tabs.add(self.editor, text='Trade Editor & Charts')
        self.tabs.add(self.settings_tab, text='Settings')
        self._make_dashboard()
        self._make_history()
        self._make_editor()
        self._make_settings()

    def notify(self, message, kind='success', duration=3400):
        """Non-modal notification inside the application; never opens a second window."""
        if self._toast_job is not None:
            self.after_cancel(self._toast_job)
            self._toast_job = None
        if self._toast_frame is not None:
            self._toast_frame.destroy()
        color = GREEN if kind == 'success' else (RED if kind == 'error' else ACCENT)
        frame = tk.Frame(self, bg=PANEL, highlightbackground=color, highlightthickness=1)
        self._toast_frame = frame
        symbol = '✓' if kind == 'success' else ('!' if kind == 'error' else 'i')
        tk.Label(frame, text=symbol, bg=PANEL, fg=color,
                 font=('Segoe UI Semibold', 17)).pack(side='left', padx=(15, 8), pady=10)
        tk.Label(frame, text=message, bg=PANEL, fg=TEXT,
                 font=('Segoe UI', 11), wraplength=480, justify='left').pack(side='left', padx=(0, 20), pady=12)
        ttk.Button(frame, text='×', command=self._hide_notification).pack(side='right', padx=8)
        frame.place(relx=.5, rely=0, y=-90, anchor='n')
        # A short, discreet downward slide adds motion without distracting animations.
        def slide(step=0):
            if self._toast_frame is not frame or not frame.winfo_exists():
                return
            target = 18
            y = round(-90 + (target + 90) * (1 - (1 - step/12)**3))
            frame.place_configure(y=y)
            if step < 12:
                self.after(16, lambda: slide(step + 1))
        slide()
        self._toast_job = self.after(duration, self._hide_notification)
        self._animate_activity(color)

    def _hide_notification(self):
        self._toast_job = None
        if self._toast_frame is not None:
            self._toast_frame.destroy()
            self._toast_frame = None

    def _animate_activity(self, color):
        """Short moving accent marker along the header after an action."""
        if not hasattr(self, 'activity_line') or not self.activity_line.winfo_exists():
            return
        if self._pulse_job is not None:
            self.after_cancel(self._pulse_job)
        canvas = self.activity_line
        canvas.delete('all')
        def tick(i=0):
            if not canvas.winfo_exists():
                return
            width = max(1, canvas.winfo_width())
            canvas.delete('all')
            if i >= 28:
                return
            x = max(0, (i / 28) * (width + 120) - 120)
            canvas.create_rectangle(x, 0, min(width, x + 120), 3, fill=color, outline='')
            self._pulse_job = self.after(18, lambda: tick(i+1))
        tick()

    def _make_dashboard(self):
        self.stats_frame = ttk.Frame(self.dashboard)
        self.stats_frame.pack(fill='x', pady=(12, 12))
        self.stats = {}
        for name in ('Total Trades', 'Win Rate', 'Net P&L', 'Profit Factor', 'Avg Win / Loss', 'Max Drawdown'):
            box = tk.Frame(self.stats_frame, bg=PANEL, padx=13, pady=12)
            box.pack(side='left', fill='both', expand=True, padx=5)
            tk.Label(box, text=name, bg=PANEL, fg=MUTED).pack(anchor='w')
            value = tk.Label(box, text='—', font=('Segoe UI Semibold', 16), bg=PANEL, fg=TEXT)
            value.pack(anchor='w', pady=(7, 0))
            self.stats[name] = value
        controls = ttk.Frame(self.dashboard)
        controls.pack(fill='x', pady=(0, 8))
        self.period_tabs = ttk.Notebook(self.dashboard)
        self.period_tabs.pack(fill='both', expand=True)
        self.period_canvases = {}
        self.date_vars = {}
        today = dt.date.today()
        for period in ('Daily', 'Weekly', 'Monthly', 'Yearly', 'All Time'):
            frame = ttk.Frame(self.period_tabs)
            self.period_tabs.add(frame, text=period)
            row = ttk.Frame(frame)
            row.pack(fill='x', padx=6, pady=8)
            if period != 'All Time':
                ttk.Label(row, text='Year:').pack(side='left', padx=4)
                year = tk.StringVar(value=str(today.year))
                self.date_vars[(period, 'year')] = year
                years = list(range(today.year-15, today.year+6))
                ttk.Combobox(row, textvariable=year, values=years, width=7, state='readonly').pack(side='left', padx=4)
                year.trace_add('write', lambda *_a, p=period: self.draw_period(p))
            if period == 'Daily':
                ttk.Label(row, text='Month:').pack(side='left', padx=4)
                month = tk.StringVar(value=f'{today.month:02d}')
                self.date_vars[(period, 'month')] = month
                ttk.Combobox(row, textvariable=month, values=[f'{m:02d}' for m in range(1,13)], width=5, state='readonly').pack(side='left', padx=4)
                month.trace_add('write', lambda *_a, p=period: self.draw_period(p))
                ttk.Label(row, text='Day:').pack(side='left', padx=4)
                day = tk.StringVar(value=f'{today.day:02d}')
                self.date_vars[(period, 'day')] = day
                ttk.Combobox(row, textvariable=day, values=[f'{d:02d}' for d in range(1,32)], width=5, state='readonly').pack(side='left', padx=4)
                day.trace_add('write', lambda *_a, p=period: self.draw_period(p))
            elif period == 'Weekly':
                ttk.Label(row, text='ISO Week:').pack(side='left', padx=4)
                week = tk.StringVar(value=f'{today.isocalendar().week:02d}')
                self.date_vars[(period, 'week')] = week
                ttk.Combobox(row, textvariable=week, values=[f'{i:02d}' for i in range(1,54)], width=5, state='readonly').pack(side='left', padx=4)
                week.trace_add('write', lambda *_a, p=period: self.draw_period(p))
            elif period == 'Monthly':
                ttk.Label(row, text='Month:').pack(side='left', padx=4)
                month = tk.StringVar(value=f'{today.month:02d}')
                self.date_vars[(period, 'month')] = month
                ttk.Combobox(row, textvariable=month, values=[f'{m:02d}' for m in range(1,13)], width=5, state='readonly').pack(side='left', padx=4)
                month.trace_add('write', lambda *_a, p=period: self.draw_period(p))
            ttk.Label(row, text='Chart:').pack(side='left', padx=(20,4))
            kind = tk.StringVar(value='Line')
            self.date_vars[(period, 'chart')] = kind
            ttk.Combobox(row, textvariable=kind, values=['Line', 'Candlestick', 'Area'], state='readonly', width=9).pack(side='left', padx=4)
            kind.trace_add('write', lambda *_a, p=period: self.draw_period(p))
            ttk.Label(row, text='Candle interval:').pack(side='left', padx=(14,4))
            interval = tk.StringVar(value='1 Hour')
            self.date_vars[(period, 'interval')] = interval
            ttk.Combobox(row, textvariable=interval,
                         values=['1 Minute', '5 Minutes', '15 Minutes', '30 Minutes', '1 Hour', '4 Hours', '1 Day', '1 Week', '1 Month', '1 Year'],
                         state='readonly', width=13).pack(side='left', padx=4)
            interval.trace_add('write', lambda *_a, p=period: self.draw_period(p))
            canvas = tk.Canvas(frame, bg=PANEL, highlightthickness=0)
            canvas.pack(fill='both', expand=True, padx=5, pady=(0, 5))
            self.period_canvases[period] = canvas
            canvas.bind('<Configure>', lambda _e, p=period: self.draw_period(p))
            canvas.bind('<Motion>', lambda e, p=period: self.chart_hover(p, e))
            canvas.bind('<Leave>', lambda _e, p=period: self.chart_unhover(p))
        self.period_tabs.bind('<<NotebookTabChanged>>', lambda _e: self.refresh())
        tk.Label(self.dashboard, text='Line & Area: each closed trade  |  Candles: interval OHLC from cumulative realized P&L  |  Hover for exact details. Inter-trade peaks are unknown.', bg=BG, fg=MUTED).pack(anchor='w', padx=6, pady=8)

    def period_data(self, period):
        selected=[]
        for row in self._all_trades():
            if row['pnl'] is None: continue
            try:
                stamp=dt.datetime.fromisoformat(row['date'].strip().replace('Z','+00:00'))
                day=stamp.date()
                if period != 'All Time':
                    year=int(self.date_vars[(period,'year')].get())
                    if period=='Daily' and (day.year,day.month,day.day)!=(year,int(self.date_vars[(period,'month')].get()),int(self.date_vars[(period,'day')].get())):continue
                    if period=='Weekly' and (day.isocalendar().year,day.isocalendar().week)!=(year,int(self.date_vars[(period,'week')].get())):continue
                    if period=='Monthly' and (day.year,day.month)!=(year,int(self.date_vars[(period,'month')].get())):continue
                    if period=='Yearly' and day.year!=year:continue
                selected.append(row)
            except (ValueError,TypeError,AttributeError):pass
        return selected, [float(r['pnl']) for r in selected]

    @staticmethod
    def _bucket_start(stamp, interval):
        if interval == '1 Year': return stamp.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        if interval == '1 Month': return stamp.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if interval == '1 Week':
            stamp = stamp - dt.timedelta(days=stamp.weekday())
            return stamp.replace(hour=0, minute=0, second=0, microsecond=0)
        if interval == '1 Day': return stamp.replace(hour=0, minute=0, second=0, microsecond=0)
        mins = {'1 Minute':1, '5 Minutes':5, '15 Minutes':15, '30 Minutes':30, '1 Hour':60, '4 Hours':240}[interval]
        minute_of_day = (stamp.hour * 60 + stamp.minute) // mins * mins
        return stamp.replace(hour=minute_of_day//60, minute=minute_of_day%60, second=0, microsecond=0)

    def _chart_points(self, period):
        # Start at the genuine all-time realized P&L BEFORE the filtered interval.
        # No fictional intratrade price path is inferred from final trade results.
        rows, _ = self.period_data(period)
        included = {r['id'] for r in rows}
        running = 0.0
        result = []
        for row in self._all_trades():
            if row['pnl'] is None: continue
            opening = running
            running += float(row['pnl'])
            if row['id'] not in included: continue
            try: stamp = dt.datetime.fromisoformat(row['date'].strip().replace('Z', '+00:00'))
            except (ValueError, TypeError, AttributeError): continue
            result.append({'id':row['id'], 'date':stamp, 'open':opening, 'close':running,
                           'pnl':float(row['pnl']), 'symbol':row['symbol']})
        return result

    def draw_period(self, period):
        if not hasattr(self, 'period_canvases') or period not in self.period_canvases:return
        c = self.period_canvases[period]
        c.delete('all')
        c._hover_data = []
        w, h = c.winfo_width(), c.winfo_height()
        if w < 100 or h < 100:return
        trades = self._chart_points(period)
        kind = self.date_vars[(period, 'chart')].get()
        interval = self.date_vars[(period, 'interval')].get()
        c.create_text(24, 22, text=f'{period.upper()} / {kind.upper()} / REALIZED EQUITY', anchor='w', fill=TEXT, font=('Segoe UI Semibold', 14))
        if not trades:
            c.create_text(w/2, h/2, text='No closed trades for this period', fill=MUTED, font=('Segoe UI', 13))
            return
        # Each candle represents a time bucket, not a trade. OHLC reflects
        # observable equity levels at bucket start and after each closed trade.
        buckets = []
        for trade in trades:
            stamp = self._bucket_start(trade['date'], interval)
            if not buckets or buckets[-1]['time'] != stamp:
                buckets.append({'time':stamp, 'open':trade['open'], 'high':max(trade['open'], trade['close']),
                                'low':min(trade['open'], trade['close']), 'close':trade['close'], 'count':1,
                                'ids':[trade['id']]})
            else:
                b = buckets[-1]
                b['high'] = max(b['high'], trade['close'])
                b['low'] = min(b['low'], trade['close'])
                b['close'] = trade['close']
                b['count'] += 1
                b['ids'].append(trade['id'])
        displayed = buckets if kind == 'Candlestick' else trades
        extrema = ([t['open'] for t in trades] + [t['close'] for t in trades]) if kind != 'Candlestick' else ([b['low'] for b in buckets] + [b['high'] for b in buckets])
        lo, hi = min(extrema), max(extrema)
        if lo == hi: lo -= 1; hi += 1
        pad = (hi-lo)*0.09
        lo -= pad; hi += pad
        left, right, top, bottom = 83, 36, 55, 75
        width = max(10, w-left-right)
        height = max(10, h-top-bottom)
        def xx(i):return left + (i+.5)*width/len(displayed)
        def yy(v):return top + (hi-v)*height/(hi-lo)
        for j in range(5):
            val=lo+(hi-lo)*j/4
            y=yy(val)
            c.create_line(left,y,w-right,y,fill='#344052',dash=(3,5))
            c.create_text(left-9,y,text=f'{val:+,.2f}',anchor='e',fill=MUTED)
        if lo <= 0 <= hi:
            c.create_line(left,yy(0),w-right,yy(0),fill='#667589',dash=(2,3))
        if kind == 'Candlestick':
            cw=max(2,min(30,width/max(1,len(buckets))*.65))
            for i,b in enumerate(buckets):
                x=xx(i)
                color = GREEN if b['close'] >= b['open'] else RED
                c.create_line(x, yy(b['high']), x, yy(b['low']), fill=color, width=2)
                y0,y1=yy(b['open']),yy(b['close'])
                c.create_rectangle(x-cw/2,min(y0,y1),x+cw/2,max(y0,y1)+1,fill=color,outline=color)
                label=b['time'].strftime('%Y-%m-%d %H:%M')
                hover=(f'{label}  |  {b["count"]} trades\n'
                       f'Open: {b["open"]:+,.2f}     High: {b["high"]:+,.2f}\n'
                       f'Low: {b["low"]:+,.2f}     Close: {b["close"]:+,.2f}\n'
                       f'Net: {b["close"]-b["open"]:+,.2f}')
                c._hover_data.append((x, hover, yy(b['close'])))
        else:
            points=[(left,yy(trades[0]['open']))]
            points += [(xx(i),yy(t['close'])) for i,t in enumerate(trades)]
            flat=[n for xy in points for n in xy]
            if kind=='Area':
                base=yy(0) if lo<=0<=hi else yy(lo)
                c.create_polygon([points[0][0],base]+flat+[points[-1][0],base],fill='#245483',outline='')
            c.create_line(*flat, fill=GREEN if trades[-1]['close']>=trades[0]['open'] else RED, width=2)
            for i,t in enumerate(trades):
                x,y=xx(i), yy(t['close'])
                color=GREEN if t['pnl']>=0 else RED
                c.create_oval(x-3,y-3,x+3,y+3,fill=color,outline='')
                hover=(f'Trade #{t["id"]}  |  {t["date"].strftime("%Y-%m-%d %H:%M")}\n'
                       f'{t["symbol"]}  |  Trade P&L: {t["pnl"]:+,.2f}\n'
                       f'Equity: {t["open"]:+,.2f} → {t["close"]:+,.2f}')
                c._hover_data.append((x,hover,y))
        stride=max(1,(len(displayed)+9)//10)
        for i,item in enumerate(displayed):
            if i%stride == 0 or i == len(displayed)-1:
                txt=item['time'].strftime('%m-%d %H:%M') if kind=='Candlestick' else f'#{item["id"]}'
                c.create_text(xx(i),h-46,text=txt,fill=MUTED,font=('Segoe UI',9))
        net=sum(t['pnl'] for t in trades)
        c.create_text(w-right,h-16,anchor='e',text=f'{len(trades)} trades  •  Period P&L: {net:+,.2f}  •  Hover over chart for values',fill=TEXT)

    def chart_unhover(self, period):
        if period in self.period_canvases:
            self.period_canvases[period].delete('chart_tooltip')

    def chart_hover(self, period, event):
        c=self.period_canvases.get(period)
        if not c:return
        c.delete('chart_tooltip')
        items=getattr(c,'_hover_data',[])
        if not items or event.x<75 or event.x>c.winfo_width()-25:return
        nearest=min(items,key=lambda item: abs(item[0]-event.x))
        # Show the nearest time bucket/trade, even in dense charts.
        x,msg,y=nearest
        lines=msg.split('\n')
        fontsize=10
        box_w=min(c.winfo_width()-20,max(250,max(len(s) for s in lines)*7+24))
        box_h=len(lines)*21+18
        tx=max(10,min(c.winfo_width()-box_w-10,x+13))
        ty=max(34,min(c.winfo_height()-box_h-10,event.y-22))
        c.create_line(x,55,x,c.winfo_height()-74,fill='#7085a2',dash=(3,3),tags='chart_tooltip')
        c.create_rectangle(tx,ty,tx+box_w,ty+box_h,fill='#111c2b',outline='#5f7797',tags='chart_tooltip')
        for i,line in enumerate(lines):
            c.create_text(tx+12,ty+16+i*21,text=line,anchor='w',fill=TEXT,font=('Segoe UI',fontsize),tags='chart_tooltip')

    def _make_history(self):
        bar = ttk.Frame(self.history)
        bar.pack(fill='x', pady=10)
        ttk.Label(bar, text='Search:').pack(side='left', padx=5)
        self.search_var = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.search_var, width=30)
        ent.pack(side='left', padx=5)
        ent.bind('<KeyRelease>', lambda e: self.load_history())
        ttk.Button(bar, text='+ New Trade', style='Accent.TButton', command=self.new_trade).pack(side='right', padx=5)
        ttk.Button(bar, text='Delete Selected', command=self.delete_selected).pack(side='right', padx=5)
        ttk.Button(bar, text='Edit Selected', command=self.edit_selected).pack(side='right', padx=5)
        cols = ('id','date','symbol','market','side','session','strategy','entry','exit','pnl','emotion')
        self.tree = ttk.Treeview(self.history, columns=cols, show='headings', selectmode='browse')
        sizes = dict(id=55, date=145, symbol=105, market=90, side=70, session=110, strategy=155, entry=95, exit=95, pnl=105, emotion=115)
        for col in cols:
            self.tree.heading(col, text=col.upper(), command=lambda c=col: self.sort_history(c))
            self.tree.column(col, width=sizes[col], minwidth=50)
        self.tree.tag_configure('win', foreground=GREEN)
        self.tree.tag_configure('loss', foreground=RED)
        sb = ttk.Scrollbar(self.history, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.tree.bind('<Double-1>', lambda e: self.edit_selected())
        self.sort_col, self.sort_reverse = 'id', True

    def _make_editor(self):
        wrap = ttk.Frame(self.editor)
        wrap.pack(fill='both', expand=True)
        left = ttk.Frame(wrap, width=420)
        left.pack(side='left', fill='y', padx=(0, 15), pady=5)
        left.pack_propagate(False)
        right = ttk.Frame(wrap)
        right.pack(side='left', fill='both', expand=True, pady=5)
        self.editor_title = ttk.Label(left, text='New Trade', font=('Segoe UI Semibold', 18))
        self.editor_title.pack(anchor='w', pady=(0, 8))
        fixed_actions=ttk.Frame(left)
        fixed_actions.pack(fill='x',pady=(0,8))
        ttk.Button(fixed_actions,text='Save Trade',style='Accent.TButton',command=self.save_trade).pack(side='left',padx=3)
        ttk.Button(fixed_actions,text='+ New Trade',command=self.new_trade).pack(side='left',padx=3)
        form = ttk.Frame(left)
        form.pack(fill='x')
        self.fields = {}
        config = [
            ('date', 'Date / time', 'entry'), ('symbol','Symbol','entry'), ('market','Market','combo'),
            ('side','Side','combo'), ('session','Session','combo'), ('strategy','Strategy','entry'),
            ('entry','Entry price','entry'), ('exit','Exit price','entry'), ('stop','Stop loss','entry'),
            ('target','Take profit','entry'), ('size','Position size / lots','entry'),
            ('risk','Risk amount','entry'), ('fees','Fees / commission','entry'),
            ('pnl','Realized net P&L','entry'),
            ('pnl_high','Trade peak P&L (optional)','entry'), ('pnl_low','Trade trough P&L (optional)','entry'), ('emotion','Emotion','combo')]
        choices = {'market':['Forex','Gold','Crypto','Stocks','Indices','Other'],
                   'side':['Buy','Sell'], 'session':['Asia','London','New York','Overlap','Other'],
                   'emotion':['Calm','Confident','Fear','Greed','FOMO','Revenge','Impatient','Other']}
        for i,(key,label,kind) in enumerate(config):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky='w', pady=4)
            v = tk.StringVar()
            w = ttk.Combobox(form, textvariable=v, values=choices[key], state='readonly', width=27) if kind=='combo' else ttk.Entry(form, textvariable=v, width=29)
            w.grid(row=i, column=1, sticky='ew', padx=8, pady=4)
            self.fields[key] = v
        form.columnconfigure(1, weight=1)
        ttk.Label(left, text='Setup / Entry rationale').pack(anchor='w', pady=(10, 3))
        self.setup_note = tk.Text(left, height=3, bg=ENTRY, fg=TEXT, insertbackground=TEXT, wrap='word', relief='flat')
        self.setup_note.pack(fill='x')
        ttk.Label(left, text='Post-trade review / lessons').pack(anchor='w', pady=(9, 3))
        self.review_note = tk.Text(left, height=3, bg=ENTRY, fg=TEXT, insertbackground=TEXT, wrap='word', relief='flat')
        self.review_note.pack(fill='x')
        ttk.Label(right, text='CHART EVIDENCE  •  BEFORE / AFTER', font=('Segoe UI Semibold', 17)).pack(anchor='w', pady=(0, 6))
        ttk.Label(right, text='Six chart slots per trade. Choose an image or paste a screenshot from the clipboard.', style='Muted.TLabel').pack(anchor='w', pady=(0, 12))
        grid = ttk.Frame(right)
        grid.pack(fill='both', expand=True)
        self.image_cards = {}
        for index, slot in enumerate(IMAGE_FIELDS):
            stage, tf = slot.split('_')
            card = tk.Frame(grid, bg=PANEL, padx=9, pady=9, highlightthickness=1, highlightbackground='#34445b')
            card.grid(row=index//3, column=index%3, sticky='nsew', padx=5, pady=5)
            tk.Label(card, text=f'{stage.upper()}  /  {tf.upper()}', font=('Segoe UI Semibold', 12), bg=PANEL, fg=ACCENT).pack(anchor='w')
            preview = tk.Label(card, text='No screenshot', bg=ENTRY, fg=MUTED, height=11, compound='center')
            preview.pack(fill='both', expand=True, pady=8)
            line = tk.Frame(card, bg=PANEL)
            line.pack(fill='x')
            ttk.Button(line, text='Choose', command=lambda s=slot:self.choose_image(s)).pack(side='left', padx=2)
            ttk.Button(line, text='Paste', command=lambda s=slot:self.paste_image(s)).pack(side='left', padx=2)
            ttk.Button(line, text='View', command=lambda s=slot:self.view_image(s)).pack(side='left', padx=2)
            ttk.Button(line, text='Clear', command=lambda s=slot:self.clear_image(s)).pack(side='left', padx=2)
            self.image_cards[slot] = preview
        for i in range(3):grid.columnconfigure(i, weight=1)
        for i in range(2):grid.rowconfigure(i, weight=1)
        self.new_trade()

    def _all_trades(self):
        with connect() as cn:
            return cn.execute('SELECT * FROM trades ORDER BY date, id').fetchall()

    def refresh(self):
        self.load_history()
        period = self.period_tabs.tab(self.period_tabs.select(), 'text') if hasattr(self,'period_tabs') else 'All Time'
        rows = self.period_data(period)[0] if hasattr(self,'date_vars') else [r for r in self._all_trades() if r['pnl'] is not None]
        pnls = [r['pnl'] for r in rows]
        wins = [x for x in pnls if x > 0]
        losses = [x for x in pnls if x < 0]
        grosswin = sum(wins)
        grossloss = abs(sum(losses))
        equity = peak = maxdd = 0.0
        for x in pnls:
            equity += x
            peak = max(peak, equity)
            maxdd = max(maxdd, peak - equity)
        stats = {
            'Total Trades': str(len(rows)),
            'Win Rate': f'{len(wins)/len(pnls)*100:.1f}%' if pnls else '—',
            'Net P&L': f'{sum(pnls):+,.2f}',
            'Profit Factor': f'{grosswin/grossloss:.2f}' if grossloss else ('∞' if grosswin else '—'),
            'Avg Win / Loss': f'{grosswin/len(wins):,.2f} / {grossloss/len(losses):,.2f}' if wins and losses else '—',
            'Max Drawdown': f'{maxdd:,.2f}' }
        for name, val in stats.items():self.stats[name].config(text=val)
        if hasattr(self,'period_canvases'):
            for p in self.period_canvases:self.draw_period(p)

    def load_history(self):
        if not hasattr(self,'tree'):return
        q=self.search_var.get().lower().strip()
        with connect() as cn:
            rows=cn.execute('SELECT * FROM trades ORDER BY id DESC').fetchall()
        rows=[r for r in rows if q in ' '.join(str(r[k] or '') for k in ('id','date','symbol','market','side','session','strategy','emotion')).lower()]
        rows=sorted(rows,key=lambda r: (r[self.sort_col] is not None, r[self.sort_col] if r[self.sort_col] is not None else ''),reverse=self.sort_reverse)
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            p=r['pnl']
            self.tree.insert('', 'end', iid=str(r['id']), values=[f'{r[k]:,.2f}' if k in ('entry','exit','pnl') and r[k] is not None else (r[k] if r[k] is not None else '') for k in self.tree['columns']], tags=('win',) if p is not None and p>0 else ('loss',) if p is not None and p<0 else ())

    def sort_history(self, col):
        if self.sort_col==col:self.sort_reverse=not self.sort_reverse
        else:self.sort_col=col;self.sort_reverse=False
        self.load_history()

    def new_trade(self):
        self.trade_id=None
        self.pending_images={}
        for v in self.fields.values():v.set('')
        self.fields['date'].set(dt.datetime.now().strftime('%Y-%m-%d %H:%M'))
        self.fields['market'].set('Forex')
        self.fields['side'].set('Buy')
        self.fields['emotion'].set('Calm')
        self.setup_note.delete('1.0','end')
        self.review_note.delete('1.0','end')
        self.editor_title.config(text='New Trade')
        self.refresh_images()
        self.tabs.select(self.editor)

    def edit_selected(self):
        items=self.tree.selection()
        if not items:return messagebox.showinfo('Select a trade','Select a trade first.')
        self.load_trade(int(items[0]))

    def load_trade(self,trade_id):
        with connect() as cn:
            row=cn.execute('SELECT * FROM trades WHERE id=?',(trade_id,)).fetchone()
            images=cn.execute('SELECT slot,path FROM trade_images WHERE trade_id=?',(trade_id,)).fetchall()
        if not row:return
        self.trade_id=trade_id
        for k,v in self.fields.items():v.set('' if row[k] is None else str(row[k]))
        self.setup_note.delete('1.0','end');self.setup_note.insert('1.0',row['setup'] or '')
        self.review_note.delete('1.0','end');self.review_note.insert('1.0',row['review'] or '')
        self.pending_images={r['slot']:r['path'] for r in images}
        self.editor_title.config(text=f'Editing Trade #{trade_id}')
        self.refresh_images()
        self.tabs.select(self.editor)

    def save_trade(self):
        values={k:v.get().strip() for k,v in self.fields.items()}
        if not values['symbol']:return self.notify('Enter a symbol before saving.', 'error')
        try:dt.datetime.strptime(values['date'],'%Y-%m-%d %H:%M')
        except ValueError:return self.notify('Date must use YYYY-MM-DD HH:MM.', 'error')
        for k in ('entry','exit','stop','target','size','risk','fees','pnl','pnl_high','pnl_low'):
            try:values[k]=float(values[k]) if values[k] else None
            except ValueError:return self.notify(f'{k} must be a number.', 'error')
        values['setup']=self.setup_note.get('1.0','end').strip()
        values['review']=self.review_note.get('1.0','end').strip()
        keys=COLUMNS
        with connect() as cn:
            if self.trade_id:
                cn.execute('UPDATE trades SET '+','.join(f'{k}=?' for k in keys)+' WHERE id=?',[values[k] for k in keys]+[self.trade_id])
                tid=self.trade_id
            else:
                cur=cn.execute('INSERT INTO trades ('+','.join(keys)+',created_at) VALUES ('+','.join(['?']*(len(keys)+1))+')',[values[k] for k in keys]+[dt.datetime.now().isoformat()])
                tid=cur.lastrowid
            old={r['slot']:r['path'] for r in cn.execute('SELECT slot,path FROM trade_images WHERE trade_id=?',(tid,))}
            for slot,path in self.pending_images.items():
                if path is None:
                    cn.execute('DELETE FROM trade_images WHERE trade_id=? AND slot=?',(tid,slot))
                    continue
                source=Path(path)
                if not source.exists():continue
                # Copy into managed local storage, even if original file later moves.
                dest=IMAGES/f'{tid}_{slot}_{uuid.uuid4().hex[:9]}{source.suffix.lower() or ".png"}'
                if source.parent.resolve()!=IMAGES.resolve():shutil.copy2(source,dest)
                else:dest=source
                cn.execute('INSERT OR REPLACE INTO trade_images(trade_id,slot,path) VALUES(?,?,?)',(tid,slot,str(dest)))
            cn.commit()
        # Remove only files no longer referenced, never imported originals.
        with connect() as cn:
            used={x[0] for x in cn.execute('SELECT path FROM trade_images')}
        for p in old.values():
            if p not in used and Path(p).parent==IMAGES and Path(p).exists():
                try:Path(p).unlink()
                except OSError:pass
        self.load_trade(tid)
        self.refresh()
        self.notify(f'Trade #{tid} saved successfully  •  Stored locally')

    def delete_selected(self):
        sel=self.tree.selection()
        if not sel:return
        tid=int(sel[0])
        if not messagebox.askyesno('Delete trade',f'Permanently delete trade #{tid}?'):return
        with connect() as cn:
            paths=[r['path'] for r in cn.execute('SELECT path FROM trade_images WHERE trade_id=?',(tid,))]
            cn.execute('DELETE FROM trades WHERE id=?',(tid,))
        for p in paths:
            try:
                if Path(p).parent==IMAGES:Path(p).unlink(missing_ok=True)
            except OSError:pass
        self.refresh()
        if self.trade_id==tid:self.new_trade()

    def choose_image(self, slot):
        file=filedialog.askopenfilename(title=f'Choose {slot} screenshot',filetypes=[('Images','*.png *.jpg *.jpeg *.webp *.bmp'),('All files','*.*')])
        if file:self.pending_images[slot]=file;self.refresh_images()

    def paste_image(self,slot):
        try:
            from PIL import ImageGrab
            result=ImageGrab.grabclipboard()
            if result is None or not hasattr(result,'save'):raise ValueError('Clipboard does not contain an image.')
            file=IMAGES/f'clipboard_{uuid.uuid4().hex}.png'
            result.save(file,'PNG')
            self.pending_images[slot]=str(file)
            self.refresh_images()
        except Exception as e:messagebox.showerror('Paste failed',f'{e}\n\nInstall Pillow for clipboard image support.')

    def clear_image(self,slot):
        self.pending_images[slot]=None
        self.refresh_images()

    def view_image(self,slot):
        p=self.pending_images.get(slot)
        if not p or not Path(p).exists():return messagebox.showinfo('No image','Add an image to this slot first.')
        try:
            os.startfile(p) if os.name=='nt' else self.preview_window(p)
        except Exception as e:messagebox.showerror('Image error',str(e))

    def preview_window(self,p):
        from PIL import Image, ImageTk
        win=tk.Toplevel(self)
        win.title(Path(p).name)
        im=Image.open(p);im.thumbnail((1100,800))
        obj=ImageTk.PhotoImage(im)
        label=tk.Label(win,image=obj,bg=BG);label.image=obj;label.pack()

    def refresh_images(self):
        for slot,label in self.image_cards.items():
            p=self.pending_images.get(slot)
            label.configure(text='No screenshot', image='')
            label.image=None
            if p and Path(p).exists():
                try:
                    from PIL import Image, ImageTk
                    im=Image.open(p)
                    im.thumbnail((275,200))
                    obj=ImageTk.PhotoImage(im)
                    label.configure(image=obj,text='')
                    label.image=obj
                except Exception:label.configure(text=Path(p).name)

    def export_csv(self):
        out=filedialog.asksaveasfilename(defaultextension='.csv',filetypes=[('CSV','*.csv')],initialfile='layaq_trades.csv')
        if not out:return
        with open(out,'w',newline='',encoding='utf-8-sig') as f:
            writer=csv.writer(f)
            writer.writerow(['id']+COLUMNS)
            for row in self._all_trades():writer.writerow([row['id']]+[row[k] for k in COLUMNS])
        self.notify(f'CSV exported successfully: {Path(out).name}')

    def backup(self):
        dest=filedialog.asksaveasfilename(defaultextension='.zip',initialfile=f'layaq_backup_{dt.date.today().isoformat()}.zip',filetypes=[('ZIP archive','*.zip')])
        if not dest:return
        temp=BASE/f'_backup_{uuid.uuid4().hex}.sqlite3'
        with connect() as source, closing(sqlite3.connect(temp)) as target:source.backup(target)
        try:
            with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED) as z:
                z.write(temp,'journal.sqlite3')
                for file in IMAGES.glob('*'):
                    if file.is_file():z.write(file,'images/'+file.name)
        finally:temp.unlink(missing_ok=True)
        self.notify('Backup complete  •  Trades and screenshots included')

    def restore(self):
        archive=filedialog.askopenfilename(filetypes=[('ZIP archive','*.zip')])
        if not archive:return
        if not messagebox.askyesno('Restore backup','This replaces the current journal and images. Make a separate backup first. Continue?'):return
        staging=BASE/f'_restore_{uuid.uuid4().hex}'
        staging.mkdir()
        recovery=staging/'original.sqlite3'
        try:
            with zipfile.ZipFile(archive) as z:
                names=z.namelist()
                if 'journal.sqlite3' not in names:raise ValueError('Missing journal.sqlite3 in backup.')
                if any(n.startswith('/') or '\\' in n or '..' in Path(n).parts for n in names):raise ValueError('Unsafe backup paths.')
                if z.testzip() is not None:raise ValueError('Corrupt backup archive.')
                staged_db=staging/'new.sqlite3';staged_db.write_bytes(z.read('journal.sqlite3'))
                image_dir=staging/'images';image_dir.mkdir()
                for n in names:
                    if n.startswith('images/') and not n.endswith('/'):
                        if Path(n).name!=n[7:]:raise ValueError('Invalid image path.')
                        (image_dir/Path(n).name).write_bytes(z.read(n))
            with closing(sqlite3.connect(staged_db)) as restored:
                if restored.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup database failed integrity check.')
                tables={r[0] for r in restored.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'trades','trade_images'}.issubset(tables):raise ValueError('Not a compatible journal backup.')
                for tid,slot,oldpath in restored.execute('SELECT trade_id,slot,path FROM trade_images').fetchall():
                    filename=Path(str(oldpath).replace('\\','/')).name
                    if not (image_dir/filename).is_file():raise ValueError(f'Missing screenshot: {filename}')
                    restored.execute('UPDATE trade_images SET path=? WHERE trade_id=? AND slot=?',(str(IMAGES/filename),tid,slot))
                restored.commit()
            # SQLite's supported backup API replaces database CONTENTS without Windows
            # file renames; no os.replace() or access-denied swap of journal.sqlite3.
            with closing(sqlite3.connect(DB)) as live, closing(sqlite3.connect(recovery)) as saved:
                live.backup(saved)
            with closing(sqlite3.connect(staged_db)) as source, closing(sqlite3.connect(DB,timeout=20)) as target:
                source.backup(target)
            # Preserve original images until replacement is verified.
            old_images=staging/'old_images';shutil.copytree(IMAGES,old_images)
            try:
                for f in IMAGES.iterdir():
                    if f.is_file():f.unlink()
                for f in image_dir.iterdir():shutil.copy2(f,IMAGES/f.name)
                setup()
            except Exception:
                with closing(sqlite3.connect(recovery)) as source, closing(sqlite3.connect(DB,timeout=20)) as target:
                    source.backup(target)
                shutil.rmtree(IMAGES,ignore_errors=True);shutil.copytree(old_images,IMAGES)
                raise
            self.new_trade();self.refresh()
            self.notify('Restore complete  •  Trades and screenshots recovered')
        except Exception as exc:
            messagebox.showerror('Restore failed',str(exc))
        finally:
            shutil.rmtree(staging,ignore_errors=True)

if __name__=='__main__':
    App().mainloop()
