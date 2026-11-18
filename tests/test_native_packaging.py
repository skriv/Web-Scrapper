"""Installer/packaging regressions; no live Internet or model inference."""
import hashlib,json,os,socket,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import common
from unittest.mock import patch
import manage

class PackagingTests(unittest.TestCase):
    def test_application_source_matches_original_snapshot(self):
        for name,digest in json.loads((ROOT/'config/source-integrity.json').read_text()).items():
            with self.subTest(name=name):self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),digest)

    def test_settings_snapshot_has_current_routing_not_class_defaults(self):
        s=json.loads((ROOT/'config/settings.json').read_text())
        self.assertEqual(len(s),32)
        self.assertEqual((s['mapsPrimary'],s['websitePrimary']),('sonagara','scrapling'))
        for key in ['mapsFallbackEnabled','crawl4aiFallback','playwrightFallback','overtureEnabled','osmEnabled','foursquareEnabled']:self.assertFalse(s[key])
        self.assertEqual(s['mapsFallback'],'gosom');self.assertEqual(s['ollamaModel'],'qwen3:4b')
        self.assertEqual(s['ollamaTimeout'],20);self.assertEqual(s['cacheStrategy'],'refresh')

    def test_seed_initializes_once_and_preserves_user_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            env={**os.environ,'PROFILEKIT_DATA_DIR':folder,'DATABASE_URL':'','REDIS_URL':'','PYTHONPATH':str(ROOT/'backend')}
            command=[sys.executable,str(ROOT/'scripts/seed_settings.py')]
            subprocess.run(command,env=env,check=True,capture_output=True)
            with sqlite3.connect(Path(folder)/'profilekit.sqlite3') as c:
                data=json.loads(c.execute("SELECT payload FROM pk_settings WHERE id='current'").fetchone()[0])
                self.assertEqual(data,json.loads((ROOT/'config/settings.json').read_text()))
                data['cacheTtl']=777;c.execute("UPDATE pk_settings SET payload=? WHERE id='current'",(json.dumps(data),))
            subprocess.run(command,env=env,check=True,capture_output=True)
            with sqlite3.connect(Path(folder)/'profilekit.sqlite3') as c:
                self.assertEqual(json.loads(c.execute("SELECT payload FROM pk_settings WHERE id='current'").fetchone()[0])['cacheTtl'],777)

    def test_pid_ownership_requires_this_script_and_unique_instance_token(self):
        state={'pid':12345,'token':'owned-test-token'}
        with patch.object(manage.os,'kill'),patch.object(manage.subprocess,'check_output',return_value='some unrelated service'):
            self.assertFalse(manage.owned_alive(state))
        with patch.object(manage.os,'kill'),patch.object(manage.subprocess,'check_output',return_value=str(ROOT/'scripts/manage.py')+' run --instance owned-test-token'):
            self.assertTrue(manage.owned_alive(state))

    def test_port_probe_detects_listener_but_allows_stopped_server(self):
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            server.bind(('127.0.0.1',0));server.listen(1)
            port=server.getsockname()[1]
            self.assertFalse(common.port_free(port))
        self.assertTrue(common.port_free(port))

    def test_launcher_keeps_sqlite_and_current_native_browser_channel(self):
        env=common.env_for(web_port=3012,api_port=8012,maps_port=8013)
        self.assertEqual(env['MAPS_BASE_URL'],'http://127.0.0.1:8013')
        self.assertEqual(env['API_BASE_URL'],'http://127.0.0.1:8012')
        self.assertEqual(env['BROWSER_CHANNEL'],'chrome')
        self.assertEqual(env['DATABASE_URL'],'');self.assertEqual(env['REDIS_URL'],'')
        self.assertEqual(env['PROFILEKIT_DATA_DIR'],str(ROOT/'data'))

if __name__=='__main__':unittest.main()
