"""Patch regressions: isolated job, fake cloud; no credentials or live writes."""
import contextlib, hashlib, io, json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'notebook-feishu-onboarding/scripts'))
import sync_queue as q

class PatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); (self.root/'out').mkdir()
        self.job = dict(items={}, baseline=['old'], board={'base_token':'Fake'}, config='offline',
                        target='fake-folder', cadence='manual', timezone='Asia/Shanghai',
                        schedule=None, user_test_confirmed=False)
        self.store(); q.save(self.root/'.run.lock', {'owner':'test'})
        self.guard = patch.object(q.cloud, 'request', side_effect=AssertionError('real cloud forbidden'))
        self.guard.start(); self.addCleanup(self.guard.stop)
    def store(self): q.save(self.root/'job.json', self.job)
    def load(self): return json.loads((self.root/'job.json').read_text())
    def cli(self, *args):
        out = io.StringIO()
        with patch.object(sys,'argv',['q','--job',str(self.root),'--owner','test',*args]), contextlib.redirect_stdout(out): q.main()
        return json.loads(out.getvalue())
    def final(self, *extra):
        return self.cli('finalize','--cadence','every 30 minutes','--timezone','Asia/Shanghai',
                        '--test-note-id','n','--test-doc-url','https://test.feishu.cn/docx/Doc',*extra)
    def orphan(self, text='body'):
        self.job['items']['n']={'status':'pending','metadata':{'id':'n','result_after':50},'query':'title'}
        self.store(); self.cache=self.root/'out'/(hashlib.sha256(b'n').hexdigest()[:24]+'.md')
        self.cache.write_text(text)
    def reply(self, **changes):
        a=dict(id='n', title='title', content='body', content_available=True);a.update(changes)
        return {'articles':[a]}
    def recover(self, *args): return self.cli('recover-source','--id','n',*args)
    def test_second_page_selection_and_exact_id(self):
        a={'id':'n','result_after':50}
        with patch.object(q,'configured_key',return_value='fake'), patch.object(q.cloud,'search',return_value={'articles':[a]}) as call:
            self.cli('select','--id','n','--query','title','--after','50')
        self.assertEqual(call.call_args.args[3:5],(50,False))
        self.assertEqual(self.load()['items']['n']['metadata']['result_after'],50)
    def test_negative_offset_never_fetches(self):
        with patch.object(q.cloud,'search') as call:
            with self.assertRaises(q.cloud.ClientError): self.cli('select','--id','n','--query','title','--after','-1')
            call.assert_not_called()
    def test_wrong_page_does_not_enqueue(self):
        with patch.object(q,'configured_key',return_value='fake'), patch.object(q.cloud,'search',return_value={'articles':[{'id':'other'}]}):
            with self.assertRaises(q.cloud.ClientError): self.cli('select','--id','n','--query','title','--after','50')
        self.assertEqual(self.load()['items'],{})
    def test_finalize_preserves_all_existing_records(self):
        self.job['items']={'n':{'status':'done','doc_url':'https://test.feishu.cn/docx/Doc','record_id':'recOne'},'other':{'status':'pending'}};self.store()
        before=self.load(); result=self.final('--user-tested'); after=self.load()
        for key in ('baseline','items','board','target','schedule'): self.assertEqual(before[key],after[key])
        self.assertTrue(after['user_test_confirmed']);self.assertFalse(result['scheduled'])
        self.assertEqual(after['test_note_id'],'n')
    def test_finalize_requires_acceptance_completed_item_and_matching_url(self):
        for status,url,flag in [('done','https://test.feishu.cn/docx/Doc',[]),('pending','https://test.feishu.cn/docx/Doc',['--user-tested']),('done','https://test.feishu.cn/docx/Other',['--user-tested'])]:
            self.job['items']={'n':{'status':status,'doc_url':url}};self.store();before=self.load()
            with self.assertRaises(q.cloud.ClientError):self.final(*flag)
            self.assertEqual(before,self.load())
    def test_schedule_receipt_rejects_unfinalized_manual_job(self):
        with self.assertRaises(q.cloud.ClientError):self.cli('schedule-receipt','--task-id','fake','--next-run','later','--trial-id','trial')
        self.assertIsNone(self.load()['schedule'])
    def test_recovery_claims_once_and_next_never_recreates(self):
        self.orphan()
        with patch.object(q,'configured_key',return_value='fake'),patch.object(q.cloud,'search',return_value=self.reply()) as call:
            self.assertTrue(self.recover('--no-write-confirmed')['new_claim'])
            self.assertFalse(self.recover('--no-write-confirmed')['new_claim'])
            self.assertFalse(self.cli('next')['new_claim'])
            self.assertEqual(call.call_count,1); self.assertEqual(call.call_args.args[2:],(1,50,True,'n'))
    def test_recovery_without_no_write_check_leaves_cache_and_state(self):
        self.orphan();before=self.load()
        with self.assertRaises(q.cloud.ClientError):self.recover()
        self.assertEqual(before,self.load());self.assertEqual(self.cache.read_text(),'body')
    def test_recovery_mismatched_body_or_id_or_unready_keeps_pending(self):
        for reply in (self.reply(content='different'),self.reply(id='other'),self.reply(content_available=False)):
            self.orphan();before=self.load()
            with patch.object(q,'configured_key',return_value='fake'),patch.object(q.cloud,'search',return_value=reply):
                with self.assertRaises(q.cloud.ClientError):self.recover('--no-write-confirmed')
            self.assertEqual(before,self.load());self.assertEqual(self.cache.read_text(),'body')
    def test_recovery_refuses_symlink(self):
        self.orphan();self.cache.unlink();self.cache.symlink_to(self.root/'job.json')
        with self.assertRaises(q.cloud.ClientError):self.recover('--no-write-confirmed')
    def test_recovery_wrong_cloud_position_preserves_cache(self):
        self.orphan();before=self.load()
        with patch.object(q,'configured_key',return_value='fake'),patch.object(q.cloud,'search',side_effect=q.cloud.SelectionError('changed')):
            with self.assertRaises(q.cloud.ClientError):self.recover('--no-write-confirmed')
        self.assertEqual(before,self.load())
    def test_recovery_does_not_reset_unknown_external_write(self):
        self.orphan();self.job['items']['n']['status']='creating';self.store()
        with self.assertRaises(q.cloud.ClientError):self.recover('--no-write-confirmed')
        self.assertEqual(self.load()['items']['n']['status'],'creating')
