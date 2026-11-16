import json
import sqlite3
import unittest
from unittest.mock import patch
from test_openrouter import Fixture
from test_analysis import report
from r52.assistant.analysis import Analysis
from r52.db.schema import V6_ANALYSIS_HISTORY
from r52.errors import AppError

class HistoryTests(Fixture):
 def run_report(self,key='saved-report-001',**kw):
  body=dict(kind='card',product_ids=[1],criterion='regular',question='',use_ai=False,advanced=False,paid_confirmed=False,idempotency_key=key);body.update(kw);return Analysis(self.assistant).run(body)
 def test_local_and_two_models_all_preserved(self):
  self.enable();self.run_report('saved-local-001')
  with patch('r52.assistant.analysis.OpenRouterClient.chat',return_value=report()):
   self.run_report('saved-mini-001',use_ai=True,paid_confirmed=True)
   self.run_report('saved-sonnet-001',use_ai=True,advanced=True,paid_confirmed=True)
  a=Analysis(self.assistant);listing=a.history([1],'card')
  self.assertEqual({x['channel'] for x in listing['items']},{'local','mini','sonnet'})
  with patch('r52.assistant.analysis.OpenRouterClient.chat',side_effect=AssertionError('must never bill for reading')):
   for x in listing['items']:
    self.assertEqual(a.saved(x['key'])['saved_key'],x['key'])
  self.assertEqual(self.cloud.status()['calls'],2)
 def test_new_instance_can_read_saved_results(self):
  self.run_report();self.assertEqual(Analysis(self.assistant).history([1],'card')['total'],1)
 def test_history_scopes_exact_not_partial(self):
  self.run_report('saved-compare-001',kind='compare',product_ids=[1,2])
  a=Analysis(self.assistant)
  self.assertEqual(a.history([1],'compare')['total'],0)
  self.assertEqual(a.history([2,1],'compare')['total'],1)
 def test_pagination(self):
  for i in range(4):self.run_report('saved-version-%03d'%i)
  a=Analysis(self.assistant);x=a.history([1],'card',2,0);y=a.history([1],'card',2,2)
  self.assertEqual(x['total'],4);self.assertFalse(set(i['key'] for i in x['items'])&set(i['key'] for i in y['items']))
 def test_missing_saved_report(self):
  with self.assertRaises(AppError):Analysis(self.assistant).saved('missing-report-001')
 def test_unfinished_report_blocks_repayment(self):
  with self.db.write() as c:c.execute("INSERT INTO assistant_analysis_request(key,fingerprint,created_at) VALUES('pending-report-001','x','2026-11-11')")
  with self.assertRaises(AppError) as e:Analysis(self.assistant).saved('pending-report-001')
  self.assertEqual(e.exception.code,'ASSISTANT_REQUEST_UNCERTAIN')
 def test_v5_results_backfilled_not_deleted(self):
  c=sqlite3.connect(':memory:');c.executescript('CREATE TABLE assistant_analysis_request(key TEXT PRIMARY KEY,fingerprint TEXT,created_at TEXT,result_json TEXT);')
  old={'kind':'card','model':'openai/gpt-5-mini','facts':[{'id':'scope','product_ids':[7]}],'comments':[],'llm':{'used':True,'calls':1,'cost_usd':.003}}
  c.execute('INSERT INTO assistant_analysis_request VALUES(?,?,?,?)',('old-report-001','f','2026-11-11',json.dumps(old)))
  c.executescript(V6_ANALYSIS_HISTORY);row=c.execute('SELECT scope_json,result_json FROM assistant_analysis_request').fetchone()
  self.assertEqual(json.loads(row[0]),[7]);self.assertEqual(json.loads(row[1]),old)
 def test_saved_snapshots_are_historical_not_live(self):
  self.run_report()
  with self.db.write() as c:c.execute("UPDATE product SET title='Changed title' WHERE id=1")
  result=Analysis(self.assistant).saved('saved-report-001')
  self.assertEqual(result['snapshots'][0]['title'],'Alpha laptop')
 def test_failed_model_result_is_still_saved(self):
  self.enable()
  with patch('r52.assistant.analysis.OpenRouterClient.chat',return_value=report([{'fact_ids':['wrong'],'text':'invalid'}])):
   self.run_report(use_ai=True,paid_confirmed=True)
  out=Analysis(self.assistant).saved('saved-report-001')
  self.assertEqual(out['llm']['rejection_code'],'UNKNOWN_FACT')

if __name__=='__main__':unittest.main()
