"""Layaq Trading Journal Super App â€” local-first Windows trading journal."""
import csv
import datetime as dt
import os
import shutil
import sqlite3
import sys
import uuid
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = 'Layaq Trading Journal Super App'
BASE = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'LayaqTradingJournal'
IMAGES = BASE / 'images'
DB = BASE / 'journal.sqlite3'
BACKUPS = BASE / 'backups'
BASE.mkdir(parents=True, exist_ok=True)
IMAGES.mkdir(exist_ok=True)
BACKUPS.mkdir(exist_ok=True)

BG, PANEL, ENTRY, TEXT, MUTED, ACCENT, GREEN, RED = '#101622', '#1c2534', '#263248', '#eef3fb', '#9aabc1', '#4aa3ff', '#42ce9b', '#ff687c'
IMAGE_FIELDS = [f'{stage.lower()}_{tf.lower()}' for stage in ('Before', 'After') for tf in ('HTF', 'MTF', 'LTF')]
COLUMNS = ['date', 'symbol', 'market', 'side', 'session', 'strategy', 'entry', 'exit', 'stop', 'target', 'size', 'risk', 'fees', 'pnl', 'emotion', 'setup', 'review']


def connect():
    cn = sqlite3.connect(DB)
    cn.row_factory = sqlite3.Row
    cn.execute('PRAGMA foreign_keys=ON')
    cn.execute('PRAGMA journal_mode=WAL')
    return cn


def setup():
    with connect() as cn:
        cn.execute('''CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, symbol TEXT NOT NULL,
            market TEXT, side TEXT, session TEXT, strategy TEXT, entry REAL,
            exit REAL, stop REAL, target REAL, size REAL, risk REAL, fees REAL,
            pnl REAL, emotion TEXT, setup TEXT, review TEXT, created_at TEXT NOT NULL)''')
        cn.execute('''CREATE TABLE IF NOT EXISTS trade_images (
            trade_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
            slot TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY(trade_id, slot))''')
        cn.execute('CREATE INDEX IF NOT EXISTS ix_trades_date ON trades(date)')
        cn.execute('CREATE INDEX IF NOT EXISTS ix_trades_symbol ON trades(symbol)')


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        setup()
        self.title(APP_NAME)
        self.geometry('1380x840')
        self.minsize(1060, 660)
        self.configure(bg=BG)
        self.trade_id = None
        self.pending_images = {}
        self.active_preview = None
        self._style()
        self._layout()
        self.refresh()

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
        tk.Label(header, text='SUPER APP   â€¢   OFFLINE', font=('Segoe UI', 10), bg=BG, fg=GREEN).pack(side='left', padx=18)
        ttk.Button(header, text='Backup', command=self.backup).pack(side='right', padx=4)
        ttk.Button(header, text='Restore', command=self.restore).pack(side='right', padx=4)
        ttk.Button(header, text='Export CSV', command=self.export_csv).pack(side='right', padx=4)
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True, padx=20, pady=10)
        self.dashboard = ttk.Frame(self.tabs)
        self.history = ttk.Frame(self.tabs)
        self.editor = ttk.Frame(self.tabs)
        self.tabs.add(self.dashboard, text='Dashboard')
        self.tabs.add(self.history, text='Trade History')
        self.tabs.add(self.editor, text='Trade Editor & Charts')
        self._make_dashboard()
        self._make_history()
        self._make_editor()

    def _make_dashboard(self):
        self.stats_frame = ttk.Frame(self.dashboard)
        self.stats_frame.pack(fill='x', pady=(12, 12))
        self.stats = {}
        for name in ('Total Trades', 'Win Rate', 'Net P&L', 'Profit Factor', 'Avg Win / Loss', 'Max Drawdown'):
            box = tk.Frame(self.stats_frame, bg=PANEL, padx=13, pady=12)
            box.pack(side='left', fill='both', expand=True, padx=5)
            tk.Label(box, text=name, bg=PANEL, fg=MUTED).pack(anchor='w')
            value = tk.Label(box, text='â€”', font=('Segoe UI Semibold', 16), bg=PANEL, fg=TEXT)
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
            ttk.Combobox(row, textvariable=kind, values=['Line', 'Bar', 'Area'], state='readonly', width=9).pack(side='left', padx=4)
            kind.trace_add('write', lambda *_a, p=period: self.draw_period(p))
            canvas = tk.Canvas(frame, bg=PANEL, highlightthickness=0)
            canvas.pack(fill='both', expand=True, padx=5, pady=(0, 5))
            self.period_canvases[period] = canvas
            canvas.bind('<Configure>', lambda _e, p=period: self.draw_period(p))
        self.period_tabs.bind('<<NotebookTabChanged>>', lambda _e: self.refresh())
        tk.Label(self.dashboard, text='Charts show realized P&L only. Select a date/week/month/year and choose Line, Bar, or Area.', bg=BG, fg=MUTED).pack(anchor='w', padx=6, pady=8)

    def period_data(self, period):
        rows = [r for r in self._all_trades() if r['pnl'] is not None]
        selected = []
        buckets = {}
        for r in rows:
            try:
                stamp = dt.datetime.fromisoformat(r['date'].strip().replace('Z', '+00:00'))
                day = stamp.date()
                year = int(self.date_vars[(period, 'year')].get()) if period != 'All Time' else 0
                if period == 'Daily':
                    if (day.year, day.month, day.day) != (year, int(self.date_vars[(period, 'month')].get()), int(self.date_vars[(period, 'day')].get())): continue
                    key = f'{stamp.hour:02d}:00'
                elif period == 'Weekly':
                    iso = day.isocalendar()
                    if (iso.year, iso.week) != (year, int(self.date_vars[(period, 'week')].get())):continue
                    key = f'{day.isoweekday()} {day.strftime("%a")}'
                elif period == 'Monthly':
                    if (day.year, day.month) != (year, int(self.date_vars[(period, 'month')].get())):continue
                    key = f'{day.day:02d}'
                elif period == 'Yearly':
                    if day.year != year:continue
                    key = f'{day.month:02d}'
                else:
                    key = day.strftime('%Y-%m')
                selected.append(r)
                buckets[key] = buckets.get(key, 0.0) + r['pnl']
            except (ValueError, TypeError, AttributeError):
                continue
        if period == 'Daily': keys=[f'{x:02d}:00' for x in range(24)]
        elif period == 'Weekly': keys=[f'{i} {['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][i-1]}' for i in range(1,8)]
        elif period == 'Monthly':
            import calendar
            try: count=calendar.monthrange(int(self.date_vars[(period,'year')].get()),int(self.date_vars[(period,'month')].get()))[1]
            except ValueError: count=31
            keys=[f'{x:02d}' for x in range(1,count+1)]
        elif period == 'Yearly': keys=[f'{x:02d}' for x in range(1,13)]
        else:keys=sorted(buckets)
        return selected, keys, [buckets.get(k,0.0) for k in keys]

    def draw_period(self, period):
        if not hasattr(self, 'period_canvases') or period not in self.period_canvases:return
        c=self.period_canvases[period]
        c.delete('all')
        w,h=c.winfo_width(),c.winfo_height()
        if w < 100 or h < 100:return
        rows, labels, values=self.period_data(period)
        cumulative=[]
        total=0.0
        for v in values:
            total+=v
            cumulative.append(total)
        kind=self.date_vars[(period,'chart')].get()
        title=f'{period.upper()}  /  {kind.upper()}  /  REALIZED NET P&L'
        c.create_text(24,23,text=title,anchor='w',fill=TEXT,font=('Segoe UI Semibold',14))
        pad_l,pad_r,pad_top,pad_bottom=70,30,55,63
        if not labels:
            c.create_text(w/2,h/2,text='No closed trades for this period',fill=MUTED,font=('Segoe UI',13))
            return
        # Bar chart shows P&L of each bucket; line and area show cumulative P&L.
        series = values if kind=='Bar' else cumulative
        lo=min([0]+series);hi=max([0]+series)
        if lo==hi:lo-=1;hi+=1
        margin=(hi-lo)*.10
        lo-=margin;hi+=margin
        plot_w=max(10,w-pad_l-pad_r);plot_h=max(10,h-pad_top-pad_bottom)
        def xx(i):return pad_l+(i+.5)*plot_w/max(1,len(labels))
        def yy(v):return pad_top+(hi-v)*plot_h/(hi-lo)
        for j in range(5):
            v=lo+(hi-lo)*j/4;y=yy(v)
            c.create_line(pad_l,y,w-pad_r,y,fill='#344052',dash=(3,5))
            c.create_text(pad_l-10,y,text=f'{v:,.0f}',anchor='e',fill=MUTED)
        zero=yy(0)
        if kind=='Bar':
            width=max(2,min(48,plot_w/max(1,len(labels))*.72))
            for i,v in enumerate(values):
                x=xx(i);y=yy(v)
                c.create_rectangle(x-width/2,min(y,zero),x+width/2,max(y,zero)+1,fill=GREEN if v>=0 else RED,outline='')
        else:
            points=[]
            for i,v in enumerate(series):points.extend([xx(i),yy(v)])
            if kind=='Area' and len(points)>=4:
                c.create_polygon([xx(0),zero]+points+[xx(len(series)-1),zero],fill='#245483',outline='')
            if len(points)>=4:c.create_line(*points,fill=GREEN if total>=0 else RED,width=3)
            elif len(points)==2:c.create_oval(points[0]-3,points[1]-3,points[0]+3,points[1]+3,fill=GREEN,outline='')
        stride=max(1,(len(labels)+11)//12)
        for i,label in enumerate(labels):
            if i%stride==0 or i==len(labels)-1:c.create_text(xx(i),h-42,text=label,fill=MUTED,font=('Segoe UI',9))
        c.create_text(w-pad_r,h-15,anchor='e',text=f'{len(rows)} closed trades  â€¢  Net P&L: {sum(values):+,.2f}',fill=TEXT)

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
        self.editor_title.pack(anchor='w', pady=(0, 12))
        form = ttk.Frame(left)
        form.pack(fill='x')
        self.fields = {}
        config = [
            ('date', 'Date / time', 'entry'), ('symbol','Symbol','entry'), ('market','Market','combo'),
            ('side','Side','combo'), ('session','Session','combo'), ('strategy','Strategy','entry'),
            ('entry','Entry price','entry'), ('exit','Exit price','entry'), ('stop','Stop loss','entry'),
            ('target','Take profit','entry'), ('size','Position size / lots','entry'),
            ('risk','Risk amount','entry'), ('fees','Fees / commission','entry'),
            ('pnl','Realized net P&L','entry'), ('emotion','Emotion','combo')]
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
        actions = ttk.Frame(left)
        actions.pack(fill='x', pady=14)
        ttk.Button(actions, text='Save Trade', style='Accent.TButton', command=self.save_trade).pack(side='left', padx=3)
        ttk.Button(actions, text='Clear / New', command=self.new_trade).pack(side='left', padx=3)
        ttk.Label(right, text='CHART EVIDENCE  â€¢  BEFORE / AFTER', font=('Segoe UI Semibold', 17)).pack(anchor='w', pady=(0, 6))
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
            'Win Rate': f'{len(wins)/len(pnls)*100:.1f}%' if pnls else 'â€”',
            'Net P&L': f'{sum(pnls):+,.2f}',
            'Profit Factor': f'{grosswin/grossloss:.2f}' if grossloss else ('âˆž' if grosswin else 'â€”'),
            'Avg Win / Loss': f'{grosswin/len(wins):,.2f} / {grossloss/len(losses):,.2f}' if wins and losses else 'â€”',
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
        if not values['symbol']:return messagebox.showwarning('Required','Enter a symbol.')
        try:dt.datetime.strptime(values['date'],'%Y-%m-%d %H:%M')
        except ValueError:return messagebox.showwarning('Invalid date','Use YYYY-MM-DD HH:MM.')
        for k in ('entry','exit','stop','target','size','risk','fees','pnl'):
            try:values[k]=float(values[k]) if values[k] else None
            except ValueError:return messagebox.showwarning('Invalid number',f'{k} must be a number.')
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
        messagebox.showinfo('Saved',f'Trade #{tid} saved locally.')

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
        messagebox.showinfo('Exported',f'CSV exported to:\n{out}')

    def backup(self):
        dest=filedialog.asksaveasfilename(defaultextension='.zip',initialfile=f'layaq_backup_{dt.date.today().isoformat()}.zip',filetypes=[('ZIP archive','*.zip')])
        if not dest:return
        temp=BASE/f'_backup_{uuid.uuid4().hex}.sqlite3'
        with connect() as source, sqlite3.connect(temp) as target:source.backup(target)
        try:
            with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED) as z:
                z.write(temp,'journal.sqlite3')
                for file in IMAGES.glob('*'):
                    if file.is_file():z.write(file,'images/'+file.name)
        finally:temp.unlink(missing_ok=True)
        messagebox.showinfo('Backup completed','Database and screenshots were included in the backup archive.')

    def restore(self):
        archive=filedialog.askopenfilename(filetypes=[('ZIP archive','*.zip')])
        if not archive:return
        if not messagebox.askyesno('Restore backup','This replaces the current journal and images. Please keep a separate backup first. Continue?'):return
        staging=BASE/f'_restore_{uuid.uuid4().hex}'
        staging.mkdir()
        old_db=BASE/f'_old_{uuid.uuid4().hex}.sqlite3'
        old_images=BASE/f'_old_images_{uuid.uuid4().hex}'
        try:
            with zipfile.ZipFile(archive) as z:
                names=z.namelist()
                if 'journal.sqlite3' not in names:raise ValueError('Not a Layaq backup: journal.sqlite3 is missing.')
                if any(n.startswith('/') or '\\' in n or '..' in Path(n).parts for n in names):raise ValueError('Unsafe file path in archive.')
                if z.testzip() is not None:raise ValueError('Backup ZIP contains a corrupted file.')
                staged_db=staging/'journal.sqlite3'
                staged_db.write_bytes(z.read('journal.sqlite3'))
                image_dir=staging/'images';image_dir.mkdir()
                for n in names:
                    if n.startswith('images/') and not n.endswith('/'):
                        if Path(n).name != n[7:]:raise ValueError('Unexpected nested screenshot path.')
                        (image_dir/Path(n).name).write_bytes(z.read(n))
            # Verify the backup before touching any active journal files.
            with sqlite3.connect(staged_db) as restored:
                if restored.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Database integrity check failed.')
                tables={r[0] for r in restored.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'trades','trade_images'}.issubset(tables):raise ValueError('Not a compatible Layaq journal backup.')
                for tid,slot,old_path in restored.execute('SELECT trade_id,slot,path FROM trade_images').fetchall():
                    filename=Path(str(old_path).replace('\\','/')).name
                    if not (image_dir/filename).is_file():raise ValueError(f'Backup missing screenshot: {filename}')
                    restored.execute('UPDATE trade_images SET path=? WHERE trade_id=? AND slot=?',(str(IMAGES/filename),tid,slot))
                restored.commit()
                # WAL -> DELETE: guarantees a single self-contained database file.
                restored.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                restored.execute('PRAGMA journal_mode=DELETE')
            # SQLite connection from connect() uses WAL; checkpoint and close before swapping.
            with connect() as current:current.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            if DB.exists():shutil.copy2(DB,old_db)
            shutil.copytree(IMAGES,old_images)
            try:
                os.replace(staged_db,DB)
                for sidecar in ('-wal','-shm'):Path(str(DB)+sidecar).unlink(missing_ok=True)
                for f in IMAGES.iterdir():
                    if f.is_file():f.unlink()
                for f in image_dir.iterdir():shutil.copy2(f,IMAGES/f.name)
                setup()
            except Exception:
                if old_db.exists():os.replace(old_db,DB)
                if old_images.exists():
                    shutil.rmtree(IMAGES,ignore_errors=True)
                    shutil.copytree(old_images,IMAGES)
                raise
            self.new_trade();self.refresh()
            messagebox.showinfo('Restored','Your trades and all six screenshot slots were restored successfully.')
        except Exception as e:
            messagebox.showerror('Restore failed',str(e))
        finally:
            shutil.rmtree(staging,ignore_errors=True)
            old_db.unlink(missing_ok=True)
            shutil.rmtree(old_images,ignore_errors=True)

if __name__=='__main__':
    App().mainloop()
