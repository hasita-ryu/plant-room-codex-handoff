import os, json, base64, hashlib, hmac, secrets, sqlite3, re, time, asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv('PLANT_ROOM_DATA_DIR', str(ROOT / 'data')))
DB_PATH = DATA_DIR / 'app.db'
UPLOADS = Path(os.getenv('PLANT_ROOM_UPLOADS_DIR', str(ROOT / 'uploads')))
ASSETS = ROOT / 'public' / 'assets'
UPLOADS.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SECRET = os.getenv('APP_SECRET', 'local-dev-secret-change-me').encode()
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_TEXT_MODEL = os.getenv('OPENAI_TEXT_MODEL', 'gpt-5.6-luna')
OPENAI_IMAGE_MODEL = os.getenv('OPENAI_IMAGE_MODEL', 'gpt-image-2')
OPENAI_BASE_URL = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
SINGLE_USER_MODE = os.getenv('SINGLE_USER_MODE', '0') == '1'
FURNITURE_KEYS = {
    'bed', 'desk', 'chair', 'shelf', 'small_shelf', 'side_table',
    'plant_stand', 'wall_shelf', 'dresser', 'floor_lamp', 'wall_picture'
}
GRID_WIDTH = 16
GRID_HEIGHT = 10
FLOOR_ROW = 8
NO_ANCHOR_ROWS = {6, 7, 9}
WALL_FURNITURE = {'shelf', 'small_shelf', 'wall_shelf', 'wall_picture'}
WINDOW_BLOCK = {'x1': 2, 'x2': 5, 'y1': 1, 'y2': 4}
DOOR_BLOCK = {'x1': 0, 'x2': 1, 'y1': 5, 'y2': 8}
FURNITURE_FOOTPRINTS = {
    'bed': (-1, 0, 1, 0),
    'desk': (-1, 0, 1, 0),
    'chair': (0, 0, 0, 0),
    'shelf': (-1, 0, 1, 0),
    'small_shelf': (0, 0, 0, 0),
    'side_table': (0, 0, 0, 0),
    'plant_stand': (0, 0, 0, 0),
    'wall_shelf': (-1, 0, 1, 0),
    'dresser': (-1, 0, 0, 0),
    'floor_lamp': (0, 0, 0, 0),
    'wall_picture': (0, 0, 0, 0),
}
INITIAL_FURNITURE = [
    ('bed', 3, FLOOR_ROW), ('plant_stand', 5, FLOOR_ROW), ('side_table', 7, FLOOR_ROW),
    ('desk', 10, FLOOR_ROW), ('chair', 12, FLOOR_ROW), ('floor_lamp', 13, FLOOR_ROW),
    ('dresser', 15, FLOOR_ROW), ('wall_picture', 7, 2), ('wall_shelf', 8, 4),
    ('shelf', 12, 2), ('small_shelf', 5, 5)
]
INITIAL_FURNITURE_POSITIONS = {key: (x, y) for key, x, y in INITIAL_FURNITURE}

app = FastAPI(title='Plant Room MVP')
app.mount('/static', StaticFiles(directory=str(ROOT/'public')), name='static')
app.mount('/uploads', StaticFiles(directory=str(UPLOADS)), name='uploads')

@app.middleware('http')
async def no_cache_static_assets(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


def now_iso():
    return datetime.now(timezone.utc).isoformat()

def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def init_db():
    c=conn()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      email TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL,
      display_name TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS plants(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      name TEXT NOT NULL,
      normalized_name TEXT NOT NULL,
      count INTEGER NOT NULL DEFAULT 1,
      dead_count INTEGER NOT NULL DEFAULT 0,
      photo_path TEXT,
      humidity_type TEXT NOT NULL DEFAULT '一般的な室内湿度で管理',
      watering_interval_days INTEGER NOT NULL DEFAULT 7,
      last_watered_at TEXT,
      next_water_at TEXT,
      care_note TEXT DEFAULT '',
      sprite_base TEXT DEFAULT '/static/assets/plant_demo',
      deleted_at TEXT,
      created_at TEXT NOT NULL,
      UNIQUE(user_id, normalized_name),
      FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS placements(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      plant_id INTEGER,
      item_type TEXT NOT NULL CHECK(item_type IN ('plant','dead_plant','furniture')),
      furniture_key TEXT,
      instance_index INTEGER DEFAULT 1,
      area TEXT NOT NULL DEFAULT 'room',
      x INTEGER NOT NULL,
      y INTEGER NOT NULL,
      placement_type TEXT NOT NULL DEFAULT 'floor',
      hang_depth INTEGER NOT NULL DEFAULT 1,
      color_variant TEXT DEFAULT 'wood',
      created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id),
      FOREIGN KEY(plant_id) REFERENCES plants(id)
    );
    CREATE TABLE IF NOT EXISTS watering_events(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      plant_id INTEGER NOT NULL,
      watered_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id),
      FOREIGN KEY(plant_id) REFERENCES plants(id)
    );
    CREATE TABLE IF NOT EXISTS inventory_events(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      plant_id INTEGER NOT NULL,
      delta INTEGER NOT NULL,
      reason TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id),
      FOREIGN KEY(plant_id) REFERENCES plants(id)
    );
    ''')
    seed_initial_furniture(c)
    normalize_room_layout(c)
    c.commit(); c.close()

def furniture_placement_type(key):
    return 'wall' if key in WALL_FURNITURE else 'floor'

def seed_initial_furniture(c):
    users = c.execute('SELECT id FROM users').fetchall()
    for user in users:
        uid = user['id']
        for key, x, y in INITIAL_FURNITURE:
            exists = c.execute(
                "SELECT id FROM placements WHERE user_id=? AND item_type='furniture' AND furniture_key=? LIMIT 1",
                (uid, key)
            ).fetchone()
            if not exists:
                c.execute(
                    "INSERT INTO placements(user_id,item_type,furniture_key,x,y,placement_type,color_variant,created_at) VALUES(?,?,?,?,?,?, 'wood',?)",
                    (uid, 'furniture', key, x, y, furniture_placement_type(key), now_iso())
                )

def normalize_room_layout(c):
    c.execute("DELETE FROM placements WHERE item_type='furniture' AND furniture_key NOT IN (%s)" % (
        ','.join('?' for _ in FURNITURE_KEYS)
    ), tuple(FURNITURE_KEYS))
    for key, (x, y) in INITIAL_FURNITURE_POSITIONS.items():
        c.execute(
            """UPDATE placements
               SET x=?, y=?, placement_type=?
               WHERE item_type='furniture' AND furniture_key=? AND COALESCE(instance_index,1)=1""",
            (x, y, furniture_placement_type(key), key)
        )


def hash_pw(password:str)->str:
    salt=secrets.token_bytes(16)
    dk=hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 160_000)
    return base64.urlsafe_b64encode(salt+dk).decode()

def verify_pw(password:str, stored:str)->bool:
    raw=base64.urlsafe_b64decode(stored.encode()); salt,expected=raw[:16],raw[16:]
    got=hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 160_000)
    return hmac.compare_digest(got, expected)

def sign_session(uid:int)->str:
    payload=f'{uid}:{int(time.time())}'
    sig=hmac.new(SECRET,payload.encode(),hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f'{payload}:{sig}'.encode()).decode()

def parse_session(token:Optional[str])->Optional[int]:
    if not token: return None
    try:
        raw=base64.urlsafe_b64decode(token.encode()).decode()
        uid,ts,sig=raw.split(':')
        payload=f'{uid}:{ts}'
        expected=hmac.new(SECRET,payload.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig,expected): return None
        if int(time.time())-int(ts) > 60*60*24*30: return None
        return int(uid)
    except Exception: return None

def current_user(request:Request)->sqlite3.Row:
    uid=parse_session(request.cookies.get('plant_session'))
    if not uid: raise HTTPException(401,'ログインしてください')
    c=conn(); u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); c.close()
    if not u: raise HTTPException(401,'ログインしてください')
    return u

def norm_name(s:str)->str:
    return re.sub(r'\s+','',s.strip().lower())

def local_sprite_base(name: str) -> str:
    n = norm_name(name)
    if any(k in n for k in ['pothos', 'ポトス']):
        return '/static/assets/preset_plants/pothos'
    if any(k in n for k in ['ivy', 'アイビー', 'ヘデラ']):
        return '/static/assets/preset_plants/ivy'
    if any(k in n for k in ['monstera', 'モンステラ']):
        return '/static/assets/preset_plants/monstera'
    return '/static/assets/plant_demo'

def calc_status(row):
    if not row['next_water_at']: return 'normal'
    try:
        due=datetime.fromisoformat(row['next_water_at'])
        if due.tzinfo is None: due=due.replace(tzinfo=timezone.utc)
        return 'thirsty' if datetime.now(timezone.utc)>=due else 'normal'
    except: return 'normal'

def plant_json(r):
    d=dict(r); d['water_status']=calc_status(r)
    return d

def placement_layer(item_type, furniture_key, placement_type):
    if item_type == 'furniture':
        return 'wall' if furniture_key in WALL_FURNITURE else 'floor'
    return placement_type if placement_type in {'floor', 'wall', 'hanging'} else 'floor'

def placement_cells(item_type, furniture_key, placement_type, x, y):
    layer = placement_layer(item_type, furniture_key, placement_type)
    if item_type == 'furniture':
        dx1, dy1, dx2, dy2 = FURNITURE_FOOTPRINTS.get(furniture_key, (0, 0, 0, 0))
    else:
        dx1, dy1, dx2, dy2 = (0, 0, 0, 0)
    cells = set()
    for gx in range(x + dx1, x + dx2 + 1):
        for gy in range(y + dy1, y + dy2 + 1):
            cells.add((layer, gx, gy))
    return cells

def row_cells(row, x=None, y=None, placement_type=None):
    return placement_cells(
        row['item_type'],
        row['furniture_key'],
        placement_type if placement_type is not None else row['placement_type'],
        int(row['x'] if x is None else x),
        int(row['y'] if y is None else y),
    )

def assert_grid_cells_in_bounds(cells):
    for _, x, y in cells:
        if x < 0 or x >= GRID_WIDTH or y < 0 or y >= GRID_HEIGHT:
            raise HTTPException(400, '部屋の範囲外です')

def rect_contains(rect, x, y):
    return rect['x1'] <= x <= rect['x2'] and rect['y1'] <= y <= rect['y2']

def cells_intersect_rect(cells, rect):
    return any(rect_contains(rect, x, y) for _, x, y in cells)

def assert_layer_rules(item_type, furniture_key, placement_type, x, y, cells):
    layer = placement_layer(item_type, furniture_key, placement_type)
    if y in NO_ANCHOR_ROWS:
        raise HTTPException(400, 'そこには置けません')
    if cells_intersect_rect(cells, DOOR_BLOCK):
        raise HTTPException(400, 'ドアの前には置けません')
    if layer == 'floor':
        if y != FLOOR_ROW:
            raise HTTPException(400, '接地ラインに置いてください')
        if x < 2:
            raise HTTPException(400, 'ドアの前には置けません')
    elif layer == 'wall':
        if cells_intersect_rect(cells, WINDOW_BLOCK):
            raise HTTPException(400, '窓には重ねられません')
        if not ((6 <= x <= 15 and 1 <= y <= 5) or (2 <= x <= 5 and y == 5)):
            raise HTTPException(400, '壁に置ける範囲を選んでください')
    elif layer == 'hanging':
        if cells_intersect_rect(cells, WINDOW_BLOCK):
            raise HTTPException(400, '窓には重ねられません')
        if not (6 <= x <= 14 and 0 <= y <= 2):
            raise HTTPException(400, '吊るせる範囲を選んでください')

def assert_space_available(c, uid, item_type, furniture_key, placement_type, x, y, exclude_id=None):
    cells = placement_cells(item_type, furniture_key, placement_type, x, y)
    assert_grid_cells_in_bounds(cells)
    assert_layer_rules(item_type, furniture_key, placement_type, x, y, cells)
    rows = c.execute('''
        SELECT p.*
        FROM placements p
        LEFT JOIN plants pl ON p.plant_id=pl.id
        WHERE p.user_id=? AND (pl.deleted_at IS NULL OR p.plant_id IS NULL)
    ''', (uid,)).fetchall()
    for row in rows:
        if exclude_id is not None and row['id'] == exclude_id:
            continue
        if cells & row_cells(row):
            raise HTTPException(400, 'そこには置けません。ほかの家具や植物と重なります')


def fallback_care(name:str):
    n=name.lower()
    # Conservative, simple categories for offline MVP. Production path uses API.
    if any(k in n for k in ['苔','コケ','シダ','モス']): return {'watering_interval_days':3,'humidity_type':'高湿度を好む','care_note':'乾燥させすぎないように管理'}
    if any(k in n for k in ['サボテン','多肉','アロエ']): return {'watering_interval_days':14,'humidity_type':'低湿度でも管理しやすい','care_note':'過湿を避ける'}
    if any(k in n for k in ['ポトス','シェフレラ','フィカス','モンステラ']): return {'watering_interval_days':7,'humidity_type':'一般的な室内湿度で管理','care_note':'土の乾き具合も確認'}
    return {'watering_interval_days':7,'humidity_type':'一般的な室内湿度で管理','care_note':'植物の状態を見ながら調整'}

async def ai_care(name:str, photo_bytes:bytes, mime:str):
    if not OPENAI_API_KEY: return fallback_care(name)
    b64=base64.b64encode(photo_bytes).decode()
    prompt=f'''植物名「{name}」と写真を参考に、室内管理用の情報を決めてください。必要ならweb検索を使ってください。
水やり間隔は「ユーザーがこのアプリの通知を見てから水やりする」前提の安全側の目安です。
湿度は数値ではなく、低湿度・一般的な室内湿度・高湿度のどれが向くかを分類してください。
種の同定に写真だけでは確信が持てない場合も、入力された植物名を優先して過度に断定しないでください。'''
    schema={
      'type':'object',
      'properties':{
        'watering_interval_days':{'type':'integer','minimum':1,'maximum':60},
        'humidity_type':{'type':'string','enum':['低湿度でも管理しやすい','一般的な室内湿度で管理','高湿度を好む']},
        'care_note':{'type':'string','maxLength':60}
      },
      'required':['watering_interval_days','humidity_type','care_note'],
      'additionalProperties':False
    }
    payload={
      'model':OPENAI_TEXT_MODEL,
      'tools':[{'type':'web_search'}],
      'input':[{'role':'user','content':[{'type':'input_text','text':prompt},{'type':'input_image','image_url':f'data:{mime};base64,{b64}'}]}],
      'text':{'format':{'type':'json_schema','name':'plant_care','strict':True,'schema':schema}}
    }
    try:
      async with httpx.AsyncClient(timeout=90) as client:
        r=await client.post(f'{OPENAI_BASE_URL}/responses',headers={'Authorization':f'Bearer {OPENAI_API_KEY}','Content-Type':'application/json'},json=payload)
        r.raise_for_status(); data=r.json()
      text=data.get('output_text') or ''
      if not text:
          chunks=[]
          for out in data.get('output',[]):
              for content in out.get('content',[]):
                  if content.get('type') in ('output_text','text'): chunks.append(content.get('text',''))
          text=''.join(chunks)
      obj=json.loads(text)
      days=max(1,min(60,int(obj['watering_interval_days'])))
      hum=obj['humidity_type']
      allowed=['低湿度でも管理しやすい','一般的な室内湿度で管理','高湿度を好む']
      if hum not in allowed: hum='一般的な室内湿度で管理'
      return {'watering_interval_days':days,'humidity_type':hum,'care_note':str(obj.get('care_note',''))[:60]}
    except Exception:
      return fallback_care(name)

async def ai_sprite(name:str, state:str, mode:str)->Optional[bytes]:
    if not OPENAI_API_KEY: return None
    mode_ja={'floor':'鉢植えで床や棚に置く','wall':'壁掛け鉢','hanging':'天井から吊るす'}[mode]
    state_ja={'normal':'元気','thirsty':'水やりが必要で少ししおれている','dead':'完全に枯れている'}[state]
    prompt=f'''「{name}」の{state_ja}状態、{mode_ja}の植物アイテム。1990年代の16bit生活シミュレーションゲーム風ピクセルアート。正面視点、輪郭明瞭、低い色数、アンチエイリアスなし、単体アイテム、背景完全透過、影は最小限。同じゲーム内家具と並べても自然な縮尺。文字なし。'''
    payload={'model':OPENAI_IMAGE_MODEL,'prompt':prompt,'size':'1024x1024','background':'transparent','output_format':'png'}
    try:
      async with httpx.AsyncClient(timeout=180) as client:
        r=await client.post(f'{OPENAI_BASE_URL}/images/generations',headers={'Authorization':f'Bearer {OPENAI_API_KEY}','Content-Type':'application/json'},json=payload)
        r.raise_for_status(); data=r.json()
      item=(data.get('data') or [{}])[0]
      if item.get('b64_json'): return base64.b64decode(item['b64_json'])
    except Exception:
      return None
    return None

@app.on_event('startup')
def startup(): init_db()

@app.get('/')
def root(): return FileResponse(ROOT/'public'/'index.html')

@app.get('/healthz')
def healthz():
    try:
        c=conn(); c.execute('SELECT 1').fetchone(); c.close()
        return {'ok': True}
    except Exception as e:
        raise HTTPException(503, f'health check failed: {e}')

@app.post('/api/register')
async def register(request:Request):
    body=await request.json(); email=body.get('email','').strip().lower(); pw=body.get('password',''); name=body.get('display_name','').strip() or 'Player'
    if '@' not in email or len(pw)<6: raise HTTPException(400,'メールアドレスと6文字以上のパスワードを入力してください')
    c=conn()
    if SINGLE_USER_MODE and c.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n'] >= 1:
      c.close(); raise HTTPException(403,'このアプリは個人用のため、新規登録は締め切られています')
    try:
      cur=c.execute('INSERT INTO users(email,password_hash,display_name,created_at) VALUES(?,?,?,?)',(email,hash_pw(pw),name,now_iso())); uid=cur.lastrowid
      # Initial furniture positions
      for key,x,y in INITIAL_FURNITURE:
        c.execute(
          "INSERT INTO placements(user_id,item_type,furniture_key,x,y,placement_type,color_variant,created_at) VALUES(?,?,?,?,?,?, 'wood',?)",
          (uid,'furniture',key,x,y,furniture_placement_type(key),now_iso())
        )
      c.commit()
    except sqlite3.IntegrityError:
      c.close(); raise HTTPException(409,'このメールアドレスは登録済みです')
    c.close(); res=JSONResponse({'ok':True,'user':{'id':uid,'email':email,'display_name':name}}); res.set_cookie('plant_session',sign_session(uid),httponly=True,samesite='lax',max_age=60*60*24*30); return res

@app.post('/api/login')
async def login(request:Request):
    body=await request.json(); email=body.get('email','').strip().lower(); pw=body.get('password','')
    c=conn(); u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone(); c.close()
    if not u or not verify_pw(pw,u['password_hash']): raise HTTPException(401,'メールアドレスまたはパスワードが違います')
    res=JSONResponse({'ok':True,'user':{'id':u['id'],'email':u['email'],'display_name':u['display_name']}}); res.set_cookie('plant_session',sign_session(u['id']),httponly=True,samesite='lax',max_age=60*60*24*30); return res

@app.post('/api/logout')
def logout():
    res=JSONResponse({'ok':True}); res.delete_cookie('plant_session'); return res

@app.get('/api/system/status')
def system_status(request:Request):
    current_user(request)
    return {
      'ai_configured': bool(OPENAI_API_KEY),
      'text_model': OPENAI_TEXT_MODEL if OPENAI_API_KEY else None,
      'image_model': OPENAI_IMAGE_MODEL if OPENAI_API_KEY else None,
      'mode': 'openai' if OPENAI_API_KEY else 'local_fallback'
    }

@app.get('/api/me')
def me(request:Request):
    u=current_user(request); return {'id':u['id'],'email':u['email'],'display_name':u['display_name']}

@app.get('/api/plants')
def list_plants(request:Request):
    u=current_user(request); c=conn(); rows=c.execute('SELECT * FROM plants WHERE user_id=? AND deleted_at IS NULL ORDER BY created_at DESC',(u['id'],)).fetchall(); c.close(); return [plant_json(r) for r in rows]

@app.post('/api/plants')
async def create_plant(request:Request, name:str=Form(...), count:int=Form(...), photo:UploadFile=File(...), viewport:int=Form(1200)):
    u=current_user(request)
    if viewport < 900: raise HTTPException(400,'植物登録はPCから行ってください')
    if count<1 or count>99: raise HTTPException(400,'個数は1〜99で入力してください')
    raw=await photo.read()
    if not raw: raise HTTPException(400,'写真を追加してください')
    ext=Path(photo.filename or 'photo.jpg').suffix.lower()
    if ext not in ['.jpg','.jpeg','.png','.webp']: ext='.jpg'
    fname=f'u{u["id"]}_{int(time.time()*1000)}{ext}'; (UPLOADS/fname).write_bytes(raw)
    care=await ai_care(name,raw,photo.content_type or 'image/jpeg')
    c=conn(); existing=c.execute('SELECT * FROM plants WHERE user_id=? AND normalized_name=?',(u['id'],norm_name(name))).fetchone()
    if existing and not existing['deleted_at']:
        c.execute('UPDATE plants SET count=count+?, photo_path=? WHERE id=?',(count,f'/uploads/{fname}',existing['id']))
        c.execute('INSERT INTO inventory_events(user_id,plant_id,delta,reason,created_at) VALUES(?,?,?,?,?)',(u['id'],existing['id'],count,'add',now_iso()))
        c.commit(); row=c.execute('SELECT * FROM plants WHERE id=?',(existing['id'],)).fetchone(); c.close(); return plant_json(row)
    cur=c.execute('''INSERT INTO plants(user_id,name,normalized_name,count,photo_path,humidity_type,watering_interval_days,last_watered_at,next_water_at,care_note,sprite_base,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(u['id'],name.strip(),norm_name(name),count,f'/uploads/{fname}',care['humidity_type'],care['watering_interval_days'],now_iso(),(datetime.now(timezone.utc)+timedelta(days=care['watering_interval_days'])).isoformat(),care['care_note'],local_sprite_base(name),now_iso()))
    pid=cur.lastrowid; c.execute('INSERT INTO inventory_events(user_id,plant_id,delta,reason,created_at) VALUES(?,?,?,?,?)',(u['id'],pid,count,'add',now_iso())); c.commit()
    # If AI image generation is available, generate the 3x3 set concurrently.
    # All nine files must succeed before switching sprite_base, so partial generation never breaks rendering.
    if OPENAI_API_KEY:
      pdir=UPLOADS/f'plant_{pid}'; pdir.mkdir(exist_ok=True)
      jobs=[(state,mode) for state in ['normal','thirsty','dead'] for mode in ['floor','wall','hanging']]
      results=await asyncio.gather(*(ai_sprite(name,state,mode) for state,mode in jobs), return_exceptions=True)
      success=True
      for (state,mode),img in zip(jobs,results):
        if isinstance(img,(bytes,bytearray)) and img:
          (pdir/f'{state}_{mode}.png').write_bytes(img)
        else:
          success=False
      if success:
        c.execute('UPDATE plants SET sprite_base=? WHERE id=?',(f'/uploads/plant_{pid}',pid)); c.commit()
    row=c.execute('SELECT * FROM plants WHERE id=?',(pid,)).fetchone(); c.close(); return plant_json(row)

@app.post('/api/plants/{pid}/water')
def water(pid:int, request:Request):
    u=current_user(request); c=conn(); p=c.execute('SELECT * FROM plants WHERE id=? AND user_id=? AND deleted_at IS NULL',(pid,u['id'])).fetchone()
    if not p: c.close(); raise HTTPException(404,'植物が見つかりません')
    t=datetime.now(timezone.utc); nxt=t+timedelta(days=p['watering_interval_days'])
    c.execute('UPDATE plants SET last_watered_at=?,next_water_at=? WHERE id=?',(t.isoformat(),nxt.isoformat(),pid)); c.execute('INSERT INTO watering_events(user_id,plant_id,watered_at) VALUES(?,?,?)',(u['id'],pid,t.isoformat())); c.commit(); row=c.execute('SELECT * FROM plants WHERE id=?',(pid,)).fetchone(); c.close(); return plant_json(row)

@app.post('/api/water-all')
def water_all(request:Request):
    u=current_user(request); c=conn(); rows=c.execute('SELECT * FROM plants WHERE user_id=? AND deleted_at IS NULL',(u['id'],)).fetchall(); ids=[]
    t=datetime.now(timezone.utc)
    for p in rows:
      if calc_status(p)=='thirsty':
        nxt=t+timedelta(days=p['watering_interval_days']); c.execute('UPDATE plants SET last_watered_at=?,next_water_at=? WHERE id=?',(t.isoformat(),nxt.isoformat(),p['id'])); c.execute('INSERT INTO watering_events(user_id,plant_id,watered_at) VALUES(?,?,?)',(u['id'],p['id'],t.isoformat())); ids.append(p['id'])
    c.commit(); c.close(); return {'ok':True,'watered_ids':ids}

@app.post('/api/plants/{pid}/decrement')
async def decrement(pid:int, request:Request):
    u=current_user(request); body=await request.json(); reason=body.get('reason')
    if reason not in ['dead','discard']: raise HTTPException(400,'reason must be dead or discard')
    c=conn(); p=c.execute('SELECT * FROM plants WHERE id=? AND user_id=? AND deleted_at IS NULL',(pid,u['id'])).fetchone()
    if not p or p['count']<=0: c.close(); raise HTTPException(400,'減らせる植物がありません')
    dead_add=1 if reason=='dead' else 0
    c.execute('UPDATE plants SET count=count-1, dead_count=dead_count+? WHERE id=?',(dead_add,pid)); c.execute('INSERT INTO inventory_events(user_id,plant_id,delta,reason,created_at) VALUES(?,?,?,?,?)',(u['id'],pid,-1,reason,now_iso()))
    # If a live instance is in the room and it died, keep it there as a dead item until the user moves it.
    if reason=='dead':
      pl=c.execute("SELECT id FROM placements WHERE user_id=? AND plant_id=? AND item_type='plant' ORDER BY id DESC LIMIT 1",(u['id'],pid)).fetchone()
      if pl: c.execute("UPDATE placements SET item_type='dead_plant' WHERE id=?",(pl['id'],))
    # Remove excess live placements after discard
    live_count=c.execute("SELECT COUNT(*) n FROM placements WHERE user_id=? AND plant_id=? AND item_type='plant'",(u['id'],pid)).fetchone()['n']
    new_count=p['count']-1
    if live_count>new_count:
      excess=live_count-new_count
      ids=c.execute("SELECT id FROM placements WHERE user_id=? AND plant_id=? AND item_type='plant' ORDER BY id DESC LIMIT ?",(u['id'],pid,excess)).fetchall()
      for r in ids:c.execute('DELETE FROM placements WHERE id=?',(r['id'],))
    c.commit(); row=c.execute('SELECT * FROM plants WHERE id=?',(pid,)).fetchone(); c.close(); return plant_json(row)

@app.delete('/api/plants/{pid}')
def delete_plant(pid:int, request:Request):
    u=current_user(request); c=conn(); c.execute('UPDATE plants SET deleted_at=? WHERE id=? AND user_id=?',(now_iso(),pid,u['id'])); c.execute('DELETE FROM placements WHERE user_id=? AND plant_id=?',(u['id'],pid)); c.commit(); c.close(); return {'ok':True}

@app.get('/api/placements')
def list_placements(request:Request):
    u=current_user(request); c=conn(); rows=c.execute('''SELECT p.*, pl.name plant_name, pl.count plant_count, pl.dead_count, pl.sprite_base, pl.next_water_at FROM placements p LEFT JOIN plants pl ON p.plant_id=pl.id WHERE p.user_id=? AND (pl.deleted_at IS NULL OR p.plant_id IS NULL) ORDER BY p.id''',(u['id'],)).fetchall(); c.close()
    result=[]
    for r in rows:
      d=dict(r)
      if r['plant_id']:
        d['water_status']='thirsty' if r['next_water_at'] and datetime.now(timezone.utc)>=datetime.fromisoformat(r['next_water_at']) else 'normal'
      result.append(d)
    return result

@app.post('/api/placements')
async def add_placement(request:Request):
    u=current_user(request); b=await request.json(); pid=int(b.get('plant_id')); item_type=b.get('item_type','plant'); ptype=b.get('placement_type','floor')
    if item_type not in ['plant','dead_plant'] or ptype not in ['floor','wall','hanging']: raise HTTPException(400,'配置タイプが不正です')
    x = int(b.get('x', 8)); y = int(b.get('y', 6))
    c=conn(); p=c.execute('SELECT * FROM plants WHERE id=? AND user_id=? AND deleted_at IS NULL',(pid,u['id'])).fetchone()
    if not p: c.close(); raise HTTPException(404,'植物がありません')
    available=p['dead_count'] if item_type=='dead_plant' else p['count']
    placed=c.execute('SELECT COUNT(*) n FROM placements WHERE user_id=? AND plant_id=? AND item_type=?',(u['id'],pid,item_type)).fetchone()['n']
    if placed>=available: c.close(); raise HTTPException(400,'配置できる所持数を超えています')
    assert_space_available(c, u['id'], item_type, None, ptype, x, y)
    cur=c.execute('''INSERT INTO placements(user_id,plant_id,item_type,instance_index,area,x,y,placement_type,hang_depth,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',(u['id'],pid,item_type,placed+1,'room',x,y,ptype,max(1,min(4,int(b.get('hang_depth',1)))),now_iso())); c.commit(); row=c.execute('SELECT * FROM placements WHERE id=?',(cur.lastrowid,)).fetchone(); c.close(); return dict(row)

@app.post('/api/furniture')
async def add_furniture(request:Request):
    u=current_user(request); b=await request.json(); key=str(b.get('furniture_key',''))
    if key not in FURNITURE_KEYS: raise HTTPException(400,'家具タイプが不正です')
    x=int(b.get('x',8)); y=int(b.get('y',7))
    c=conn()
    assert_space_available(c, u['id'], 'furniture', key, 'floor', x, y)
    placed=c.execute("SELECT COUNT(*) n FROM placements WHERE user_id=? AND item_type='furniture' AND furniture_key=?",(u['id'],key)).fetchone()['n']
    cur=c.execute("""INSERT INTO placements(user_id,item_type,furniture_key,instance_index,area,x,y,placement_type,color_variant,created_at)
                     VALUES(?,'furniture',?,?,?,?,?,'floor','wood',?)""",(u['id'],key,placed+1,'room',x,y,now_iso()))
    c.commit(); row=c.execute('SELECT * FROM placements WHERE id=?',(cur.lastrowid,)).fetchone(); c.close(); return dict(row)

@app.patch('/api/placements/{plid}')
async def move_placement(plid:int, request:Request):
    u=current_user(request); b=await request.json(); fields=[]; vals=[]
    c=conn(); existing=c.execute('SELECT * FROM placements WHERE id=? AND user_id=?',(plid,u['id'])).fetchone()
    if not existing: c.close(); raise HTTPException(404,'配置が見つかりません')
    next_x = int(b['x']) if 'x' in b else int(existing['x'])
    next_y = int(b['y']) if 'y' in b else int(existing['y'])
    next_ptype = str(b['placement_type']) if 'placement_type' in b else existing['placement_type']
    if next_ptype not in ['floor','wall','hanging']: c.close(); raise HTTPException(400,'配置タイプが不正です')
    if any(key in b for key in ['x', 'y', 'placement_type']):
      assert_space_available(c, u['id'], existing['item_type'], existing['furniture_key'], next_ptype, next_x, next_y, exclude_id=plid)
    for key in ['x','y','hang_depth']:
      if key in b: fields.append(f'{key}=?'); vals.append(int(b[key]))
    for key in ['placement_type','color_variant']:
      if key in b: fields.append(f'{key}=?'); vals.append(str(b[key]))
    if not fields:
      c.close(); return {'ok':True}
    vals += [plid,u['id']]; c.execute(f'UPDATE placements SET {", ".join(fields)} WHERE id=? AND user_id=?',vals); c.commit(); row=c.execute('SELECT * FROM placements WHERE id=?',(plid,)).fetchone(); c.close(); return dict(row) if row else {'ok':False}

@app.delete('/api/placements/{plid}')
def remove_placement(plid:int, request:Request):
    u=current_user(request); c=conn(); c.execute('DELETE FROM placements WHERE id=? AND user_id=?',(plid,u['id'])); c.commit(); c.close(); return {'ok':True}

@app.get('/api/dashboard')
def dashboard(request:Request):
    u=current_user(request); c=conn(); plants=c.execute('SELECT * FROM plants WHERE user_id=? AND deleted_at IS NULL',(u['id'],)).fetchall(); c.close(); thirsty=[plant_json(p) for p in plants if calc_status(p)=='thirsty']
    return {'total_types':len(plants),'total_live':sum(p['count'] for p in plants),'total_dead':sum(p['dead_count'] for p in plants),'thirsty':thirsty}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=int(os.getenv('PORT','8787')))
