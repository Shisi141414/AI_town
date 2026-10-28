"""本地过程档案：有序事件、模型请求、记忆变化和可直接显示的状态帧。"""
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

REQUEST_TRACE = ContextVar('ai_town_request_trace', default=None)
VALID_ID = re.compile(r'\d{8}-\d{6}-[a-f0-9]{8}')


class Recorder:
    def __init__(self, directory: Path, initial):
        directory.mkdir(parents=True, exist_ok=True)
        self.id=datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        self.db=sqlite3.connect(directory/(self.id+'.sqlite3'),check_same_thread=False)
        self.lock=threading.RLock()
        self.closed=False
        self.frame_count=0
        self.db.execute('CREATE TABLE records (seq INTEGER PRIMARY KEY, kind TEXT, wall REAL, data TEXT)')
        self.db.execute('CREATE INDEX record_kind ON records(kind,seq)')
        self.append('header',{'version':1,'started':time.time(),'initial':initial})

    def append(self, kind, data):
        with self.lock:
            if not self.closed:
                if kind=="frame":self.frame_count+=1
                with self.db:
                    self.db.execute('INSERT INTO records(kind,wall,data) VALUES(?,?,?)',
                                    (kind,time.time(),json.dumps(data,ensure_ascii=False)))

    def close(self):
        with self.lock:
            if not self.closed:
                self.append('end',{})
                self.closed=True
                self.db.close()


@contextmanager
def open_recording(directory: Path, rid: str):
    if not VALID_ID.fullmatch(rid):
        raise ValueError('无效录像编号')
    path=directory/(rid+'.sqlite3')
    if not path.is_file():
        raise FileNotFoundError('未找到过程档案')
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        yield db
    finally:
        db.close()


def recordings(directory):
    if not directory.exists():return []
    result=[]
    for path in sorted(directory.glob('*.sqlite3'),reverse=True):
        try:
            with open_recording(directory,path.stem) as db:
                row=db.execute("SELECT data FROM records WHERE kind='header' LIMIT 1").fetchone()
                count=db.execute("SELECT COUNT(*) FROM records WHERE kind='frame'").fetchone()[0]
                header=json.loads(row[0])
                result.append({'id':path.stem,'started':header['started'],'frames':count,'bytes':path.stat().st_size})
        except (ValueError,sqlite3.Error,TypeError):
            continue
    return result


def read_frame(directory,rid,index):
    with open_recording(directory,rid) as db:
        total=db.execute("SELECT COUNT(*) FROM records WHERE kind='frame'").fetchone()[0]
        if not 0<=index<total:raise IndexError('回放位置超出范围')
        seq,data=db.execute("SELECT seq,data FROM records WHERE kind='frame' ORDER BY seq LIMIT 1 OFFSET ?",(index,)).fetchone()
        frame=json.loads(data)
        events=db.execute("SELECT data FROM records WHERE kind='event' AND seq<=? ORDER BY seq DESC LIMIT 500",(seq,)).fetchall()
        frame['events']=[json.loads(r[0]) for r in reversed(events)]
        frame['running']=False
        return {'index':index,'total':total,'frame':frame}
