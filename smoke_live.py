"""Exercise the deployed API with an isolated throwaway account, then remove it."""
import http.cookiejar
import json
import secrets
import sqlite3
import urllib.error
import urllib.request

origin = 'https://polka.maark.ru'
name = 'check_' + secrets.token_hex(8)
password = secrets.token_urlsafe(24)

def client():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

def call(opener, path, method='GET', data=None, csrf=''):
    request = urllib.request.Request(origin + path, method=method, data=json.dumps(data).encode() if data else None,
        headers={'Origin': origin, 'Content-Type': 'application/json', 'X-Polka-CSRF': csrf})
    try:
        with opener.open(request, timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)

computer, phone, visitor = client(), client(), client()
owner = None
try:
    assert call(computer, '/api/register', 'POST', {'username': name, 'password': password})[0] == 200
    session = call(computer, '/api/session')[1]
    owner = session['user']['id']
    csrf = session['csrf']
    assert call(phone, '/api/login', 'POST', {'username': name, 'password': password})[0] == 200
    document = {'version': 1, 'worlds': [{'id': 'w', 'name': 'Smoke', 'description': '', 'color': '#777c65', 'theme': 'night'}], 'items': []}
    assert call(computer, '/api/shelf', 'PUT', {'revision': 0, 'state': document}, csrf)[0] == 200
    assert call(phone, '/api/shelf')[1]['state']['worlds'][0]['theme'] == 'night'
    assert call(visitor, '/api/shelf')[0] == 401
    assert call(computer, '/api/shelf', 'PUT', {'revision': 0, 'state': document}, csrf)[0] == 409
    print('Live API passed: registration, login, two sessions, persisted settings, privacy, conflict rejection')
finally:
    if owner:
        db = sqlite3.connect('/opt/polka/data/polka.sqlite3')
        try:
            assert db.execute('SELECT owner FROM accounts WHERE username=?', (name,)).fetchone()[0] == owner
            with db:
                for table in ('sessions', 'snapshots', 'accounts', 'shelves'):
                    db.execute('DELETE FROM ' + table + ' WHERE owner=?', (owner,))
        finally:
            db.close()
