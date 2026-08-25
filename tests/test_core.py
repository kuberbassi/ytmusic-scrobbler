import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from api.index import (
    app,
    extract_primary_artist,
    generate_track_uids,
    is_music_content,
    strip_title_variants,
)
from api import database


class ScrobbleAccuracyTests(unittest.TestCase):
    def test_title_variants_are_bounded_and_predictable(self):
        self.assertEqual(strip_title_variants('Song (Official Audio)'), 'Song')
        self.assertEqual(strip_title_variants('Song (Live at Wembley)'), 'Song')
        self.assertEqual(strip_title_variants('Song (Original Version)'), 'Song (Original Version)')

    def test_primary_artist_keeps_solo_names(self):
        self.assertEqual(extract_primary_artist('Artist - Topic'), 'Artist')
        self.assertEqual(extract_primary_artist('AC/DC'), 'AC/DC')
        self.assertEqual(extract_primary_artist('Artist A & Artist B'), 'Artist A')

    def test_non_music_filter_preserves_music_performances(self):
        self.assertFalse(is_music_content({'title': 'Album Review and Reaction'}))
        self.assertTrue(is_music_content({'title': 'Acoustic Cover Performance'}))
        self.assertTrue(is_music_content({'title': 'Track', 'album': {'name': 'Album'}}))

    def test_generated_uids_fit_database_constraint(self):
        uids = generate_track_uids('T' * 700, 'A' * 700, 'video123')
        self.assertIn('vid:video123', uids)
        self.assertTrue(all(len(uid) <= 512 for uid in uids))

    def test_persistent_match_never_becomes_pending_again(self):
        from api.index import is_track_scrobbled
        matched, uid = is_track_scrobbled(
            ['vid:old'],
            {'vid:old': {'timestamp': 1}},
            cooldown_seconds=1,
        )
        self.assertTrue(matched)
        self.assertEqual(uid, 'vid:old')


class ApiSecurityTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='test-secret')
        self.client = app.test_client()

    def test_cheap_status_never_probes_third_parties(self):
        config = {
            'lastfm': {'api_key': 'a', 'api_secret': 'b', 'session_key': 'c'},
            'ytmusic': {'headers': 'cookie: test'},
        }
        with patch('api.index.get_lastfm_network', side_effect=AssertionError('Last.fm probe')), \
             patch('api.index.get_ytmusic_client', side_effect=AssertionError('YT Music probe')):
            response = self.client.post('/api/status', json=config)
        self.assertEqual(response.status_code, 200)

    def test_validation_reports_expired_credentials(self):
        ytmusic = Mock()
        ytmusic.get_history.side_effect = RuntimeError('expired cookie')
        config = {
            'lastfm': {'api_key': 'a', 'api_secret': 'b', 'session_key': 'c'},
            'ytmusic': {'headers': 'cookie: test'},
        }
        with patch('api.index.validate_lastfm_session', side_effect=RuntimeError('invalid session')), \
             patch('api.index.get_ytmusic_client', return_value=(ytmusic, None)):
            payload = self.client.post('/api/status?validate=1', json=config).get_json()
        self.assertFalse(payload['lastfm']['connected'])
        self.assertFalse(payload['ytmusic']['connected'])
        self.assertIn('expired', payload['ytmusic']['error'].lower())

    def test_cron_secret_is_mandatory(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.client.get('/api/cron').status_code, 503)
        with patch.dict(os.environ, {'CRON_SECRET': 'correct'}, clear=True):
            self.assertEqual(self.client.get('/api/cron').status_code, 401)

    def test_config_response_never_contains_secrets(self):
        with self.client.session_transaction() as session:
            session['logged_in'] = True
            session['user_id'] = 'user-id'
        stored = {
            'lastfm': {'api_key': 'key', 'api_secret': 'secret', 'session_key': 'session'},
            'ytmusic': {'headers': 'private-cookie'},
            'auto_scrobble': True,
            'interval': 300,
        }
        with patch('api.index.ConfigManager.load', return_value=stored):
            response = self.client.get('/api/config')
        raw = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('secret', raw)
        self.assertNotIn('private-cookie', raw)
        self.assertTrue(response.get_json()['lastfm_configured'])

    def test_credential_save_clears_stale_sync_health_and_sets_baseline(self):
        with self.client.session_transaction() as session:
            session['logged_in'] = True
            session['user_id'] = 'user-id'
        stored = {
            'lastfm': {'api_key': 'key', 'api_secret': 'secret', 'session_key': 'session'},
            'ytmusic': {'headers': 'old'},
            'auto_scrobble': True,
            'interval': 300,
        }
        with patch('api.index.ConfigManager.load', return_value=stored), \
             patch('api.index.ConfigManager.save', return_value=True) as save, \
             patch('api.index.reset_user_sync_health', return_value=True) as reset:
            response = self.client.post('/api/config', json={'ytmusic': {'headers': 'new'}})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(save.call_args.args[0]['baseline_history_on_next_sync'])
        reset.assert_called_once_with('user-id')

    def test_lastfm_auth_url_uses_stored_key_without_exposing_it(self):
        with self.client.session_transaction() as session:
            session['logged_in'] = True
            session['user_id'] = 'user-id'
        stored = {'lastfm': {'api_key': 'stored-key', 'api_secret': 'stored-secret'}}
        with patch('api.index._enrich_config_from_db', return_value=stored):
            response = self.client.get('/api/lastfm-auth-url')
        self.assertEqual(response.status_code, 200)
        self.assertIn('api_key=stored-key', response.get_json()['url'])
        self.assertNotIn('stored-secret', response.get_data(as_text=True))

    def test_history_reset_requires_explicit_confirmation(self):
        response = self.client.post('/api/reset-history', json={})
        self.assertEqual(response.status_code, 400)

    def test_sensitive_routes_require_login_in_multi_user_mode(self):
        with patch('api.index.is_multi_user_enabled', return_value=True):
            self.assertEqual(self.client.post('/api/status', json={}).status_code, 401)
            self.assertEqual(self.client.post('/api/scrobble-single', json={'artist': 'a', 'title': 't'}).status_code, 401)
            self.assertEqual(self.client.post('/api/lastfm-session', json={}).status_code, 401)


class DatabaseAccessTests(unittest.TestCase):
    def test_atomic_claim_uses_rpc(self):
        response = Mock(status_code=200)
        response.json.return_value = [{'id': 'user', 'sync_claim_token': 'claim'}]
        with patch.object(database, 'REST_API_AVAILABLE', True), \
             patch.object(database, 'SUPABASE_URL', 'https://example.supabase.co'), \
             patch.object(database.requests, 'post', return_value=response) as post:
            users = database.claim_active_users(limit=20, interval_seconds=300)
        self.assertEqual(users[0]['sync_claim_token'], 'claim')
        self.assertIn('/rpc/claim_scrobble_users', post.call_args.args[0])

    def test_finish_sync_releases_claim_and_records_success(self):
        response = Mock(status_code=204, text='')
        with patch.object(database, 'REST_API_AVAILABLE', True), \
             patch.object(database, 'SUPABASE_URL', 'https://example.supabase.co'), \
             patch.object(database.requests, 'patch', return_value=response) as patch_request:
            self.assertTrue(database.finish_user_sync('user', 'claim'))
        payload = patch_request.call_args.kwargs['json']
        self.assertIsNone(payload['sync_claim_token'])
        self.assertIsNotNone(payload['last_sync_success_at'])

    def test_targeted_match_query_does_not_download_full_history(self):
        response = Mock(status_code=200)
        response.json.return_value = [{'track_uid': 'vid:one', 'last_scrobble_time': 1}]
        with patch.object(database, 'REST_API_AVAILABLE', True), \
             patch.object(database, 'SUPABASE_URL', 'https://example.supabase.co'), \
             patch.object(database.requests, 'get', return_value=response) as get:
            matches, _ = database.get_user_scrobble_matches('user', ['vid:one', 'vid:two'])
        self.assertEqual(matches, {'vid:one'})
        self.assertIn('track_uid', get.call_args.kwargs['params'])

    def test_production_migration_is_non_destructive_and_not_public(self):
        sql = Path('supabase/migrations/20260825_harden_sync_backend.sql').read_text(encoding='utf-8').lower()
        self.assertNotIn('drop table', sql)
        self.assertNotIn('using (true)', sql)
        self.assertIn('for update skip locked', sql)
        self.assertIn('revoke all', sql)


if __name__ == '__main__':
    unittest.main()
