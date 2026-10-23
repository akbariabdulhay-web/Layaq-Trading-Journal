"""Layaq Trading Journal Super App — local-first Windows trading journal."""
import csv
import hashlib
import hmac
import json
import secrets
import datetime as dt
import calendar
from collections import defaultdict
import os
import shutil
import sqlite3
import sys
import uuid
import zipfile
from pathlib import Path
from contextlib import contextmanager, closing
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog as _native_filedialog  # used only as fallback if requested
from PIL import Image, ImageTk

APP_NAME = 'Layaq Trading Journal Super App'
BASE = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'LayaqTradingJournal'
IMAGES = BASE / 'images'
DB = BASE / 'journal.sqlite3'
BACKUPS = BASE / 'backups'
AUTH = BASE / 'access.json'
LOGO = BASE / 'custom_logo.png'
BASE.mkdir(parents=True, exist_ok=True)
IMAGES.mkdir(exist_ok=True)
BACKUPS.mkdir(exist_ok=True)

BG, PANEL, ENTRY, TEXT, MUTED, ACCENT, GREEN, RED = '#0b1220', '#152238', '#1f3048', '#f1f6ff', '#a3b6ca', '#5db9ff', '#45d6ad', '#ff6d82'
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


# All legacy confirmation, text, and file-selection calls are routed into the same root UI.
_ACTIVE_APP = None

class _EmbeddedMessages:
    @staticmethod
    def askyesno(title, message, **kw):
        return _ACTIVE_APP._overlay_prompt(title, message, mode='confirm') is True
    @staticmethod
    def showwarning(title, message, **kw):
        return _ACTIVE_APP._overlay_prompt(title, message, mode='notice')
    showerror = showwarning
    showinfo = showwarning

class _EmbeddedInput:
    @staticmethod
    def askstring(title, prompt, **kw):
        return _ACTIVE_APP._overlay_prompt(title, prompt, mode='entry', secret=kw.get('show')=='*')

class _EmbeddedFiles:
    @staticmethod
    def askopenfilename(**kw):
        return _ACTIVE_APP._file_overlay(kw, save=False)
    @staticmethod
    def asksaveasfilename(**kw):
        return _ACTIVE_APP._file_overlay(kw, save=True)

messagebox = _EmbeddedMessages()
simpledialog = _EmbeddedInput()
filedialog = _EmbeddedFiles()

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        global _ACTIVE_APP
        _ACTIVE_APP = self
        self.overrideredirect(True)
        self.geometry('1380x840+80+40')
        self.minsize(900, 620)
        self.after(40, self._maximize_custom)
        self.bind('<Escape>', self._escape_handler)
        self.configure(bg=BG)
        self._logo_image = None
        self._logo_widgets = []
        self._style()
        self._chrome()
        self._set_window_icon()
        self._toast_job = None
        self._toast_frame = None
        self._pulse_job = None
        self._show_access_screen()

    def _maximize_custom(self):
        try:
            sw,sh=self.winfo_screenwidth(),self.winfo_screenheight()
            self.geometry(f'{sw}x{sh}+0+0')
            self._maximized=True
        except tk.TclError: pass

    def _escape_handler(self,event=None):
        # Do not silently quit or bypass authentication.
        return None

    def _chrome(self):
        self._maximized=True
        bar=tk.Frame(self,bg='#0b111b',height=42)
        bar.pack(fill='x',side='top')
        bar.pack_propagate(False)
        self.chrome=bar
        # The logo is also embedded in the application title bar.
        self._add_logo(bar,28,'#0b111b',side='left')
        tk.Label(bar,text='LAYAQ   /   TRADING JOURNAL',fg='#eaf4ff',bg='#0b111b',
            font=('Segoe UI Semibold',10)).pack(side='left',padx=(0,16))
        tk.Label(bar,text='●  OFFLINE WORKSPACE',fg=GREEN,bg='#0b111b',
            font=('Segoe UI',9)).pack(side='left')
        for label,action,hot in [('×',self.destroy,'#a33a48'),('▢',self._toggle_max,'#263b57'),('−',self._minimize,'#263b57')]:
            btn=tk.Label(bar,text=label,bg='#0b111b',fg='#eaf4ff',font=('Segoe UI',17),width=4,cursor='hand2')
            btn.pack(side='right',fill='y')
            btn.bind('<Button-1>',lambda e,act=action:act())
            btn.bind('<Enter>',lambda e,b=btn,c=hot:b.config(bg=c))
            btn.bind('<Leave>',lambda e,b=btn:b.config(bg='#0b111b'))
        for child in (bar,):
            child.bind('<ButtonPress-1>',self._drag_start)
            child.bind('<B1-Motion>',self._drag_move)

    def _drag_start(self,e):
        self._drag_x=e.x_root-self.winfo_x();self._drag_y=e.y_root-self.winfo_y()
    def _drag_move(self,e):
        if getattr(self,'_maximized',False):return
        self.geometry(f'+{e.x_root-self._drag_x}+{e.y_root-self._drag_y}')
    def _toggle_max(self):
        if getattr(self,'_maximized',False):
            self.geometry('1260x790+100+70');self._maximized=False
        else:self._maximize_custom()
    def _minimize(self):
        # Borderless Tk windows need temporary window-manager decorations to iconify reliably.
        self.overrideredirect(False)
        self.iconify()
        self.bind('<Map>',self._restore_borderless,add='+')
    def _restore_borderless(self,event=None):
        if self.state()!='normal':return
        self.after(40,lambda:self.overrideredirect(True) if self.state()=='normal' else None)

    def _on_tab_change(self,event=None):
        if not hasattr(self,'tabs'):return
        # Gentle in-page highlight, without blocking navigation or changing saved values.
        self._animate_activity(ACCENT)
        current=self.tabs.select()
        try:
            tab=self.nametowidget(current)
            flash=tk.Frame(tab,bg=ACCENT,height=2)
            flash.place(relx=0,rely=0,relwidth=1)
            self.after(230,lambda:flash.destroy() if flash.winfo_exists() else None)
        except (tk.TclError,AttributeError):pass

    def _overlay_shell(self,title):
        """A blocking interaction rendered inside this SAME Tk root, never a Toplevel."""
        cover=tk.Frame(self,bg='#101622')
        cover.place(x=0,y=42,relwidth=1,relheight=1)
        cover.lift()
        cover._finished=tk.StringVar(value='pending')
        frame=tk.Frame(cover,bg=PANEL,highlightbackground='#365272',highlightthickness=1,padx=24,pady=20)
        frame.place(relx=.5,rely=.46,anchor='center')
        tk.Label(frame,text=title,bg=PANEL,fg=ACCENT,font=('Segoe UI Semibold',18)).pack(anchor='w',pady=(0,12))
        body=tk.Frame(frame,bg=PANEL)
        body.pack(fill='both',expand=True)
        def finish(value):
            cover._result=value
            cover._finished.set('done')
        cover._result=None
        return cover,body,finish

    def _overlay_prompt(self,title,message,mode='notice',secret=False):
        overlay,body,finish=self._overlay_shell(title)
        tk.Label(body,text=message,bg=PANEL,fg=TEXT,wraplength=520,justify='left',
                 font=('Segoe UI',11)).pack(anchor='w',pady=(0,16))
        entry=None
        if mode=='entry':
            value=tk.StringVar()
            entry=ttk.Entry(body,textvariable=value,width=60,show='*' if secret else '')
            entry.pack(fill='x',pady=(0,15));entry.focus_set()
            entry.bind('<Return>',lambda e:finish(value.get()))
        row=tk.Frame(body,bg=PANEL);row.pack(fill='x',pady=(4,0))
        if mode!='notice':
            ttk.Button(row,text='Cancel',command=lambda:finish(None if mode=='entry' else False)).pack(side='right',padx=5)
        ttk.Button(row,text='Confirm' if mode!='notice' else 'OK',style='Accent.TButton',
                   command=lambda:finish(value.get() if mode=='entry' else True)).pack(side='right',padx=5)
        self.wait_variable(overlay._finished)
        result=overlay._result;overlay.destroy()
        return result

    def _file_overlay(self,kw,save=False):
        """Embedded directory browser supporting paths, folders and typed filenames."""
        title=kw.get('title') or ('Save file' if save else 'Choose file')
        overlay,body,finish=self._overlay_shell(title)
        start=Path.home()/'Downloads'
        if not start.is_dir():start=Path.home()
        folder=tk.StringVar(value=str(start))
        filename=tk.StringVar(value=kw.get('initialfile',''))
        tk.Label(body,text='Folder',fg=MUTED,bg=PANEL).pack(anchor='w')
        folder_entry=ttk.Entry(body,textvariable=folder,width=72);folder_entry.pack(fill='x',pady=4)
        files=tk.Listbox(body,width=80,height=12,bg=ENTRY,fg=TEXT,selectbackground='#31629b',
                        borderwidth=0,highlightthickness=0,font=('Segoe UI',10))
        files.pack(fill='both',expand=True,pady=9)
        patterns=kw.get('filetypes',[])
        extensions=[]
        if patterns:
            for mask in str(patterns[0][1]).split():
                if mask.startswith('*.'):extensions.append(mask[1:].lower())
        def refresh_dir():
            files.delete(0,'end')
            try:
                location=Path(folder.get()).expanduser()
                for p in sorted(location.iterdir(),key=lambda x:(not x.is_dir(),x.name.lower())):
                    if p.is_dir() or not extensions or p.suffix.lower() in extensions:
                        files.insert('end',('📁 ' if p.is_dir() else '      ')+p.name)
            except OSError as exc:
                self.notify(f'Cannot list directory: {exc}','error')
        def choose(e=None):
            selection=files.curselection()
            if not selection:return
            name=files.get(selection[0]).replace('📁 ','',1).strip()
            p=Path(folder.get())/name
            if p.is_dir():folder.set(str(p));refresh_dir()
            else:filename.set(name)
        def up():
            folder.set(str(Path(folder.get()).parent));refresh_dir()
        row=tk.Frame(body,bg=PANEL);row.pack(fill='x')
        ttk.Button(row,text='↑ Parent folder',command=up).pack(side='left',padx=3)
        ttk.Button(row,text='Open folder / Refresh',command=refresh_dir).pack(side='left',padx=3)
        files.bind('<Double-Button-1>',choose)
        files.bind('<<ListboxSelect>>',choose)
        tk.Label(body,text='Filename or full path',bg=PANEL,fg=MUTED).pack(anchor='w',pady=(10,2))
        entry=ttk.Entry(body,textvariable=filename,width=72);entry.pack(fill='x')
        def submit():
            val=filename.get().strip()
            if not val:return self.notify('Choose or enter a filename.','error')
            p=Path(val).expanduser()
            if not p.is_absolute():p=Path(folder.get())/p
            if save and kw.get('defaultextension') and not p.suffix:
                p=p.with_suffix(kw['defaultextension'])
            if not save and not p.is_file():return self.notify('File not found.','error')
            if save and not p.parent.is_dir():return self.notify('Choose an existing destination folder.','error')
            finish(str(p))
        buttons=tk.Frame(body,bg=PANEL);buttons.pack(fill='x',pady=15)
        ttk.Button(button,text='Cancel',command=lambda:finish('')).pack(side='right',padx=5)
        ttk.Button(button,text='Save here' if save else 'Use selected file',style='Accent.TButton',command=submit).pack(side='right',padx=5)
        refresh_dir()
        self.wait_variable(overlay._finished)
        result=overlay._result;overlay.destroy()
        return result

    def _show_access_screen(self):
        self.auth_frame = tk.Frame(self, bg=BG)
        self.auth_frame.pack(fill='both', expand=True)
        pane = tk.Frame(self.auth_frame, bg=PANEL, padx=30, pady=26)
        pane.place(relx=.5, rely=.5, anchor='center')
        self._add_logo(pane, 100, PANEL)
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
        ttk.Separator(box,orient='horizontal').pack(fill='x',pady=(16,12))
        ttk.Label(box,text='Trade ID numbering',font=('Segoe UI Semibold',16)).pack(anchor='w',pady=(0,6))
        ttk.Label(box,text='Reset the next Trade ID to 1 after all trades have been deleted.\nExisting trades and screenshots are never deleted by this action.',style='Muted.TLabel').pack(anchor='w',pady=(0,10))
        ttk.Button(box,text='Reset Trade ID Counter',command=self.reset_trade_ids).pack(anchor='w',pady=6)
        ttk.Separator(box,orient='horizontal').pack(fill='x',pady=(16,12))
        ttk.Label(box,text='Branding / Logo',font=('Segoe UI Semibold',16)).pack(anchor='w',pady=(0,6))
        ttk.Label(box,text='Change the in-app logo now. To update the Windows EXE / Desktop icon,\nexport icon.ico and rebuild your EXE on GitHub.',style='Muted.TLabel').pack(anchor='w',pady=(0,8))
        ttk.Button(box,text='Choose App Logo (PNG / JPG)',command=self._change_logo).pack(anchor='w',pady=5)
        ttk.Button(box,text='Export Windows Icon (.ico)',command=self._export_icon).pack(anchor='w',pady=5)
        ttk.Button(box,text='Restore Default App Logo',command=self._reset_logo).pack(anchor='w',pady=5)
        self._refresh_security_label()

    def _logo_source(self):
        if LOGO.is_file():
            return LOGO
        # Compiled GitHub asset, if supplied.
        bundled=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'icon.png'
        return bundled if bundled.exists() else None

    def _load_logo(self, size):
        source=self._logo_source()
        if source:
            try:
                with Image.open(source) as source_img:
                    img=source_img.convert('RGBA')
                    img.thumbnail((size,size),Image.Resampling.LANCZOS)
                    self._logo_image=ImageTk.PhotoImage(img)
                    return self._logo_image
            except (OSError,ValueError):
                pass
        return None

    def _add_logo(self,parent,size,bg,side=None):
        holder=tk.Label(parent,bg=bg,bd=0)
        if side: holder.pack(side=side,padx=(0,10))
        else: holder.pack(pady=(0,8))
        holder.logo_size=size
        self._logo_widgets.append(holder)
        image=self._load_logo(size)
        if image:
            holder.configure(image=image,text='')
            holder.image=image
        else:
            holder.configure(text='◈',font=('Segoe UI',min(44,size//2)),fg=ACCENT)
        return holder

    def _reload_logo(self):
        for widget in list(self._logo_widgets):
            if not widget.winfo_exists():
                self._logo_widgets.remove(widget)
                continue
            image=self._load_logo(widget.logo_size)
            if image:
                widget.configure(image=image,text='')
                widget.image=image
            else:
                widget.configure(image='',text='◈',font=('Segoe UI',min(44,widget.logo_size//2)),fg=ACCENT)
                widget.image=None
        self._set_window_icon()

    def _set_window_icon(self):
        source=self._logo_source()
        if source:
            try:
                with Image.open(source) as raw:
                    img=raw.convert('RGBA')
                    img.thumbnail((64,64),Image.Resampling.LANCZOS)
                    self._window_icon=ImageTk.PhotoImage(img)
                    self.iconphoto(True,self._window_icon)
            except (OSError, ValueError):
                pass

    def _change_logo(self):
        file=filedialog.askopenfilename(parent=self,title='Choose application logo',filetypes=[('Images','*.png *.jpg *.jpeg *.webp'),('All files','*.*')])
        if not file:return
        try:
            with Image.open(file) as raw:
                image=raw.convert('RGBA')
                if max(image.size)>2048:
                    image.thumbnail((2048,2048),Image.Resampling.LANCZOS)
                temporary=LOGO.with_name('logo_new.png')
                image.save(temporary,'PNG')
            os.replace(temporary,LOGO)
            self._reload_logo()
            self.notify('App logo updated. Export an .ico for the EXE icon.')
        except (OSError,ValueError) as exc:
            self.notify(f'Could not update logo: {exc}','error',6000)

    def _reset_logo(self):
        try:
            if LOGO.exists():LOGO.unlink()
            self._reload_logo()
            self.notify('Default in-app logo restored.')
        except OSError as exc:self.notify(f'Could not reset logo: {exc}','error',6000)

    def _export_icon(self):
        source=self._logo_source()
        if not source:
            self.notify('Select a logo first, then export its Windows icon.','error',6000)
            return
        path=filedialog.asksaveasfilename(parent=self,title='Export Windows EXE icon',defaultextension='.ico',initialfile='icon.ico',filetypes=[('Windows icon','*.ico')])
        if not path:return
        try:
            with Image.open(source) as raw:
                im=raw.convert('RGBA')
                # Center image on square transparent canvas to avoid stretching.
                im.thumbnail((256,256),Image.Resampling.LANCZOS)
                canvas=Image.new('RGBA',(256,256),(0,0,0,0))
                canvas.alpha_composite(im,((256-im.width)//2,(256-im.height)//2))
                canvas.save(path,format='ICO',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
            self.notify('Windows icon exported. Upload icon.ico to GitHub and rebuild the EXE.',duration=6500)
        except (OSError,ValueError) as exc:
            self.notify(f'Icon export failed: {exc}','error',6000)

    def reset_trade_ids(self):
        # Reset only an empty journal: SQLite AUTOINCREMENT will otherwise use MAX(id)+1.
        try:
            with connect() as cn:
                remaining=cn.execute('SELECT COUNT(*) FROM trades').fetchone()[0]
                if remaining:
                    return self.notify(f'Cannot reset IDs: {remaining} trade(s) still exist. Remove them first.', 'error', 6500)
            if not messagebox.askyesno('Reset Trade IDs',
                                      'Reset next Trade ID to 1?\n\nThis does not delete trades, images, or settings.\nThis works only while the journal is empty.',parent=self):
                return
            # Make a durable pre-change DB copy before modifying the sequence.
            backup_path=BACKUPS / ('before_id_reset_' + dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.sqlite3')
            with closing(sqlite3.connect(DB)) as source, closing(sqlite3.connect(backup_path)) as target:
                source.backup(target)
            with connect() as cn:
                cn.execute('BEGIN IMMEDIATE')
                if cn.execute('SELECT COUNT(*) FROM trades').fetchone()[0]:
                    return self.notify('Journal is no longer empty; reset cancelled.', 'error')
                cn.execute("DELETE FROM sqlite_sequence WHERE name='trades'")
            self.refresh()
            self.notify('Trade ID counter reset. Next new trade will be #1.')
        except Exception as exc:
            self.notify(f'ID reset failed: {exc}', 'error', 7500)

    def _style(self):
        s = ttk.Style(self)
        s.theme_use('clam')
        s.configure('.', font=('Segoe UI', 10), background=BG, foreground=TEXT)
        s.configure('TFrame', background=BG)
        s.configure('Panel.TFrame', background=PANEL)
        s.configure('TLabel', background=BG, foreground=TEXT)
        s.configure('Muted.TLabel', background=BG, foreground=MUTED)
        s.configure('TButton', background=ENTRY, foreground=TEXT, padding=(10, 7), borderwidth=0)
        s.map('TButton', background=[('active', '#415b7a'), ('pressed','#27405f')], foreground=[('disabled','#73859b')])
        s.configure('Accent.TButton', background=ACCENT, foreground='#071426', padding=(14,9))
        s.map('Accent.TButton',background=[('active','#72b8ff'),('pressed','#3289d9')])
        s.configure('TEntry', fieldbackground=ENTRY, foreground=TEXT, insertcolor=TEXT, padding=6)
        s.configure('TCombobox', fieldbackground=ENTRY, background=ENTRY, foreground=TEXT, padding=5)
        s.map('TCombobox', fieldbackground=[('readonly', ENTRY)], foreground=[('readonly', TEXT)])
        s.configure('Treeview', background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=30, borderwidth=0)
        s.map('Treeview', background=[('selected', '#245483')], foreground=[('selected', 'white')])
        s.configure('Treeview.Heading', background=ENTRY, foreground=TEXT, padding=8)
        s.configure('TNotebook', background=BG, borderwidth=0)
        s.configure('TNotebook.Tab', padding=(16, 9), background=ENTRY, foreground=TEXT)
        s.map('TNotebook.Tab', background=[('selected', PANEL),('active','#30405a')], foreground=[('selected','#8bd2ff'),('active','#f9fbff')])

    def _layout(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill='x', padx=22, pady=(16, 6))
        self._add_logo(header, 48, BG, side='left')
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
        self.calendar_tab = ttk.Frame(self.tabs)
        self.analytics_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.dashboard, text='Dashboard')
        self.tabs.add(self.history, text='Trade History')
        self.tabs.add(self.editor, text='Trade Editor & Charts')
        self.tabs.add(self.calendar_tab, text='Trading Calendar')
        self.tabs.add(self.analytics_tab, text='Advanced Analytics')
        self.tabs.add(self.settings_tab, text='Settings')
        self._make_dashboard()
        self._make_history()
        self._make_editor()
        self._make_settings()
        self._make_calendar()
        self._make_analytics()
        self.tabs.bind('<<NotebookTabChanged>>', self._on_tab_change)

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


    # -------- Trading calendar and analytics (closed trades only) --------
    def _closed_records(self):
        output = []
        for row in self._all_trades():
            if row['pnl'] is None:
                continue
            try:
                stamp = dt.datetime.fromisoformat(str(row['date']).strip().replace('Z', '+00:00'))
                output.append((stamp, row, float(row['pnl'])))
            except (ValueError, TypeError, OverflowError):
                continue
        return output

    def _make_calendar(self):
        top = ttk.Frame(self.calendar_tab)
        top.pack(fill='x', padx=16, pady=12)
        ttk.Label(top, text='TRADING PERFORMANCE CALENDAR', font=('Segoe UI Semibold', 17)).pack(side='left', padx=(0, 20))
        now = dt.date.today()
        self.cal_year = tk.IntVar(value=now.year)
        self.cal_month = tk.IntVar(value=now.month)
        ttk.Button(top, text='◀ Previous', command=lambda: self._shift_calendar(-1)).pack(side='left', padx=5)
        self.cal_heading = ttk.Label(top, text='', font=('Segoe UI Semibold', 14))
        self.cal_heading.pack(side='left', padx=12)
        ttk.Button(top, text='Next ▶', command=lambda: self._shift_calendar(1)).pack(side='left', padx=5)
        ttk.Button(top, text='Current Month', command=self._today_calendar).pack(side='left', padx=12)
        self.cal_summary = ttk.Label(self.calendar_tab, text='', font=('Segoe UI', 11))
        self.cal_summary.pack(anchor='w', padx=20, pady=(0, 8))
        self.cal_grid = ttk.Frame(self.calendar_tab)
        self.cal_grid.pack(fill='both', expand=True, padx=16, pady=(4, 12))
        self.cal_details = ttk.Label(self.calendar_tab, text='Click a day to see its result and trades.', font=('Segoe UI', 11))
        self.cal_details.pack(anchor='w', padx=20, pady=(0, 15))

    def _shift_calendar(self, step):
        y, m = self.cal_year.get(), self.cal_month.get() + step
        y += (m-1)//12
        m = (m-1)%12 + 1
        self.cal_year.set(y)
        self.cal_month.set(m)
        self.refresh_calendar()

    def _today_calendar(self):
        self.cal_year.set(dt.date.today().year)
        self.cal_month.set(dt.date.today().month)
        self.refresh_calendar()

    def _calendar_select_day(self, date_value, pnls):
        if not pnls:
            self.cal_details.config(text=f'{date_value:%d %b %Y}  ·  No closed trades')
        else:
            wins = sum(v > 0 for v in pnls)
            self.cal_details.config(text=f'{date_value:%d %b %Y}  ·  {len(pnls)} trades  ·  '
                               f'Net P&L {sum(pnls):+,.2f}  ·  Wins {wins}  ·  Losses {sum(v < 0 for v in pnls)}')

    def refresh_calendar(self):
        if not hasattr(self, 'cal_grid'):
            return
        y, m = self.cal_year.get(), self.cal_month.get()
        self.cal_heading.config(text=f'{calendar.month_name[m]} {y}')
        daily = defaultdict(list)
        for stamp, row, pnl in self._closed_records():
            daily[stamp.date()].append(pnl)
        monthly_values = [x for day, values in daily.items() if day.year == y and day.month == m for x in values]
        active_days = sum(bool(values) for day, values in daily.items() if day.year == y and day.month == m)
        self.cal_summary.config(text=f'Month Net P&L: {sum(monthly_values):+,.2f}    |    '
                                f'Closed Trades: {len(monthly_values)}    |    Trading Days: {active_days}    |    '
                                f'Winning Days: {sum(sum(v)>0 for d,v in daily.items() if d.year==y and d.month==m)}')
        for widget in self.cal_grid.winfo_children():
            widget.destroy()
        for col, title in enumerate(('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')):
            ttk.Label(self.cal_grid, text=title, anchor='center', font=('Segoe UI Semibold', 11)).grid(row=0, column=col, sticky='ew', pady=(0, 5))
            self.cal_grid.grid_columnconfigure(col, weight=1, uniform='calendar')
        weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(y, m)
        for r, week in enumerate(weeks, 1):
            self.cal_grid.grid_rowconfigure(r, weight=1)
            for col, day in enumerate(week):
                vals = daily.get(day, []) if day.month == m else []
                net = sum(vals)
                bg = ('#154737' if net > 0 else '#552a35' if net < 0 else PANEL)
                if day.month != m:
                    bg = BG
                label = f'{day.day}\n{net:+,.2f}  ({len(vals)})' if vals else str(day.day)
                button = tk.Button(self.cal_grid, text=label, bg=bg, fg=TEXT if day.month == m else MUTED,
                                   activebackground='#345577', activeforeground=TEXT, relief='flat',
                                   cursor='hand2', font=('Segoe UI', 10), justify='center',
                                   command=lambda d=day, v=tuple(vals): self._calendar_select_day(d, v))
                button.grid(row=r, column=col, sticky='nsew', padx=3, pady=3, ipady=9)

    def _make_analytics(self):
        outer = ttk.Frame(self.analytics_tab)
        outer.pack(fill='both', expand=True, padx=18, pady=12)
        ttk.Label(outer, text='ADVANCED TRADING ANALYTICS', font=('Segoe UI Semibold', 18)).pack(anchor='w', pady=(0, 12))
        period_row = ttk.Frame(outer)
        period_row.pack(fill='x', pady=(0, 14))
        ttk.Label(period_row, text='Report period:').pack(side='left', padx=(0, 8))
        self.analytics_period = tk.StringVar(value='All Time')
        for option in ('All Time', 'This Year', 'This Month', 'This Week'):
            ttk.Radiobutton(period_row, text=option, value=option, variable=self.analytics_period,
                            command=self.refresh_analytics).pack(side='left', padx=7)
        self.analytics_numbers = ttk.Frame(outer)
        self.analytics_numbers.pack(fill='x', pady=(0, 16))
        self.analytics_labels = {}
        for n in ('Total Trades', 'Net P&L', 'Expectancy', 'Max Drawdown', 'Max DD %',
                  'Win Rate', 'Avg Win', 'Avg Loss', 'Profit Factor', 'Best Streak', 'Worst Streak', 'Payoff Ratio'):
            cell = tk.Frame(self.analytics_numbers, bg=PANEL, padx=13, pady=9)
            idx = len(self.analytics_labels)
            cell.grid(row=idx//6, column=idx%6, sticky='nsew', padx=4, pady=4)
            tk.Label(cell, text=n, bg=PANEL, fg=MUTED).pack(anchor='w')
            value = tk.Label(cell, text='—', bg=PANEL, fg=TEXT, font=('Segoe UI Semibold', 15))
            value.pack(anchor='w', pady=5)
            self.analytics_labels[n] = value
        for col in range(6): self.analytics_numbers.grid_columnconfigure(col, weight=1)
        tables = ttk.Frame(outer)
        tables.pack(fill='both', expand=True)
        self.analytics_tables = {}
        for index, name in enumerate(('Strategy Performance', 'Session Performance', 'Symbol Performance')):
            box = ttk.Frame(tables)
            box.grid(row=0, column=index, sticky='nsew', padx=6)
            ttk.Label(box, text=name, font=('Segoe UI Semibold', 13)).pack(anchor='w', pady=(0, 8))
            tree = ttk.Treeview(box, columns=('name','trades','winrate','net','avg'), show='headings', height=11)
            for key, label, size in [('name','NAME',135),('trades','TRADES',65),('winrate','WIN %',70),('net','NET P&L',110),('avg','AVG P&L',100)]:
                tree.heading(key, text=label)
                tree.column(key, width=size, minwidth=55, anchor='w' if key=='name' else 'e')
            tree.pack(fill='both', expand=True)
            self.analytics_tables[name] = tree
            tables.grid_columnconfigure(index, weight=1)
        tables.grid_rowconfigure(0, weight=1)
        ttk.Label(outer, text='Calculated from closed trades. Drawdown % is unavailable without a starting account balance; '
                  'a cash drawdown is shown instead. Date filters use the computer’s local dates.',
                  style='Muted.TLabel', wraplength=1100).pack(anchor='w', pady=(12, 0))

    def refresh_analytics(self):
        if not hasattr(self, 'analytics_labels'):
            return
        today = dt.date.today()
        period = self.analytics_period.get()
        records = []
        for stamp, row, pnl in self._closed_records():
            day = stamp.date()
            if period == 'This Year' and day.year != today.year: continue
            if period == 'This Month' and (day.year, day.month) != (today.year, today.month): continue
            if period == 'This Week' and day.isocalendar()[:2] != today.isocalendar()[:2]: continue
            records.append((stamp, row, pnl))
        vals = [x[2] for x in records]
        wins = [x for x in vals if x > 0]
        losses = [x for x in vals if x < 0]
        peak = equity = dd = 0.0
        win_streak = lose_streak = best = worst = 0
        for p in vals:
            equity += p
            peak = max(peak, equity)
            dd = max(dd, peak-equity)
            if p > 0:
                win_streak += 1; lose_streak = 0; best = max(best, win_streak)
            elif p < 0:
                lose_streak += 1; win_streak = 0; worst = max(worst, lose_streak)
            else:
                win_streak = lose_streak = 0
        avgwin = sum(wins)/len(wins) if wins else 0
        avgloss = abs(sum(losses))/len(losses) if losses else 0
        factor = sum(wins)/abs(sum(losses)) if losses else None
        report = {
            'Total Trades':str(len(vals)), 'Net P&L':f'{sum(vals):+,.2f}',
            'Expectancy': f'{sum(vals)/len(vals):+,.2f}' if vals else '—',
            'Max Drawdown':f'{dd:,.2f}', 'Max DD %':'N/A',
            'Win Rate':f'{100*len(wins)/len(vals):.1f}%' if vals else '—',
            'Avg Win':f'{avgwin:+,.2f}' if wins else '—',
            'Avg Loss':f'{-avgloss:,.2f}' if losses else '—',
            'Profit Factor':f'{factor:.2f}' if factor is not None else ('∞' if wins else '—'),
            'Best Streak':str(best), 'Worst Streak':str(worst),
            'Payoff Ratio':f'{avgwin/avgloss:.2f}' if avgloss and wins else '—'
        }
        for key, label in self.analytics_labels.items(): label.config(text=report[key])
        for name, field in (('Strategy Performance','strategy'),('Session Performance','session'),('Symbol Performance','symbol')):
            grouped = defaultdict(list)
            for _, row, pnl in records:
                grouped[str(row[field] or 'Unspecified')].append(pnl)
            tree = self.analytics_tables[name]
            tree.delete(*tree.get_children())
            for key, group in sorted(grouped.items(), key=lambda item: sum(item[1]), reverse=True):
                tree.insert('', 'end', values=(key,len(group),f'{100*sum(p>0 for p in group)/len(group):.1f}%',
                                               f'{sum(group):+,.2f}',f'{sum(group)/len(group):+,.2f}'))

    def _make_history(self):
        bar = ttk.Frame(self.history)
        bar.pack(fill='x', pady=10)
        ttk.Label(bar, text='Search:').pack(side='left', padx=5)
        self.search_var = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.search_var, width=30)
        ent.pack(side='left', padx=5)
        ent.bind('<KeyRelease>', lambda e: self.load_history())
        ttk.Button(bar, text='+ New Trade', style='Accent.TButton', command=self.new_trade).pack(side='right', padx=5)
        ttk.Button(bar, text='Delete All Trades', command=self.delete_all_trades).pack(side='right', padx=5)
        ttk.Button(bar, text='Delete Selected', command=self.delete_selected).pack(side='right', padx=5)
        ttk.Button(bar, text='Select All', command=self.select_all_trades).pack(side='right', padx=5)
        ttk.Button(bar, text='Clear Selection', command=self.clear_trade_selection).pack(side='right', padx=5)
        ttk.Button(bar, text='Edit Selected', command=self.edit_selected).pack(side='right', padx=5)
        cols = ('id','date','symbol','market','side','session','strategy','entry','exit','pnl','emotion')
        self.tree = ttk.Treeview(self.history, columns=cols, show='headings', selectmode='extended')
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
        self.history_selection_label = ttk.Label(bar, text='Ctrl+Click / Shift+Click to select multiple', style='Muted.TLabel')
        self.history_selection_label.pack(side='left', padx=9)
        self.tree.bind('<<TreeviewSelect>>', self.update_trade_selection_count)
        self.sort_col, self.sort_reverse = 'id', True

    def _make_editor(self):
        wrap = ttk.Frame(self.editor)
        wrap.pack(fill='both', expand=True)
        left = ttk.Frame(wrap, width=460)
        left.pack(side='left', fill='y', padx=(0, 15), pady=5)
        left.pack_propagate(False)
        # The editor has a persistent action bar and its own independently
        # scrollable form. Long notes are never clipped on smaller displays.
        action_bar = ttk.Frame(left)
        action_bar.pack(fill='x', pady=(0, 8))
        self.editor_title = ttk.Label(action_bar, text='New Trade', font=('Segoe UI Semibold', 18))
        self.editor_title.pack(anchor='w', pady=(0, 8))
        fixed_actions = ttk.Frame(action_bar)
        fixed_actions.pack(fill='x')
        ttk.Button(fixed_actions, text='Save Trade', style='Accent.TButton', command=self.save_trade).pack(side='left', padx=3)
        ttk.Button(fixed_actions, text='+ New Trade', command=self.new_trade).pack(side='left', padx=3)

        scroll_container = ttk.Frame(left)
        scroll_container.pack(fill='both', expand=True)
        editor_canvas = tk.Canvas(scroll_container, bg=BG, highlightthickness=0,
                                  borderwidth=0, takefocus=0)
        editor_scrollbar = ttk.Scrollbar(scroll_container, orient='vertical', command=editor_canvas.yview)
        editor_canvas.configure(yscrollcommand=editor_scrollbar.set)
        editor_scrollbar.pack(side='right', fill='y')
        editor_canvas.pack(side='left', fill='both', expand=True)
        form_body = ttk.Frame(editor_canvas)
        canvas_window = editor_canvas.create_window((0, 0), window=form_body, anchor='nw')
        form_body.bind('<Configure>', lambda e: editor_canvas.configure(scrollregion=editor_canvas.bbox('all')))
        editor_canvas.bind('<Configure>', lambda e: editor_canvas.itemconfigure(canvas_window, width=e.width))
        self.editor_form_canvas = editor_canvas

        def scroll_editor(event):
            if event.num == 4: increment = -1
            elif event.num == 5: increment = 1
            else: increment = -int(event.delta / 120) if event.delta else 0
            if increment:
                editor_canvas.yview_scroll(increment, 'units')
                return 'break'

        # Bind only within editor-form widgets: dashboard, chart mouse wheel,
        # and other tabs remain unaffected. Text fields scroll their own text.
        def bind_editor_wheel(widget):
            if not isinstance(widget, tk.Text):
                widget.bind('<MouseWheel>', scroll_editor, add='+')
                widget.bind('<Button-4>', scroll_editor, add='+')
                widget.bind('<Button-5>', scroll_editor, add='+')
            for child in widget.winfo_children():
                bind_editor_wheel(child)

        right = ttk.Frame(wrap)
        right.pack(side='left', fill='both', expand=True, pady=5)
        form = ttk.Frame(form_body)
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
        ttk.Label(form_body, text='Setup / Entry rationale').pack(anchor='w', pady=(10, 3))
        self.setup_note = tk.Text(form_body, height=5, bg=ENTRY, fg=TEXT, insertbackground=TEXT, wrap='word', relief='flat')
        self.setup_note.pack(fill='x')
        ttk.Label(form_body, text='Post-trade review / lessons').pack(anchor='w', pady=(9, 3))
        self.review_note = tk.Text(form_body, height=5, bg=ENTRY, fg=TEXT, insertbackground=TEXT, wrap='word', relief='flat')
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
        # Keep the last field visible and give a visual cue that scrolling works.
        ttk.Label(form_body, text='Scroll to reach all editor fields  •  Mouse wheel / scrollbar',
                  style='Muted.TLabel').pack(anchor='w', pady=(10, 14))
        bind_editor_wheel(form_body)
        editor_canvas.bind('<MouseWheel>', scroll_editor, add='+')
        editor_canvas.bind('<Button-4>', scroll_editor, add='+')
        editor_canvas.bind('<Button-5>', scroll_editor, add='+')
        self.new_trade()

    def _all_trades(self):
        with connect() as cn:
            return cn.execute('SELECT * FROM trades ORDER BY date, id').fetchall()

    def refresh(self):
        self.load_history()
        self.refresh_calendar()
        self.refresh_analytics()
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
        if not items:return self.notify('Select a trade first.', 'error')
        if len(items)>1:return self.notify('Select only one trade for editing.', 'error')
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

    def update_trade_selection_count(self, event=None):
        if hasattr(self, 'history_selection_label'):
            n=len(self.tree.selection())
            self.history_selection_label.config(text=f'{n} selected  •  Ctrl+Click / Shift+Click' if n else 'Ctrl+Click / Shift+Click to select multiple')

    def select_all_trades(self):
        # Select only currently visible (filtered) rows, never hidden trades.
        items=self.tree.get_children()
        if items:self.tree.selection_set(items)
        self.update_trade_selection_count()

    def clear_trade_selection(self):
        self.tree.selection_remove(self.tree.selection())
        self.update_trade_selection_count()

    def _safety_backup_before_delete(self):
        # Always preserve a restorable zip snapshot, including screenshots.
        filename=f"pre_delete_{dt.datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}.zip"
        dest=BACKUPS/filename
        temp=BASE/f'_safety_{uuid.uuid4().hex}.sqlite3'
        try:
            with connect() as source, closing(sqlite3.connect(temp)) as target:
                source.backup(target)
            with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED) as z:
                z.write(temp,'journal.sqlite3')
                for file in IMAGES.iterdir():
                    if file.is_file():z.write(file, 'images/'+file.name)
            with zipfile.ZipFile(dest) as z:
                if z.testzip() is not None:raise OSError('Backup verification failed.')
            return dest
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        finally:
            temp.unlink(missing_ok=True)

    def _delete_trades(self, ids):
        ids=sorted(set(int(x) for x in ids))
        if not ids:return
        # SQL parameter binding and an atomic delete protect against partial removals.
        marks=','.join('?' for _ in ids)
        try:
            safety=self._safety_backup_before_delete()
        except Exception as exc:
            self.notify(f'Deletion cancelled: safety backup failed ({exc})', 'error', 8000)
            return
        try:
            with connect() as cn:
                paths=[r['path'] for r in cn.execute(f'SELECT path FROM trade_images WHERE trade_id IN ({marks})',ids)]
                cn.execute(f'DELETE FROM trades WHERE id IN ({marks})',ids)
            # Delete files only if no record still references them; backup already holds copies.
            with connect() as cn:
                remaining={r[0] for r in cn.execute('SELECT path FROM trade_images')}
            for p in paths:
                f=Path(p)
                if p not in remaining and f.parent == IMAGES and f.is_file():
                    try:f.unlink()
                    except OSError:pass
            if self.trade_id in ids:self.new_trade()
            self.refresh()
            self.notify(f'{len(ids)} trade(s) deleted  •  Safety backup: {safety.name}',duration=6500)
        except Exception as exc:
            self.notify(f'Deletion failed: {exc}. Backup: {safety.name}','error',8500)

    def delete_selected(self):
        ids=[int(x) for x in self.tree.selection()]
        if not ids:return self.notify('Select one or more trades to delete.', 'error')
        if not messagebox.askyesno('Delete selected trades',
                f'Permanently delete {len(ids)} selected trade(s) and their screenshots?\n\nA ZIP safety backup will be created automatically first.',parent=self):return
        self._delete_trades(ids)

    def delete_all_trades(self):
        with connect() as cn:
            ids=[r[0] for r in cn.execute('SELECT id FROM trades ORDER BY id')]
        if not ids:return self.notify('There are no trades to delete.')
        answer=simpledialog.askstring('Delete ALL trades',
            f'This will delete ALL {len(ids)} trades and their screenshots, including rows hidden by search.\n'
            'A ZIP backup will be created first.\n\nType DELETE ALL to confirm:',parent=self)
        if answer!='DELETE ALL':return self.notify('Delete All cancelled.')
        self._delete_trades(ids)

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
            self.preview_window(p)
        except Exception as e:messagebox.showerror('Image error',str(e))

    def preview_window(self,p):
        try:
            with Image.open(p) as im:
                picture=im.convert('RGBA')
                width=max(300,self.winfo_width()-160)
                height=max(250,self.winfo_height()-190)
                picture.thumbnail((width,height),Image.Resampling.LANCZOS)
                photo=ImageTk.PhotoImage(picture)
        except Exception as exc:
            return self.notify(str(exc),'error')
        overlay,body,finish=self._overlay_shell('SCREENSHOT PREVIEW')
        tk.Label(body,image=photo,bg=PANEL).pack(expand=True,pady=15)
        body._preview_image=photo
        ttk.Button(body,text='Close preview',command=lambda:finish(None)).pack(pady=12)
        self.wait_variable(overlay._finished)

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
