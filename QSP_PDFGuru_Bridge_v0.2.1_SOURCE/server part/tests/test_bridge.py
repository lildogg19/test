import base64
import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi.testclient import TestClient
from PIL import Image
from server.store import Store
from server.settings import Settings
from server.app import create_app
from server.matching import match_invoice, source_key
from server.invoice import parse_invoice
from server.worker import Worker
from server.browser import LoginRequired

FIXTURE = Path(__file__).parent/'invoice.json'

class FakeBrowser:
    def recognize(self, source, prompt, stage):
        stage('Тестовое распознавание')
        return FIXTURE.read_text(encoding='utf-8-sig')

class LoginBrowser:
    def recognize(self, *args): raise LoginRequired('Требуется вход')

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name))
        self.settings=Settings(data_dir=Path(self.tmp.name),worker_enabled=False,api_token='a'*40)
        self.invoice=json.loads(FIXTURE.read_text(encoding='utf-8-sig'))
        self.refs={'suppliers':[{'id':'supplier1','name':'Поставщик','bin':self.invoice['supplier']['bin']}],
                   'organizations':[{'id':'org1','name':'Покупатель','bin':self.invoice['buyer']['bin']}],
                   'items':[{'id':'item1','name':self.invoice['items'][0]['name'],'sku':'P01','unit':'шт','service':False}]}
        self.store.set_refs('base1',self.refs)
        self.worker=Worker(self.settings,self.store)

    def add(self,key='a',actor='Бухгалтер 1',base='base1'):
        source=Path(self.tmp.name)/'file.jpeg';source.write_bytes(b'test')
        return self.store.create(base,actor,key,'Счёт.jpeg',source,'digest')[0]

    def test_fifo_shared_users_and_restart(self):
        one=self.add('1');two=self.add('2','Бухгалтер 2')
        queue=self.store.queue('base1')['jobs']
        self.assertEqual([j['position'] for j in queue],[1,2])
        first=self.store.claim();self.assertEqual(first['id'],one['id'])
        self.assertIsNone(self.store.claim())
        queue=self.store.queue('base1')['jobs']
        self.assertEqual(queue[0]['actor'],'Бухгалтер 1');self.assertEqual(queue[1]['position'],1)
        reopened=Store(Path(self.tmp.name));reopened.recover()
        self.assertEqual(reopened.get(one['id'],'base1')['status'],'failed')
        self.assertEqual(reopened.claim()['id'],two['id'])

    def test_concurrent_claim_only_one(self):
        self.add('1');self.add('2')
        with ThreadPoolExecutor(4) as pool:
            results=list(pool.map(lambda _:self.store.claim(),range(4)))
        self.assertEqual(sum(r is not None for r in results),1)

    def test_idempotency(self):
        first=self.add();second=self.add()
        self.assertEqual(first['id'],second['id'])
        with self.assertRaises(ValueError):
            self.store.create('base1','Другой бухгалтер','a','file.jpeg','file','other_digest')

    def test_scope_and_ambiguous_bin(self):
        self.assertFalse(match_invoice(self.store,'other_base',self.invoice)['all_matched'])
        self.refs['suppliers'].append({'id':'supplier2','name':'Дубликат','bin':self.invoice['supplier']['bin']})
        self.store.set_refs('base1',self.refs)
        self.assertEqual(match_invoice(self.store,'base1',self.invoice)['supplier']['status'],'ambiguous')

    def test_supplier_mapping_and_deleted_reference(self):
        self.refs['items'][0]['name']='Код нашего товара';self.store.set_refs('base1',self.refs)
        self.store.save_mapping('base1','supplier1',source_key(self.invoice['items'][0]),'item1')
        self.assertTrue(match_invoice(self.store,'base1',self.invoice)['all_matched'])
        self.refs['items']=[];self.store.set_refs('base1',self.refs)
        self.assertFalse(match_invoice(self.store,'base1',self.invoice)['all_matched'])

    def test_fuzzy_never_auto_selects(self):
        self.refs['items'][0]['name']='Набор прокладок 120 4 новый';self.store.set_refs('base1',self.refs)
        item=match_invoice(self.store,'base1',self.invoice)['items'][0]
        self.assertEqual(item['status'],'suggested');self.assertIsNone(item['id'])

    def test_invoice_checks_preserve_missing_fields(self):
        data,errors=parse_invoice(json.dumps(self.invoice,ensure_ascii=False))
        self.assertEqual(errors,[]);self.assertIsNone(data['subtotal']);self.assertIsNone(data['total_quantity'])
        self.assertIsNone(data['supplier']['iban'])
        data['items'][0]['price']=3100
        _,errors=parse_invoice(json.dumps(data));self.assertTrue(errors)
        with self.assertRaises(ValueError):parse_invoice('{"total":35200')

    def test_supplier_requisites_are_preserved(self):
        self.invoice['supplier'].update({
            'address':'г. Экибастуз, ул. Целинная 70-25',
            'kbe':'19',
            'iban':'KZ866010002106902313',
            'bank_name':'АО Народный Банк Казахстана',
            'bic':'HSBKKZKX',
        })
        data,errors=parse_invoice(json.dumps(self.invoice,ensure_ascii=False))
        self.assertEqual(errors,[])
        self.assertEqual(data['supplier']['iban'],'KZ866010002106902313')
        self.assertEqual(data['supplier']['bic'],'HSBKKZKX')

    def test_vat_and_row_number_checks(self):
        self.invoice['vat_mode']='with_vat';self.invoice['items'][0]['vat_amount']=5632
        self.invoice['items'][0]['vat_rate']=16;self.invoice['total']=40832
        self.assertEqual(parse_invoice(json.dumps(self.invoice))[1],[])
        self.invoice['vat']=100
        self.assertTrue(parse_invoice(json.dumps(self.invoice))[1])
        self.invoice['vat']=None
        self.invoice['items'][0]['line_no']='2'
        self.assertTrue(parse_invoice(json.dumps(self.invoice))[1])

    def test_worker_success_and_login(self):
        self.add('1');job=self.store.claim();self.worker.run_one(FakeBrowser(),job)
        finished=self.store.get(job['id'],'base1');self.assertEqual(finished['status'],'completed')
        self.assertTrue(json.loads(finished['matches'])['all_matched'])
        self.assertTrue(self.store.retry(job['id'],'base1'))
        repeated=self.store.get(job['id'],'base1')
        self.assertEqual(repeated['status'],'queued');self.assertIsNone(repeated['result']);self.assertIsNone(repeated['matches'])
        job=self.store.claim();self.worker.run_one(FakeBrowser(),job)
        self.add('2');job=self.store.claim();self.worker.run_one(LoginBrowser(),job)
        self.assertEqual(self.store.get(job['id'],'base1')['status'],'awaiting_login')
        self.assertEqual(self.store.queue('base1')['worker']['status'],'awaiting_login')

    def test_api_upload_result_confirmation_and_auth(self):
        client=TestClient(create_app(self.settings,self.store))
        self.assertEqual(client.get('/queue',params={'base':'base1'}).status_code,401)
        client.headers['X-QSP-Token']='a'*40
        b=io.BytesIO();Image.new('RGB',(50,50),'white').save(b,format='PNG')
        request={'base':'base1','actor':'Бухгалтер 1','request_key':'upload1','filename':'scan.png','content_base64':base64.b64encode(b.getvalue()).decode()}
        response=client.post('/jobs',json=request);self.assertEqual(response.status_code,200,response.text)
        job_id=response.json()['id'];self.assertFalse(client.post('/jobs',json=request).json()['created'])
        self.assertEqual(client.get('/jobs/'+job_id,params={'base':'other'}).status_code,404)
        job=self.store.claim();self.worker.run_one(FakeBrowser(),job)
        result=client.get('/jobs/'+job_id,params={'base':'base1'}).json()
        self.assertEqual(result['invoice']['number'],'107');self.assertTrue(result['matches']['all_matched'])
        confirmation={'base':'base1','supplier_id':'supplier1','organization_id':'org1','item_ids':['item1']}
        self.assertEqual(client.post('/jobs/'+job_id+'/confirm-matches',json=confirmation).status_code,200)
        self.assertEqual(client.get('/jobs/'+job_id+'/confirmed',params={'base':'base1'}).status_code,200)
        doc='b0d6bbaa-2698-4af5-915e-a216047f35e7'
        self.assertEqual(client.post('/jobs/'+job_id+'/imported',json={'base':'base1','document_id':doc}).status_code,200)
        self.assertEqual(client.get('/jobs/'+job_id,params={'base':'base1'}).json()['status'],'imported')
        self.assertEqual(client.post('/jobs/'+job_id+'/confirm-matches',json=confirmation).status_code,409)
        self.assertEqual(client.post('/jobs/'+job_id+'/imported',json={'base':'base1','document_id':'00000000-0000-0000-0000-000000000001'}).status_code,409)
        self.refs['items']=[];self.store.set_refs('base1',self.refs)
        self.assertEqual(client.get('/jobs/'+job_id+'/confirmed',params={'base':'base1'}).status_code,409)
        self.assertEqual(client.get('/jobs/'+job_id+'/source',params={'base':'base1'}).content,b.getvalue())

    def test_invalid_file_and_cross_key(self):
        client=TestClient(create_app(self.settings,self.store));client.headers['X-QSP-Token']='a'*40
        req={'base':'base1','actor':'x','request_key':'x','filename':'not.pdf','content_base64':base64.b64encode(b'not a pdf').decode()}
        self.assertEqual(client.post('/jobs',json=req).status_code,400)

    def test_worker_lock_excludes_second_process(self):
        other=Worker(self.settings,self.store)
        self.assertTrue(self.worker.acquire_lock())
        try:self.assertFalse(other.acquire_lock())
        finally:self.worker.lock_file.close()

if __name__=='__main__':unittest.main()
