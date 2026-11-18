"""PostgreSQL persistence + Redis cache; explicit SQLite fallback for native development."""
import asyncio
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

class Storage:
    def __init__(self):
        self.database_url = os.getenv('DATABASE_URL', '')
        self.redis_url = os.getenv('REDIS_URL', '')
        self.path = Path(os.getenv('PROFILEKIT_DATA_DIR', './data')) / 'profilekit.sqlite3'
        self.ready = False
        self.error = None

    def _connect(self):
        if self.database_url:
            import psycopg
            return psycopg.connect(self.database_url, connect_timeout=3)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.path, timeout=5)

    def _query(self, sql, params=(), fetch=False):
        if self.database_url: sql = sql.replace('?', '%s')
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall() if fetch else []
            connection.commit()
            return rows

    async def init(self):
        if self.ready: return
        for table in ['settings','history','cache']:
            await asyncio.to_thread(self._query, f'CREATE TABLE IF NOT EXISTS pk_{table} (id TEXT PRIMARY KEY, payload TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL)')
        self.ready = True

    async def get(self, table, key):
        await self.init()
        rows = await asyncio.to_thread(self._query, f'SELECT payload, expires FROM pk_{table} WHERE id=?', (key,), True)
        return json.loads(rows[0][0]) if rows and (rows[0][1] == 0 or rows[0][1] > time.time()) else None

    async def put(self, table, key, value, ttl=0):
        await self.init()
        await asyncio.to_thread(self._query, f'INSERT INTO pk_{table}(id,payload,expires) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, expires=excluded.expires', (key, json.dumps(value), time.time()+ttl if ttl else 0))
        # Retain at most 100 successful website profiles / 7 days of history.
        if table in {'cache','history'}:
            await asyncio.to_thread(self._query, f'DELETE FROM pk_{table} WHERE expires > 0 AND expires < ?', (time.time(),))
        if table == 'history':
            await asyncio.to_thread(self._query, 'DELETE FROM pk_history WHERE id NOT IN (SELECT id FROM pk_history ORDER BY expires DESC LIMIT 100)')

    async def history(self):
        await self.init()
        rows = await asyncio.to_thread(self._query, 'SELECT payload FROM pk_history WHERE expires > ? ORDER BY expires DESC LIMIT 50', (time.time(),), True)
        return [json.loads(row[0]) for row in rows]

    async def cache_get(self, key):
        if self.redis_url:
            try:
                import redis.asyncio as redis
                async with redis.from_url(self.redis_url, socket_connect_timeout=1, socket_timeout=1) as client:
                    value = await client.get('profilekit:v2:'+key)
                    if value: return json.loads(value), 'redis'
            except Exception:
                self.error = 'Redis unavailable; database cache used.'
        return await self.get('cache', key), 'postgresql' if self.database_url else 'sqlite'

    async def cache_put(self, key, value, ttl):
        await self.put('cache', key, value, ttl)
        if self.redis_url:
            try:
                import redis.asyncio as redis
                async with redis.from_url(self.redis_url, socket_connect_timeout=1, socket_timeout=1) as client:
                    await client.setex('profilekit:v2:'+key, ttl, json.dumps(value))
            except Exception:
                self.error = 'Redis unavailable; database cache used.'

    async def clear_cache(self):
        await self.init()
        # Never flush another application's Redis keys.
        if self.redis_url:
            import redis.asyncio as redis
            async with redis.from_url(self.redis_url, socket_connect_timeout=1, socket_timeout=1) as client:
                batch = []
                async for key in client.scan_iter('profilekit:v2:*', count=100):
                    batch.append(key)
                    if len(batch) >= 100:
                        await client.delete(*batch); batch=[]
                if batch: await client.delete(*batch)
        await asyncio.to_thread(self._query, 'DELETE FROM pk_cache')

    async def health(self):
        database = 'postgresql' if self.database_url else 'sqlite (native development fallback)'
        try:
            await self.init()
            await asyncio.to_thread(self._query, 'SELECT 1', (), True)
            db_status = 'ready'
        except Exception:
            db_status = 'unavailable'
        redis_status = 'not configured'
        if self.redis_url:
            try:
                import redis.asyncio as redis
                async with redis.from_url(self.redis_url, socket_connect_timeout=1, socket_timeout=1) as client:
                    await client.ping(); redis_status='ready'
            except Exception: redis_status='unavailable'
        return {'database':database, 'databaseStatus':db_status,'redis':redis_status,'warning':self.error}

store = Storage()

def cache_key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',',':')).encode()).hexdigest()
