import argparse,contextlib,io,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
SCRIPTS=Path(__file__).resolve().parents[1]/'notebook-feishu-onboarding/scripts'
sys.path.insert(0,str(SCRIPTS))
import sync_queue as q
from record_plan import topic_fields
class V09(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)/'job';self.root.mkdir();self.board={'base_token':'Test','tables':{'选题池':'Topics'}}
  self.job={'items':{'n':{'status':'created','doc_url':'https://test.feishu.cn/docx/Doc'}},'board':self.board,'config':'offline','baseline':[]}
  q.save(self.root/'job.json',self.job)
 def run_cli(self,args):
  o=io.StringIO()
  with patch.object(sys,'argv',['q','--job',str(self.root)]+args),contextlib.redirect_stdout(o):q.main()
  return json.loads(o.getvalue())
 def test_manual_and_scheduler_cannot_claim_together(self):
  self.run_cli(['run-claim','--owner','manual'])
  with self.assertRaises(q.cloud.ClientError):self.run_cli(['run-claim','--owner','scheduled'])
 def test_other_owner_cannot_write_or_release(self):
  self.run_cli(['run-claim','--owner','manual'])
  for a in [['--owner','other','index-start','--id','n'],['run-release','--owner','other']]:
   with self.assertRaises(q.cloud.ClientError):self.run_cli(a)
 def test_second_index_claim_is_recovery(self):
  self.run_cli(['run-claim','--owner','manual'])
  a=['--owner','manual','index-start','--id','n'];self.assertTrue(self.run_cli(a)['new_claim']);self.assertFalse(self.run_cli(a)['new_claim'])
 def test_completed_select_does_not_fetch_again(self):
  self.job['items']['n']['status']='done';q.save(self.root/'job.json',self.job)
  self.run_cli(['run-claim','--owner','manual'])
  with patch.object(q.cloud,'search',side_effect=AssertionError('duplicate fetched')):
   self.assertTrue(self.run_cli(['--owner','manual','select','--id','n','--query','title'])['duplicate'])
 def test_repeat_pull_does_not_reset_user_state(self):
  fields=topic_fields({'id':'n','title':'title'},'doc',existing={'选题状态':['已拍摄'],'录制与剪辑版本':[{'id':'recOne'}]});self.assertEqual(fields,{})
 def test_missing_metrics_are_not_zero_or_current_date(self):
  fields=topic_fields({'id':'n','title':'title'},'doc');self.assertNotIn('发布时间',fields);self.assertNotIn('点赞',fields);self.assertEqual(fields['选题状态'],['待研究'])
 def test_complete_without_index_cannot_mark_done(self):
  rec={'ok':True,'identity':'user','data':{'document':{'document_id':'Doc','content':'body'}}};q.save(self.root/'receipt.json',rec)
  with self.assertRaises(q.cloud.ClientError):q.complete(self.root,self.job,argparse.Namespace(id='n',readback='receipt.json',content_checked=True,images_checked=True,index_readback=None))
  self.assertEqual(self.job['items']['n']['status'],'created')
 def test_scan_relocates_manual_selection_query_together(self):
  self.job['items']['n']={'status':'pending','query':'old query','metadata':{'id':'n','result_after':0}}
  with patch.object(q,'metadata',return_value={'n':{'id':'n','result_after':8}}):q.scan(self.root,self.job)
  self.assertEqual(self.job['items']['n']['query'],'');self.assertEqual(self.job['items']['n']['metadata']['result_after'],8)
 def test_public_template_has_no_creator_coordinates(self):
  text=(SCRIPTS.parent/'assets/feishu-template.json').read_text();self.assertNotIn('敏敏',text);self.assertNotIn('/Users/',text)
  import re
  self.assertIsNone(re.search(r'\b(?:tbl|fld|rec)[A-Za-z0-9]{8,}\b',text))
if __name__=='__main__':unittest.main()
