"""Offline read-only analysis regression suite."""
import copy
import json
import threading
import unittest
from unittest.mock import patch
from test_openrouter import Fixture, response, plan
from r52.assistant.analysis import Analysis, build_facts, fact, ADVANCED_MODEL
from r52.assistant.openrouter import OpenRouterClient, MODEL
from r52.errors import AppError


def report(comments=None,reference=""):
    return response({"comments":comments or [],"reference_text":reference})


class AnalysisTests(Fixture):
    def request(self,**changes):
        body=dict(kind="card",product_ids=[1],criterion="regular",question="",use_ai=False,advanced=False,paid_confirmed=False,idempotency_key="analysis-key-001")
        body.update(changes)
        return Analysis(self.assistant).run(body)

    def test_local_report_is_free(self):
        with patch('r52.assistant.analysis.OpenRouterClient.chat',side_effect=AssertionError('paid')):
            out=self.request()
        self.assertTrue(out['facts']);self.assertEqual(out['llm']['calls'],0)
        self.assertTrue(any(f['id']=='p1-missing' for f in out['facts']))

    def test_requires_explicit_paid_confirmation(self):
        self.enable()
        with self.assertRaises(AppError):self.request(use_ai=True)

    def test_off_does_not_call_cloud(self):
        with patch('r52.assistant.analysis.OpenRouterClient.chat',side_effect=AssertionError('paid')):
            out=self.request(use_ai=True,paid_confirmed=True)
        self.assertFalse(out['llm']['used'])

    def test_ai_comment_uses_fact_references(self):
        self.enable()
        p=report([{'fact_ids':['p1-missing'],'text':'Важные сведения отсутствуют; уточните их перед выбором.'}])
        with patch('r52.assistant.analysis.OpenRouterClient.chat',return_value=p) as client:
            out=self.request(use_ai=True,paid_confirmed=True)
        self.assertTrue(out['llm']['used']);client.assert_called_once()
        self.assertEqual(out['model'],MODEL)

    def test_bad_fact_reference_falls_back_with_reason(self):
        self.enable()
        with patch('r52.assistant.analysis.OpenRouterClient.chat',return_value=report([{'fact_ids':['nonexistent'],'text':'Факт'}])):
            out=self.request(use_ai=True,paid_confirmed=True)
        self.assertFalse(out['llm']['used']);self.assertEqual(out['llm']['rejection_code'],'UNKNOWN_FACT')
        self.assertTrue(out['facts']);self.assertAlmostEqual(out['llm']['cost_usd'],.001)

    def test_invented_number_rejected(self):
        self.enable()
        with patch('r52.assistant.analysis.OpenRouterClient.chat',return_value=report([{'fact_ids':['p1-missing'],'text':'Цена 999999 рублей'}])):
            out=self.request(use_ai=True,paid_confirmed=True)
        self.assertEqual(out['llm']['rejection_code'],'UNSUPPORTED_NUMBER')

    def test_missing_product_not_charged(self):
        self.enable()
        with patch('r52.assistant.analysis.OpenRouterClient.chat',side_effect=AssertionError('paid')):
            with self.assertRaises(AppError):self.request(product_ids=[9999],use_ai=True,paid_confirmed=True)
        self.assertEqual(self.cloud.status()['calls'],0)

    def test_duplicate_request_cached(self):
        self.enable()
        with patch('r52.assistant.analysis.OpenRouterClient.chat',return_value=report()) as client:
            a=self.request(use_ai=True,paid_confirmed=True);b=self.request(use_ai=True,paid_confirmed=True)
        self.assertEqual(a,b);client.assert_called_once()

    def test_parallel_paid_once(self):
        self.enable();out=[];errors=[]
        def worker():
            try:out.append(self.request(use_ai=True,paid_confirmed=True))
            except BaseException as e:errors.append(e)
        with patch('r52.assistant.analysis.OpenRouterClient.chat',return_value=report()) as client:
            threads=[threading.Thread(target=worker) for _ in range(3)]
            for t in threads:t.start()
            for t in threads:t.join(10)
        self.assertFalse(errors);self.assertEqual(len(out),3);client.assert_called_once()

    def test_same_key_changed_body_rejected(self):
        self.request()
        with self.assertRaises(AppError) as cm:self.request(kind='quality')
        self.assertEqual(cm.exception.code,'IDEMPOTENCY_CONFLICT')

    def test_no_writes_to_product_tables(self):
        with self.db.read() as c:
            before=[tuple(r) for r in c.execute('SELECT * FROM product ORDER BY id')]
        self.request(kind='quality',product_ids=[1,2])
        with self.db.read() as c:
            after=[tuple(r) for r in c.execute('SELECT * FROM product ORDER BY id')]
            self.assertEqual(c.execute('SELECT count(*) FROM assistant_action').fetchone()[0],0)
        self.assertEqual(before,after)

    def test_reference_explicitly_separate_from_facts(self):
        self.enable()
        with patch('r52.assistant.analysis.OpenRouterClient.chat',return_value=report(reference='Объём памяти — одна из характеристик устройства.')) as client:
            out=self.request(kind='reference',product_ids=[],question='Что такое память?',use_ai=True,paid_confirmed=True)
        payload=json.loads(client.call_args[0][0][1]['content'])
        self.assertEqual(payload['facts'],[]);self.assertTrue(out['reference_text'])
        self.assertIn('не данные вашей базы',out['note'])

    def test_reference_cannot_accept_cards(self):
        with self.assertRaises(AppError):self.request(kind='reference',question='Термин')

    def test_advanced_explicit_model_only(self):
        self.enable()
        with patch('r52.assistant.analysis.OpenRouterClient.chat',autospec=True,return_value=report()) as client:
            out=self.request(advanced=True,use_ai=True,paid_confirmed=True)
        self.assertEqual(client.call_args[0][0].model,ADVANCED_MODEL)
        self.assertEqual(out['model'],ADVANCED_MODEL)

    def test_advanced_reserve_is_larger(self):
        self.enable();self.app.settings.update({'assistant.openrouter_budget_cents':20})
        with patch('r52.assistant.analysis.OpenRouterClient.chat',side_effect=AssertionError('paid')):
            out=self.request(advanced=True,use_ai=True,paid_confirmed=True)
        self.assertEqual(out['llm']['calls'],0);self.assertIn('бюджет',out['note'])

    def test_advanced_unknown_reserve_persisted(self):
        self.enable()
        with patch('r52.assistant.analysis.OpenRouterClient.chat',side_effect=AppError('OPENROUTER_TIMEOUT',message='timeout')):
            self.request(advanced=True,use_ai=True,paid_confirmed=True)
        with self.db.read() as c:self.assertEqual(c.execute('SELECT reserve_usd FROM assistant_llm_call').fetchone()[0],.50)

    def test_no_automatic_fallback_to_advanced(self):
        self.enable()
        with patch('r52.assistant.analysis.OpenRouterClient.chat',autospec=True,side_effect=AppError('UNAVAILABLE',message='failed')) as client:
            out=self.request(use_ai=True,paid_confirmed=True)
        client.assert_called_once();self.assertEqual(client.call_args[0][0].model,MODEL)
        self.assertFalse(out['llm']['used'])

    def test_invalid_modes_ids_and_boolean_types(self):
        for change in [dict(kind='delete'),dict(product_ids=[True]),dict(product_ids=[1,1]),dict(advanced='yes'),dict(kind='compare'),dict(criterion='profit'),dict(use_ai=1),dict(kind='reference',product_ids=[],question='')]:
            with self.subTest(change=change),self.assertRaises(AppError):self.request(**change)

    def test_compare_ordinary_price_groups_currency(self):
        with self.db.write() as c:
            c.execute("UPDATE product SET last_currency='RUB', last_regular_minor=10000,last_lowest_minor=9000,last_lowest_role='card' WHERE id=1")
            c.execute("UPDATE product SET last_currency='USD', last_regular_minor=500,last_lowest_minor=500,last_lowest_role='regular' WHERE id=2")
        out=self.request(kind='compare',product_ids=[1,2])
        ranking=[f for f in out['facts'] if f['id'].startswith('ranking-')]
        self.assertEqual(len(ranking),2)
        self.assertTrue(any('разные валюты' in f['text'].lower() for f in out['facts']))

    def test_same_titles_only_candidates_for_duplicate(self):
        with self.db.write() as c:c.execute("UPDATE product SET title='Alpha laptop' WHERE id=2")
        out=self.request(kind='quality',product_ids=[1,2])
        dup=[f for f in out['facts'] if f['id'].startswith('duplicate-')]
        self.assertEqual(len(dup),1);self.assertIn('ручную',dup[0]['text'])

    def test_no_unverified_trend(self):
        out=self.request()
        self.assertTrue(any('Динамика не интерпретируется' in f['text'] for f in out['facts']))

    def test_response_plan_cannot_contain_actions(self):
        with self.assertRaises(ValueError):Analysis.validate({'comments':[],'reference_text':'','actions':[{}]},[],'card')

    def test_model_whitelist(self):
        with self.assertRaises(AppError):OpenRouterClient(model='arbitrary/model')


class ChatFixTests(Fixture):
    def test_rejection_reason_and_useful_facts(self):
        self.enable()
        with patch('r52.assistant.cloud.OpenRouterClient.chat',return_value=response({'operation':'unknown'})):
            out=self.send('Мне важнее минимальная цена. Какие данные у тебя есть?')
        data=out['reply']['data']
        self.assertEqual(data['llm']['rejection_code'],'PLAN_FORMAT')
        self.assertNotIn('Не понял',out['reply']['text'])
        self.assertTrue(data['cards'])

    def test_clarification_can_reference_supplied_model_number(self):
        with self.db.write() as c:c.execute("UPDATE product SET title='RTX 5070' WHERE id=1")
        self.enable()
        with patch('r52.assistant.cloud.OpenRouterClient.chat',return_value=response(plan('clarify',clarification='Сравнить RTX 5070 по цене?'))):
            out=self.send('Помоги выбрать')
        self.assertTrue(out['reply']['data']['llm']['used'])
        self.assertTrue(out['reply']['data']['cards'])

    def test_unverified_price_in_answer_still_rejected(self):
        self.enable()
        with patch('r52.assistant.cloud.OpenRouterClient.chat',return_value=response(plan('answer',answer=[{'product_id':1,'text':'Цена 987654 рублей'}]))):
            out=self.send()
        self.assertEqual(out['reply']['data']['llm']['rejection_code'],'UNSUPPORTED_NUMBERS')


if __name__=='__main__':unittest.main()
