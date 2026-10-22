"""Layaq Trading Journal Super App — local-first Windows trading journal."""
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
        tk.Label(header, text='SUPER APP   •   OFFLINE', font=('Segoe UI', 10), bg=BG, fg=GREEN).pack(side='left', padx=18)
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
        self.stats_frame.pack(fill='x', pady=(12, 18))
        self.stats = {}
        for name in ('Total Trades', 'Win Rate', 'Net P&L', 'Profit Factor', 'Avg Win / Loss', 'Max Drawdown'):
            box = tk.Frame(self.stats_frame, bg=PANEL, padx=13, pady=16)
            box.pack(side='left', fill='both', expand=True, padx=5)
            tk.Label(box, text=name, bg=PANEL, fg=MUTED).pack(anchor='w')
            value = tk.Label(box, text='—', font=('Segoe UI Semibold', 17), bg=PANEL, fg=TEXT)
            value.pack(anchor='w', pady=(8, 0))
            self.stats[name] = value
        charts = ttk.Frame(self.dashboard)
        charts.pack(fill='both', expand=True)
        self.graph = tk.Canvas(charts, bg=PANEL, highlightthickness=0)
        self.graph.pack(fill='both', expand=True, padx=5)
        self.graph.bind('<Configure>', lambda e: self.draw_equity())
        tk.Label(self.dashboard, text='P&L is entered as realized account-currency P&L. Open trades are excluded from win/loss statistics.', bg=BG, fg=MUTED).pack(anchor='w', padx=6, pady=12)

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
        rows = [r for r in self._all_trades() if r['pnl'] is not None]
        pnls = [r['pnl'] for r in rows]
        wins = [x for x in pnls if x > 0]
        losses = [x for x in pnls if x < 0]
        grosswin = sum(wins)
        grossloss = abs(sum(losses))
        equity = 0.0
        peak = 0.0
        maxdd = 0.0
        for x in pnls:
            equity += x
            peak = max(peak, equity)
            maxdd = max(maxdd, peak - equity)
        stats = {
            'Total Trades': str(len(self._all_trades())),
            'Win Rate': f'{len(wins)/len(pnls)*100:.1f}%' if pnls else '—',
            'Net P&L': f'{sum(pnls):+,.2f}',
            'Profit Factor': f'{grosswin/grossloss:.2f}' if grossloss else ('∞' if grosswin else '—'),
            'Avg Win / Loss': f'{grosswin/len(wins):,.2f} / {grossloss/len(losses):,.2f}' if wins and losses else '—',
            'Max Drawdown': f'{maxdd:,.2f}' }
        for name, val in stats.items():self.stats[name].config(text=val)
        self.draw_equity()

    def draw_equity(self):
        if not hasattr(self, 'graph'):return
        c = self.graph
        c.delete('all')
        w,h = c.winfo_width(),c.winfo_height()
        if w<60 or h<60:return
        pnls = [r['pnl'] for r in self._all_trades() if r['pnl'] is not None]
        totals=[0.0]
        for p in pnls:totals.append(totals[-1]+p)
        low,high=min(totals),max(totals)
        if high==low:high+=1;low-=1
        pad=65
        def xy(i,n):return (pad+i*(w-2*pad)/max(1,len(totals)-1), h-pad-(n-low)*(h-2*pad)/(high-low))
        c.create_text(24,20,text='EQUITY CURVE  /  REALIZED P&L',anchor='w',fill=TEXT,font=('Segoe UI Semibold',14))
        for j in range(5):
            v=low+(high-low)*j/4
            y=xy(0,v)[1]
            c.create_line(pad,y,w-pad,y,fill='#344052',dash=(3,5))
            c.create_text(pad-8,y,text=f'{v:,.0f}',anchor='e',fill=MUTED)
        points=[]
        for i, v in enumerate(totals):points.extend(xy(i,v))
        if len(points)>2:c.create_line(*points,fill=GREEN if totals[-1]>=0 else RED,width=3,smooth=False)
        c.create_text(w-pad,h-24,text=f'{len(pnls)} closed trades',anchor='e',fill=MUTED)

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
        if not messagebox.askyesno('Restore backup','This will replace all CURRENT journal data and images. Make a backup first. Continue?'):return
        with zipfile.ZipFile(archive) as z:
            names=z.namelist()
            if 'journal.sqlite3' not in names:return messagebox.showerror('Invalid backup','Missing journal.sqlite3')
            if any(n.startswith('/') or '..' in Path(n).parts for n in names):return messagebox.showerror('Invalid archive','Unsafe file path.')
            raw=z.read('journal.sqlite3')
            tmp=BASE/'restore_temp.sqlite3';tmp.write_bytes(raw)
            try:
                check=sqlite3.connect(tmp)
                ok=check.execute('PRAGMA integrity_check').fetchone()[0]
                check.close()
                if ok!='ok':raise ValueError(ok)
                # Backup archives may be restored on a different Windows PC.
                with sqlite3.connect(tmp) as restored:
                    for slot_row in restored.execute('SELECT trade_id,slot,path FROM trade_images').fetchall():
                        tid,slot,old_path=slot_row
                        new_path=str(IMAGES/Path(old_path.replace('\\','/')).name)
                        restored.execute('UPDATE trade_images SET path=? WHERE trade_id=? AND slot=?',(new_path,tid,slot))
                for p in IMAGES.glob('*'):
                    if p.is_file():p.unlink()
                for name in names:
                    if name.startswith('images/') and not name.endswith('/'):
                        p=IMAGES/Path(name).name
                        p.write_bytes(z.read(name))
                for f in (Path(str(DB)+'-wal'),Path(str(DB)+'-shm')):f.unlink(missing_ok=True)
                os.replace(tmp,DB)
            except Exception as e:
                tmp.unlink(missing_ok=True)
                return messagebox.showerror('Restore failed',str(e))
        self.new_trade();self.refresh()
        messagebox.showinfo('Restored','Backup restored successfully.')

if __name__=='__main__':
    App().mainloop()
