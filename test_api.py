import io
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from api import Polka, EMPTY, clean_state, HTTPError


class APITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Polka(str(Path(self.temp.name) / 'db.sqlite'), client_id='12345', client_secret='test-secret')

    def tearDown(self):
        self.temp.cleanup()

    def call(self, path, method='GET', body=None, cookie='', csrf='', origin='https://polka.maark.ru'):
        parsed = urlsplit(path)
        encoded = json.dumps(body).encode() if body is not None else b''
        env = {'PATH_INFO': parsed.path, 'QUERY_STRING': parsed.query, 'REQUEST_METHOD': method, 'HTTP_COOKIE': cookie, 'HTTP_ORIGIN': origin, 'HTTP_X_POLKA_CSRF': csrf, 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(encoded)), 'wsgi.input': io.BytesIO(encoded)}
        result = {}
        def start(status, headers):
            result.update(status=int(status.split()[0]), headers=headers)
        data = b''.join(self.app(env, start))
        result['body'] = json.loads(data) if data else None
        return result

    @staticmethod
    def cookie(result, name):
        for key, value in result['headers']:
            if key.lower() == 'set-cookie' and value.startswith(name + '='):
                return value.split(';')[0]
        return ''

    def login(self, owner='123'):
        name = 'test_' + owner
        result = self.call('/api/register', 'POST', {'username': name, 'password': 'test-password-123'})
        if result['status'] == 409:
            result = self.call('/api/login', 'POST', {'username': name, 'password': 'test-password-123'})
        self.assertEqual(result['status'], 200)
        cookie = self.cookie(result, '__Host-polka')
        session = self.call('/api/session', cookie=cookie)['body']
        self.assertTrue(session['user']['id'].startswith('local:'))
        return cookie, session['csrf']

    def test_signed_login_and_private_session(self):
        cookie, _ = self.login()
        result = self.call('/api/shelf', cookie=cookie)
        self.assertEqual(result['body'], {'revision': 0, 'state': None})
        self.assertEqual(self.call('/api/shelf')['status'], 401)
        self.assertEqual(self.call('/api/shelf', cookie='__Host-polka=forged')['status'], 401)

    def test_passwords_and_duplicate_names(self):
        self.login()
        self.assertEqual(self.call('/api/login','POST',{'username':'test_123','password':'wrong-password'})['status'],401)
        self.assertEqual(self.call('/api/register','POST',{'username':'TEST_123','password':'test-password-123'})['status'],409)
        self.assertEqual(self.call('/api/register','POST',{'username':'test_short','password':'short'})['status'],422)
        with closing(self.app.db()) as db:
            self.assertNotIn('test-password-123',db.execute('SELECT password FROM accounts').fetchone()[0])

    def test_customization_roundtrip(self):
        cookie, csrf = self.login()
        value = json.loads(json.dumps(EMPTY))
        value['worlds'][0].update(theme='night', font='serif', background='#303c43', backdrop='data:image/webp;base64,AA==')
        value['items']=[dict(id='photo',world='first',title='Photo',type='image',size=1.5,r=-18,fit='contain',frame='rounded',caption=False,image='data:image/png;base64,AA==')]
        response=self.call('/api/shelf','PUT',{'revision':0,'state':value},cookie,csrf)
        self.assertEqual(response['status'],200)
        saved=self.call('/api/shelf',cookie=cookie)['body']['state']
        self.assertEqual(saved['worlds'][0]['theme'],'night')
        self.assertEqual(saved['items'][0]['size'],1.5)
        self.assertEqual(saved['items'][0]['r'],-18)
        self.assertFalse(saved['items'][0]['caption'])

    def test_csrf_and_logout(self):
        cookie, csrf = self.login()
        self.assertEqual(self.call('/api/shelf', 'PUT', {'revision': 0, 'state': EMPTY}, cookie)['status'], 403)
        self.assertEqual(self.call('/api/shelf', 'PUT', {'revision': 0, 'state': EMPTY}, cookie, csrf, 'https://evil.example')['status'], 403)
        self.assertEqual(self.call('/api/session', 'DELETE', cookie=cookie, csrf=csrf)['status'], 200)
        self.assertEqual(self.call('/api/shelf', cookie=cookie)['status'], 401)

    def test_two_devices_and_private_ownership(self):
        computer, csrf1 = self.login()
        phone, csrf2 = self.login()
        stranger, _ = self.login('999')
        saved = self.call('/api/shelf', 'PUT', {'revision': 0, 'state': EMPTY}, computer, csrf1)
        self.assertEqual(saved['status'], 200)
        self.assertEqual(self.call('/api/shelf', cookie=phone)['body']['state'], EMPTY)
        self.assertIsNone(self.call('/api/shelf', cookie=stranger)['body']['state'])
        self.assertEqual(self.call('/api/shelf', 'PUT', {'revision': 0, 'state': EMPTY}, phone, csrf2)['status'], 409)
        self.assertEqual(self.call('/api/shelf?since=1', cookie=phone)['body']['unchanged'], True)
        # A fresh app process reads the same durable database and sessions.
        self.app = Polka(self.app.database, client_id='12345', client_secret='test-secret')
        self.assertEqual(self.call('/api/shelf', cookie=phone)['body']['revision'], 1)

    def test_atomic_revision_under_parallel_writes(self):
        cookie, csrf = self.login()
        def write(_):
            return self.call('/api/shelf', 'PUT', {'revision': 0, 'state': EMPTY}, cookie, csrf)['status']
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(write, range(2))), [200, 409])

    def test_validation_and_history(self):
        cookie, csrf = self.login()
        invalid = {'version': 1, 'worlds': EMPTY['worlds'], 'items': [{'id': 'x', 'world': 'missing', 'title': 'x', 'type': 'quote'}]}
        self.assertEqual(self.call('/api/shelf', 'PUT', {'revision': 0, 'state': invalid}, cookie, csrf)['status'], 422)
        for revision in range(23):
            self.assertEqual(self.call('/api/shelf', 'PUT', {'revision': revision, 'state': EMPTY}, cookie, csrf)['status'], 200)
        with closing(self.app.db()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 20)
        with self.assertRaises(HTTPError):
            clean_state({'version': 1, 'worlds': [], 'items': []})



if __name__ == '__main__':
    unittest.main()
