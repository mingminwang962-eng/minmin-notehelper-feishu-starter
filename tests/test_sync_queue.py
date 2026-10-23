"""Offline state transitions; no real credentials, network, Feishu, or scheduler."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'notebook-feishu-onboarding/scripts'
sys.path.insert(0, str(SCRIPTS))
import sync_queue as queue


class QueueTests(unittest.TestCase):
    def setUp(self):
        network_guard = patch.object(queue.cloud, 'request', side_effect=AssertionError('real API forbidden'))
        network_guard.start()
        self.addCleanup(network_guard.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'job'
        self.config = Path(self.temp.name) / 'key.json'
        self.config.write_text('{"api_key":"offline-only"}')
        self.args = argparse.Namespace(user_tested=True, config=str(self.config),
            target='https://example.feishu.cn/drive/folder/target', cadence='every 30 minutes',
            timezone='Asia/Shanghai', test_note_id='old',
            test_doc_url='https://example.feishu.cn/docx/TestDoc')
        with patch.object(queue, 'metadata', return_value={'old': {'id': 'old'}}):
            queue.initialize(self.root, self.args)

    def job(self):
        return json.loads((self.root / 'job.json').read_text())

    def main(self, args):
        stream = io.StringIO()
        with patch.object(sys, 'argv', ['sync_queue.py', '--job', str(self.root)] + args), contextlib.redirect_stdout(stream):
            queue.main()
        return json.loads(stream.getvalue())

    def add(self):
        articles = {'old': {'id': 'old'},
                    'new': {'id': 'new', 'title': 'test title', 'result_after': 1}}
        with patch.object(queue, 'metadata', return_value=articles):
            queue.scan(self.root, self.job())

    def claim(self):
        self.add()
        response = {'articles': [{'id': 'new', 'title': 'test title',
            'url': 'https://example.test/article', 'content': 'first\n\nlast',
            'content_available': True}]}
        with patch.object(queue.cloud, 'load_key', return_value='offline'), patch.object(
                queue.cloud, 'search', return_value=response):
            return queue.next_item(self.root, self.job())

    def bind(self):
        self.claim()
        job = self.job()
        job['items']['new'].update(status='created', doc_url='https://example.feishu.cn/docx/NewDoc')
        queue.save(self.root / 'job.json', job)

    def receipt(self, token='NewDoc', identity='user'):
        path = self.root / 'receipt.json'
        path.write_text(json.dumps({'ok': True, 'identity': identity,
            'data': {'document': {'document_id': token, 'content': 'first\n\nlast'}}}))
        return argparse.Namespace(id='new', readback=path.name,
                                  content_checked=True, images_checked=True)

    def test_initialization_requires_user_test_and_keeps_runtime(self):
        self.assertTrue((self.root / 'runtime/sync_queue.py').is_file())
        self.assertTrue((self.root / 'runtime/notehelper_client.py').is_file())
        self.assertTrue((self.root / 'RUN.md').is_file())
        self.assertIsNone(self.job()['schedule'])
        self.assertNotIn('offline-only', (self.root / 'job.json').read_text())
        self.args.user_tested = False
        with self.assertRaises(queue.cloud.ClientError):
            queue.initialize(Path(self.temp.name) / 'unconfirmed', self.args)

    def test_old_library_is_baseline_and_scan_is_idempotent(self):
        self.add()
        self.add()
        self.assertEqual(list(self.job()['items']), ['new'])
        self.assertEqual(self.job()['baseline'], ['old'])

    def test_scan_failure_does_not_advance_state(self):
        self.add()
        before = (self.root / 'job.json').read_bytes()
        with patch.object(queue, 'metadata', side_effect=queue.cloud.ClientError('offline failure')):
            with self.assertRaises(queue.cloud.ClientError):
                queue.scan(self.root, self.job())
        self.assertEqual(before, (self.root / 'job.json').read_bytes())

    def test_metadata_paginates_past_known_ids(self):
        pages = [{'articles': [{'id': 'old'}], 'next_after': 1},
                 {'articles': [{'id': 'late-new'}], 'next_after': None}]
        with patch.object(queue.cloud, 'load_key', return_value='offline'), patch.object(
                queue.cloud, 'search', side_effect=pages) as request:
            result = queue.metadata(self.config)
        self.assertEqual(set(result), {'old', 'late-new'})
        self.assertEqual(request.call_args.args[3], 1)

    def test_repeated_page_is_not_silently_complete(self):
        page = {'articles': [{'id': 'old'}], 'next_after': 1}
        with patch.object(queue.cloud, 'load_key', return_value='offline'), patch.object(
                queue.cloud, 'search', return_value=page):
            with self.assertRaises(queue.cloud.ClientError):
                queue.metadata(self.config)

    def test_claim_persists_before_creation_and_resume_never_claims_again(self):
        result = self.claim()
        self.assertTrue(result['new_claim'])
        self.assertEqual(self.job()['items']['new']['status'], 'creating')
        self.assertEqual((self.root / result['source_file']).read_text(), 'first\n\nlast')
        with patch.object(queue.cloud, 'search') as request:
            resumed = queue.next_item(self.root, self.job())
        self.assertFalse(resumed['new_claim'])
        request.assert_not_called()

    def test_failed_cloud_body_remains_pending(self):
        self.add()
        with patch.object(queue.cloud, 'load_key', return_value='offline'), patch.object(
                queue.cloud, 'search', side_effect=queue.cloud.ClientError('wrong id')):
            with self.assertRaises(queue.cloud.ClientError):
                queue.next_item(self.root, self.job())
        self.assertEqual(self.job()['items']['new']['status'], 'pending')
        self.assertEqual(list((self.root / 'out').iterdir()), [])

    def test_readback_wrong_id_or_bot_does_not_finish(self):
        self.bind()
        for token, identity in [('WrongDoc', 'user'), ('NewDoc', 'bot')]:
            with self.assertRaises(queue.cloud.ClientError):
                queue.complete(self.root, self.job(), self.receipt(token, identity))
        self.assertEqual(self.job()['items']['new']['status'], 'created')

    def test_verification_flags_are_required(self):
        self.bind()
        args = self.receipt()
        args.images_checked = False
        with self.assertRaises(queue.cloud.ClientError):
            queue.complete(self.root, self.job(), args)

    def test_only_verified_item_done_and_second_run_idle(self):
        self.bind()
        result = queue.complete(self.root, self.job(), self.receipt())
        self.assertEqual(result['status'], 'done')
        self.add()
        self.assertEqual(queue.next_item(self.root, self.job())['status'], 'idle')
        self.assertEqual(self.job()['items']['new']['status'], 'done')

    def test_two_processes_cannot_write_state_together(self):
        with queue.locked(self.root):
            with self.assertRaises(queue.cloud.ClientError):
                with queue.locked(self.root):
                    self.fail('second lock unexpectedly acquired')
        self.assertFalse((self.root / '.sync.lock').exists())

    def test_cannot_ack_without_bind_or_with_external_receipt(self):
        self.claim()
        with self.assertRaises(queue.cloud.ClientError):
            queue.complete(self.root, self.job(), self.receipt())
        job = self.job()
        job['items']['new'].update(status='created', doc_url='https://example.feishu.cn/docx/NewDoc')
        args = self.receipt()
        args.readback = '../external.json'
        with self.assertRaises(queue.cloud.ClientError):
            queue.complete(self.root, job, args)

    def test_bind_is_cli_visible_and_cannot_change_document(self):
        self.claim()
        result = self.main(['bind', '--id', 'new', '--doc-url', 'https://example.feishu.cn/docx/NewDoc'])
        self.assertFalse(result['verified'])
        with self.assertRaises(queue.cloud.ClientError):
            self.main(['bind', '--id', 'new', '--doc-url', 'https://example.feishu.cn/docx/AnotherDoc'])
        self.assertEqual(self.job()['items']['new']['doc_url'], 'https://example.feishu.cn/docx/NewDoc')

    def test_schedule_receipt_does_not_create_task_or_allow_second_task(self):
        result = self.main(['schedule-receipt', '--task-id', 'offline-task',
                            '--next-run', '2026-10-23T09:00:00+08:00', '--trial-id', 'offline-trial'])
        self.assertTrue(result['host_receipt_recorded'])
        with self.assertRaises(queue.cloud.ClientError):
            self.main(['schedule-receipt', '--task-id', 'duplicate-task',
                       '--next-run', '2026-10-23T09:00:00+08:00', '--trial-id', 'other-trial'])
        self.assertEqual(self.main(['status'])['schedule']['task_id'], 'offline-task')

    def test_existing_job_never_reinitializes(self):
        before = (self.root / 'job.json').read_bytes()
        with patch.object(queue, 'metadata') as query:
            with self.assertRaises(queue.cloud.ClientError):
                queue.initialize(self.root, self.args)
            query.assert_not_called()
        self.assertEqual(before, (self.root / 'job.json').read_bytes())

    def test_scheduled_task_uses_recorded_file_not_ambient_key(self):
        with patch.dict('os.environ', {'NOTEHELPER_API_KEY': 'different-ambient-dummy'}):
            self.assertEqual(queue.configured_key(self.config), 'offline-only')

    def deferred_pair(self):
        articles = {'old': {'id': 'old'},
                    'slow': {'id': 'slow', 'title': 'slow', 'result_after': 1},
                    'ready': {'id': 'ready', 'title': 'ready', 'result_after': 2}}
        with patch.object(queue, 'metadata', return_value=articles):
            queue.scan(self.root, self.job())
        return articles

    def body_reply(self, key, query, limit, after, content, note_id):
        return {'articles': [{'id': note_id, 'title': note_id,
            'content': '' if note_id == 'slow' else 'ready body',
            'content_available': note_id != 'slow'}]}

    def test_unready_first_item_does_not_block_ready_item(self):
        self.deferred_pair()
        with patch.object(queue.cloud, 'search', side_effect=self.body_reply):
            result = queue.next_item(self.root, self.job())
        self.assertEqual(result['id'], 'ready')
        self.assertEqual(result['deferred'], ['slow'])
        self.assertEqual(self.job()['items']['slow']['status'], 'pending')

    def test_disappeared_first_item_is_retained_without_blocking(self):
        articles = self.deferred_pair()
        del articles['slow']
        with patch.object(queue, 'metadata', return_value=articles):
            queue.scan(self.root, self.job())
        with patch.object(queue.cloud, 'search', side_effect=self.body_reply) as request:
            result = queue.next_item(self.root, self.job())
        self.assertEqual(result['id'], 'ready')
        self.assertEqual(request.call_count, 1)
        self.assertIn('slow', self.job()['items'])

    def test_stale_position_defers_only_that_item(self):
        self.deferred_pair()
        def reply(*args):
            if args[-1] == 'slow':
                raise queue.cloud.SelectionError('position changed')
            return self.body_reply(*args)
        with patch.object(queue.cloud, 'search', side_effect=reply):
            result = queue.next_item(self.root, self.job())
        self.assertEqual(result['id'], 'ready')
        self.assertEqual(result['deferred'], ['slow'])

    def test_per_run_skip_retains_uncertain_write_for_next_run(self):
        result = self.claim()
        result = queue.next_item(self.root, self.job(), skip_ids=['new'])
        self.assertEqual(result['status'], 'retry_pending')
        self.assertEqual(self.job()['items']['new']['status'], 'creating')
        resumed = queue.next_item(self.root, self.job())
        self.assertFalse(resumed['new_claim'])
        self.assertEqual(resumed['id'], 'new')


if __name__ == '__main__':
    unittest.main(verbosity=2)
