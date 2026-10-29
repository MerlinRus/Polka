"""Private shelf API with nickname and password accounts"""
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import time
import urllib.parse
import zlib
from contextlib import closing
from http.cookies import SimpleCookie
from pathlib import Path

import unicodedata

MAX_BODY = 20 * 1024 * 1024
SESSION_AGE = 30 * 86400
EMPTY = {'version': 1, 'worlds': [{'id': 'first', 'name': 'Моя полка', 'description': 'Место для того, что хочется оставить.', 'color': '#777c65'}], 'items': []}


class HTTPError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def clean_state(value):
    def text(obj, key, limit, required=False):
        value = obj.get(key, '')
        if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
            raise HTTPError(422, 'Проверь названия, заметки и размер картинок.')
        return value

    if not isinstance(value, dict) or value.get('version') != 1:
        raise HTTPError(422, 'Неверный формат полки.')
    worlds, items = value.get('worlds'), value.get('items')
    if not isinstance(worlds, list) or not 1 <= len(worlds) <= 100 or not isinstance(items, list) or len(items) > 2000:
        raise HTTPError(422, 'Можно сохранить до 100 миров и 2000 находок.')
    result = {'version': 1, 'worlds': [], 'items': []}
    world_ids, item_ids = set(), set()
    for world in worlds:
        if not isinstance(world, dict):
            raise HTTPError(422, 'Повреждён мир.')
        w = {k: text(world, k, limit, k != 'description') for k, limit in [('id', 100), ('name', 60), ('description', 200), ('color', 7)]}
        if w['id'] in world_ids or not re.fullmatch(r'#[0-9a-fA-F]{6}', w['color']):
            raise HTTPError(422, 'Повреждён мир.')
        for field, allowed in [('theme', ('sage','ivory','rose','sky','night')), ('font', ('sans','serif'))]:
            if field in world and world[field] in allowed:
                w[field] = world[field]
        if world.get('background') and re.fullmatch(r'#[0-9a-fA-F]{6}', str(world['background'])):
            w['background'] = world['background']
        if world.get('backdrop'):
            backdrop = text(world, 'backdrop', 1600000)
            if not re.fullmatch(r'data:image/(jpeg|png|webp);base64,[A-Za-z0-9+/=]+', backdrop):
                raise HTTPError(422, 'Неподдерживаемый фон')
            w['backdrop'] = backdrop
        world_ids.add(w['id'])
        result['worlds'].append(w)
    for item in items:
        if not isinstance(item, dict):
            raise HTTPError(422, 'Повреждена находка.')
        i = {k: text(item, k, limit, k in ('id', 'world', 'title', 'type')) for k, limit in [('id', 100), ('world', 100), ('title', 120), ('type', 10), ('note', 600), ('text', 3000), ('url', 2000), ('image', 1600000), ('artist', 200), ('created', 50)]}
        if i['id'] in item_ids or i['world'] not in world_ids or i['type'] not in ('image', 'quote', 'music', 'link'):
            raise HTTPError(422, 'Повреждена находка.')
        for field in ('url',):
            if i[field] and urllib.parse.urlsplit(i[field]).scheme not in ('http', 'https'):
                raise HTTPError(422, 'Нужна ссылка http:// или https://.')
        if i['image'] and not re.fullmatch(r'data:image/(jpeg|png|webp|gif);base64,[A-Za-z0-9+/=]+', i['image']):
            raise HTTPError(422, 'Неподдерживаемый формат картинки.')
        for field, low, high, default in [('x', 10, 930, 30), ('y', 25, 16000, 50), ('r', -20, 20, 0)]:
            number = item.get(field, default)
            if not isinstance(number, (int, float)) or not math.isfinite(number):
                raise HTTPError(422, 'Неверное положение предмета.')
            i[field] = max(low, min(high, number))
        if 'size' in item:
            if not isinstance(item['size'], (float,int)) or not math.isfinite(item['size']):
                raise HTTPError(422, 'Неверный размер')
            i['size'] = max(.8, min(1.6, item['size']))
        for field, allowed in [('fit',('cover','contain')),('frame',('paper','plain','rounded'))]:
            if field in item and item[field] in allowed:
                i[field] = item[field]
        if 'caption' in item:
            i['caption'] = item['caption'] is not False
        i['favorite'] = item.get('favorite') is True
        i['color'] = item.get('color') if item.get('color') in ('cream', 'yellow', 'pink', 'blue', 'sage') else 'cream'
        i['art'] = item.get('art') if item.get('art') in ('sea', 'mountain', 'orange', 'window', 'flower') else None
        item_ids.add(i['id'])
        result['items'].append(i)
    return result


class Polka:
    def __init__(self, database=None, origin=None, client_id=None, client_secret=None):
        self.database = database or os.environ.get('POLKA_DATABASE', '/opt/polka/data/polka.sqlite3')
        self.origin = (origin or os.environ.get('POLKA_ORIGIN', 'https://polka.maark.ru')).rstrip('/')

        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.db()) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS shelves (owner TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, document BLOB);
                CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, owner TEXT NOT NULL, csrf TEXT NOT NULL, expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS accounts (username TEXT PRIMARY KEY, owner TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots (owner TEXT NOT NULL, revision INTEGER NOT NULL, document BLOB NOT NULL, created INTEGER NOT NULL, PRIMARY KEY(owner, revision));
            ''')

    def db(self):
        db = sqlite3.connect(self.database, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    def cookie(self, name, value, age):
        return ('Set-Cookie', f'{name}={value}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age={age}')

    def cookies(self, env):
        cookie = SimpleCookie()
        try:
            cookie.load(env.get('HTTP_COOKIE', ''))
            return {key: value.value for key, value in cookie.items()}
        except Exception:
            return {}

    def session(self, env, db):
        token = self.cookies(env).get('__Host-polka', '')
        session = db.execute('SELECT sessions.*, shelves.name FROM sessions JOIN shelves ON shelves.owner=sessions.owner WHERE token=? AND expires>?', (digest(token), int(time.time()))).fetchone()
        if not session:
            raise HTTPError(401, 'Войди, чтобы открыть свою полку')
        return session

    def body(self, env):
        try:
            size = int(env.get('CONTENT_LENGTH') or 0)
        except ValueError:
            raise HTTPError(400, 'Неверный размер запроса.')
        if size < 1 or size > MAX_BODY:
            raise HTTPError(413, 'Полка должна занимать не больше 20 МБ.')
        if env.get('CONTENT_TYPE', '').split(';')[0] != 'application/json':
            raise HTTPError(415, 'Нужен JSON.')
        try:
            value = json.loads(env['wsgi.input'].read(size))
        except (ValueError, UnicodeError):
            raise HTTPError(400, 'Не удалось прочитать запрос.')
        if not isinstance(value, dict):
            raise HTTPError(400, 'Неверный запрос.')
        return value

    def csrf(self, env, session):
        if env.get('HTTP_ORIGIN') != self.origin or not hmac.compare_digest(env.get('HTTP_X_POLKA_CSRF', ''), session['csrf']):
            raise HTTPError(403, 'Обнови страницу и попробуй ещё раз.')

    @staticmethod
    def unpack(blob):
        return json.loads(zlib.decompress(blob)) if blob else None

    def route(self, env, db):
        path, method = env['PATH_INFO'], env['REQUEST_METHOD']
        if path == '/api/healthz' and method == 'GET':
            db.execute('SELECT 1')
            return 200, {'ok': True}, []
        if path in ('/api/register', '/api/login') and method == 'POST':
            if env.get('HTTP_ORIGIN') != self.origin:
                raise HTTPError(403, 'Обнови страницу и повтори вход')
            body = self.body(env)
            name, password = body.get('username'), body.get('password')
            if not isinstance(name, str) or not isinstance(password, str):
                raise HTTPError(422, 'Введи никнейм и пароль')
            name = unicodedata.normalize('NFKC', name).strip()
            if not re.fullmatch(r'[\w-]{3,40}', name) or not 8 <= len(password) <= 256:
                raise HTTPError(422, 'Никнейм: 3–40 букв, цифр, дефисов или подчёркиваний. Пароль: от 8 символов')
            username = name.casefold()
            now = int(time.time())
            bucket = digest(username)
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM attempts WHERE expires<?', (now,))
            attempt = db.execute('SELECT count FROM attempts WHERE bucket=?', (bucket,)).fetchone()
            if attempt and attempt['count'] >= 15:
                db.execute('ROLLBACK')
                raise HTTPError(429, 'Слишком много попыток. Попробуй через минуту')
            db.execute('INSERT INTO attempts VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1', (bucket, now+60))
            db.execute('COMMIT')
            account = db.execute('SELECT * FROM accounts WHERE username=?', (username,)).fetchone()
            if path == '/api/register':
                if account:
                    raise HTTPError(409, 'Этот никнейм уже занят')
                salt = secrets.token_hex(16)
                hashed = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
                owner = 'local:' + secrets.token_hex(16)
                db.execute('BEGIN IMMEDIATE')
                try:
                    db.execute('INSERT INTO accounts VALUES(?,?,?,?)', (username, owner, salt, hashed))
                    db.execute('INSERT INTO shelves(owner,name) VALUES(?,?)', (owner, name))
                    db.execute('COMMIT')
                except sqlite3.IntegrityError:
                    db.execute('ROLLBACK')
                    raise HTTPError(409, 'Этот никнейм уже занят')
            else:
                salt = account['salt'] if account else '00'*16
                hashed = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
                if not account or not hmac.compare_digest(hashed, account['password']):
                    raise HTTPError(401, 'Неверный никнейм или пароль')
                owner = account['owner']
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            db.execute('DELETE FROM sessions WHERE expires<?', (now,))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (digest(token), owner, csrf, now+SESSION_AGE))
            return 200, {'ok': True}, [self.cookie('__Host-polka', token, SESSION_AGE)]
        if path == '/api/session' and method == 'GET':
            try:
                session = self.session(env, db)
            except HTTPError:
                return 200, {'user': None, 'configured': True}, []
            return 200, {'user': {'id': session['owner'], 'name': session['name']}, 'csrf': session['csrf'], 'configured': True}, []
        if path == '/api/session' and method == 'DELETE':
            session = self.session(env, db)
            self.csrf(env, session)
            db.execute('DELETE FROM sessions WHERE token=?', (session['token'],))
            return 200, {'ok': True}, [self.cookie('__Host-polka', '', 0)]
        if path == '/api/shelf' and method in ('GET', 'PUT'):
            session = self.session(env, db)
            if method == 'GET':
                row = db.execute('SELECT revision,document FROM shelves WHERE owner=?', (session['owner'],)).fetchone()
                since = urllib.parse.parse_qs(env.get('QUERY_STRING', '')).get('since', [''])[0]
                if since == str(row['revision']):
                    return 200, {'revision': row['revision'], 'unchanged': True}, []
                return 200, {'revision': row['revision'], 'state': self.unpack(row['document'])}, []
            self.csrf(env, session)
            body = self.body(env)
            if type(body.get('revision')) is not int or body['revision'] < 0:
                raise HTTPError(422, 'Неверная версия полки.')
            state = clean_state(body.get('state'))
            encoded = json.dumps(state, ensure_ascii=False, separators=(',', ':')).encode()
            if len(encoded) > MAX_BODY - 1024:
                raise HTTPError(413, 'Полка должна занимать не больше 20 МБ.')
            blob = zlib.compress(encoded)
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,document FROM shelves WHERE owner=?', (session['owner'],)).fetchone()
            if row['revision'] != body['revision']:
                db.execute('ROLLBACK')
                return 409, {'error': {'message': 'Полка изменилась на другом устройстве.'}, 'revision': row['revision'], 'state': self.unpack(row['document'])}, []
            revision = row['revision'] + 1
            db.execute('UPDATE shelves SET revision=?,document=? WHERE owner=?', (revision, blob, session['owner']))
            db.execute('INSERT INTO snapshots VALUES(?,?,?,?)', (session['owner'], revision, blob, int(time.time())))
            db.execute('DELETE FROM snapshots WHERE owner=? AND revision<?', (session['owner'], revision - 19))
            db.execute('COMMIT')
            return 200, {'revision': revision, 'state': state}, []
        raise HTTPError(404, 'Не найдено.')

    def __call__(self, env, start_response):
        try:
            with closing(self.db()) as db:
                status, body, headers = self.route(env, db)
        except HTTPError as error:
            status, body, headers = error.status, {'error': {'message': error.message}}, []
        except Exception:
            status, body, headers = 500, {'error': {'message': 'Сервер временно недоступен. Изменения останутся на устройстве.'}}, []
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else b''
        headers += [('Content-Type', 'application/json; charset=utf-8'), ('Cache-Control', 'no-store'), ('Content-Length', str(len(data))), ('X-Content-Type-Options', 'nosniff')]
        from http import HTTPStatus
        start_response(f'{status} {HTTPStatus(status).phrase}', headers)
        return [data]


def create_app():
    return Polka()
