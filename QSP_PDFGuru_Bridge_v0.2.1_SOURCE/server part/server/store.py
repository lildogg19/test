import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

def now():
    return datetime.now(timezone.utc).isoformat()

class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'uploads').mkdir(exist_ok=True)
        self.db = self.root / 'bridge.sqlite3'
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS jobs (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
              base TEXT NOT NULL, actor TEXT NOT NULL, request_key TEXT NOT NULL,
              filename TEXT NOT NULL, source TEXT NOT NULL, digest TEXT NOT NULL,
              status TEXT NOT NULL, stage TEXT NOT NULL, created TEXT NOT NULL,
              updated TEXT NOT NULL, error TEXT, result TEXT, matches TEXT,
              UNIQUE(base,request_key));
            CREATE TABLE IF NOT EXISTS refs (base TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mappings (
              base TEXT NOT NULL, supplier TEXT NOT NULL, source_key TEXT NOT NULL,
              target TEXT NOT NULL, PRIMARY KEY(base,supplier,source_key));
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            c.execute('PRAGMA foreign_keys=ON')
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def create(self, base, actor, request_key, filename, source, digest):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            existing = c.execute('SELECT * FROM jobs WHERE base=? AND request_key=?', (base,request_key)).fetchone()
            if existing:
                if existing['digest'] != digest or existing['actor'] != actor:
                    raise ValueError('Ключ запроса уже использован для другого файла или пользователя')
                return dict(existing), False
            job_id = uuid.uuid4().hex
            c.execute('INSERT INTO jobs(id,base,actor,request_key,filename,source,digest,status,stage,created,updated) VALUES(?,?,?,?,?,?,?, ?,?,?,?)',
                      (job_id,base,actor,request_key,filename,str(source),digest,'queued','В очереди',now(),now()))
            return dict(c.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()), True

    def get(self, job_id, base):
        with self.connect() as c:
            row = c.execute('SELECT * FROM jobs WHERE id=? AND base=?', (job_id,base)).fetchone()
            return dict(row) if row else None

    def claim(self):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM jobs WHERE status='processing'").fetchone():
                return None
            row = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY seq LIMIT 1").fetchone()
            if not row:
                return None
            c.execute("UPDATE jobs SET status='processing',stage='Запуск браузера',updated=? WHERE id=?", (now(),row['id']))
            return dict(c.execute('SELECT * FROM jobs WHERE id=?',(row['id'],)).fetchone())

    def update(self, job_id, **fields):
        allowed = {'status','stage','error','result','matches'}
        if not fields.keys() <= allowed:
            raise ValueError('Unsupported field')
        fields['updated'] = now()
        with self.connect() as c:
            c.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),job_id))

    def recover(self):
        with self.connect() as c:
            c.execute("UPDATE jobs SET status='failed', stage='Обработка прервана перезапуском',error='interrupted', updated=? WHERE status='processing'",(now(),))

    def queue(self, base):
        with self.connect() as c:
            rows = c.execute("SELECT id,base,actor,filename,status,stage,seq,created FROM jobs WHERE status IN ('queued','processing','awaiting_login') OR seq IN (SELECT seq FROM jobs WHERE base=? ORDER BY seq DESC LIMIT 50) ORDER BY seq",(base,)).fetchall()
            state = c.execute("SELECT value FROM state WHERE key='worker'").fetchone()
        waiting = 0
        items = []
        for row in rows:
            item = dict(row)
            if item['status'] == 'queued':
                waiting += 1
            if item['base'] == base:
                item['position'] = waiting if item['status'] == 'queued' else 0
                item.pop('base'); item.pop('seq')
                items.append(item)
        return {'worker': json.loads(state['value']) if state else {'status':'offline','message':'Обработчик не запущен'},
                'jobs':items,'global_waiting':waiting,'processing':any(r['status']=='processing' for r in rows)}

    def worker_state(self, status, message):
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO state VALUES(?,?)',('worker',json.dumps({'status':status,'message':message,'updated':now()},ensure_ascii=False)))

    def set_refs(self, base, refs):
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO refs VALUES(?,?)',(base,json.dumps(refs,ensure_ascii=False)))

    def refs(self, base):
        with self.connect() as c:
            row = c.execute('SELECT payload FROM refs WHERE base=?',(base,)).fetchone()
        return json.loads(row['payload']) if row else {'suppliers':[],'organizations':[],'items':[]}

    def mappings(self, base, supplier):
        with self.connect() as c:
            return dict(c.execute('SELECT source_key,target FROM mappings WHERE base=? AND supplier=?',(base,supplier)).fetchall())

    def save_mapping(self, base, supplier, source_key, target):
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO mappings VALUES(?,?,?,?)',(base,supplier,source_key,target))

    def retry(self, job_id, base):
        with self.connect() as c:
            c.execute("UPDATE jobs SET status='queued',stage='В очереди (повтор)',error=NULL,result=NULL,matches=NULL,updated=? WHERE id=? AND base=? AND status IN ('failed','awaiting_login','completed')",(now(),job_id,base))
            return c.total_changes == 1

