import base64
import binascii
import hashlib
import json
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from .settings import Settings
from .store import Store
from .worker import Worker
from .matching import match_invoice, source_key

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')

class Upload(Strict):
    base: str = Field(min_length=1,max_length=100)
    actor: str = Field(min_length=1,max_length=100)
    request_key: str = Field(min_length=1,max_length=100)
    filename: str = Field(min_length=1,max_length=180)
    content_base64: str

class Reference(Strict):
    id: str
    name: str
    bin: str | None = None
    sku: str | None = None
    unit: str | None = None
    unit_id: str | None = None
    service: bool = False

class References(Strict):
    base: str
    suppliers: list[Reference]
    organizations: list[Reference]
    items: list[Reference]

class Confirmation(Strict):
    base: str
    supplier_id: str
    organization_id: str
    item_ids: list[str]

class Receipt(Strict):
    base: str
    document_id: str

def create_app(settings=None, store=None):
    settings=settings or Settings()
    store=store or Store(settings.data_dir)
    worker=Worker(settings,store)

    @asynccontextmanager
    async def lifespan(app):
        if len(settings.api_token.get_secret_value()) < 32 or settings.api_token.get_secret_value()=='CHANGE_TO_A_RANDOM_LONG_SECRET':
            raise RuntimeError('Set QSP_API_TOKEN in .env before starting')
        if settings.worker_enabled: worker.start()
        yield
        worker.stop.set()
        if worker.thread: worker.thread.join(timeout=5)

    app=FastAPI(title='QSP PDF Guru Bridge',version='0.2.1',lifespan=lifespan)
    app.state.store=store

    def authorize(x_qsp_token: Annotated[str | None,Header()] = None):
        expected=settings.api_token.get_secret_value()
        if not expected or not x_qsp_token or not secrets.compare_digest(expected,x_qsp_token):
            raise HTTPException(401,'Неверный ключ сервиса')

    def get_job(job_id,base):
        row=store.get(job_id,base)
        if not row: raise HTTPException(404,'Задание не найдено')
        return row

    secured=[Depends(authorize)]

    @app.get('/health',dependencies=secured)
    def health(): return {'ok':True,'version':app.version}

    @app.post('/jobs',dependencies=secured)
    def upload(req:Upload):
        if len(req.content_base64)>settings.max_upload_mb*1024*1024*4//3+8:
            raise HTTPException(413,'Файл слишком большой')
        try: content=base64.b64decode(req.content_base64,validate=True)
        except (ValueError,binascii.Error): raise HTTPException(400,'Некорректный Base64')
        if not content or len(content)>settings.max_upload_mb*1024*1024:
            raise HTTPException(413,'Файл пуст или превышает лимит')
        filename=req.filename.replace('\\','/').split('/')[-1]
        suffix=Path(filename).suffix.lower()
        if suffix not in {'.pdf','.png','.jpg','.jpeg','.tif','.tiff','.webp'}:
            raise HTTPException(400,'Поддерживаются PDF и изображения')
        # Check content, not merely the extension. Never silently truncate PDFs.
        try:
            if suffix=='.pdf':
                import fitz
                with fitz.open(stream=content,filetype='pdf') as pdf:
                    if pdf.needs_pass: raise ValueError('PDF защищён паролем')
                    if len(pdf)==0 or len(pdf)>settings.max_pdf_pages: raise ValueError('Превышен лимит страниц PDF')
            else:
                import io
                from PIL import Image
                with Image.open(io.BytesIO(content)) as image: image.verify()
        except Exception:
            raise HTTPException(400,'Не удалось прочитать файл: повреждение, пароль или лимит страниц')
        digest=hashlib.sha256(content).hexdigest()
        source=store.root/'uploads'/(uuid.uuid4().hex+suffix)
        source.write_bytes(content)
        try:
            row,created=store.create(req.base,req.actor,req.request_key,filename,source,digest)
        except ValueError as exc:
            source.unlink(missing_ok=True); raise HTTPException(409,str(exc))
        if not created: source.unlink(missing_ok=True)
        return {'id':row['id'],'status':row['status'],'created':created}

    @app.get('/queue',dependencies=secured)
    def queue(base:str): return store.queue(base)

    @app.get('/jobs/{job_id}',dependencies=secured)
    def job(job_id:str,base:str):
        row=get_job(job_id,base)
        result={k:row[k] for k in ['id','filename','actor','status','stage','created','updated','error']}
        if row['result']:
            result['invoice']=json.loads(row['result'])
            # Refresh using the latest reference snapshot; deleted references cannot linger.
            result['matches']=match_invoice(store,base,result['invoice'])
            result['confirmed_matches']=json.loads(row['matches'] or '{}')
        return result

    @app.post('/references',dependencies=secured)
    def references(req:References):
        payload=req.model_dump()
        for kind in ['suppliers','organizations','items']:
            ids=[r['id'] for r in payload[kind]]
            if len(ids)!=len(set(ids)): raise HTTPException(400,'Повтор идентификаторов справочника')
        store.set_refs(req.base,{k:payload[k] for k in ['suppliers','organizations','items']})
        return {'ok':True}

    @app.post('/jobs/{job_id}/confirm-matches',dependencies=secured)
    def confirm(job_id:str,req:Confirmation):
        row=get_job(job_id,req.base)
        if row['status']=='imported': raise HTTPException(409,'Счёт уже импортирован; сопоставление задания зафиксировано')
        if not row['result']: raise HTTPException(409,'Распознавание не завершено')
        invoice=json.loads(row['result']); refs=store.refs(req.base)
        if req.supplier_id not in {r['id'] for r in refs['suppliers']} or req.organization_id not in {r['id'] for r in refs['organizations']}:
            raise HTTPException(409,'Контрагент или организация отсутствует в актуальной выгрузке')
        if len(req.item_ids)!=len(invoice['items']) or any(i not in {r['id'] for r in refs['items']} for i in req.item_ids):
            raise HTTPException(409,'Не все строки сопоставлены с актуальным справочником')
        for item,target in zip(invoice['items'],req.item_ids):
            store.save_mapping(req.base,req.supplier_id,source_key(item),target)
        # Store operator-selected parties for explicit 1C import; do not mutate OCR originals.
        matches={'confirmed':True,'supplier_id':req.supplier_id,'organization_id':req.organization_id,'item_ids':req.item_ids}
        store.update(job_id,matches=json.dumps(matches),stage='Сопоставление подтверждено; готово к проверке счёта в 1С')
        return {'ok':True,**matches}

    @app.get('/jobs/{job_id}/confirmed',dependencies=secured)
    def confirmed(job_id:str,base:str):
        row=get_job(job_id,base)
        matches=json.loads(row['matches'] or '{}')
        if not matches.get('confirmed'): raise HTTPException(409,'Подтвердите сопоставление')
        refs=store.refs(base)
        if matches['supplier_id'] not in {r['id'] for r in refs['suppliers']} or matches['organization_id'] not in {r['id'] for r in refs['organizations']} or any(x not in {r['id'] for r in refs['items']} for x in matches['item_ids']):
            raise HTTPException(409,'Справочник изменился; требуется новое сопоставление')
        return {'invoice':json.loads(row['result']),'matches':matches}

    @app.get('/jobs/{job_id}/source',dependencies=secured)
    def source(job_id:str,base:str):
        row=get_job(job_id,base)
        return FileResponse(row['source'],filename=row['filename'])

    @app.post('/jobs/{job_id}/imported',dependencies=secured)
    def imported(job_id:str,req:Receipt):
        row=get_job(job_id,req.base)
        matches=json.loads(row['matches'] or '{}')
        if not matches.get('confirmed'):
            raise HTTPException(409,'Сопоставление не подтверждено')
        try: document_id=str(uuid.UUID(req.document_id))
        except ValueError: raise HTTPException(400,'Некорректный GUID документа')
        previous=matches.get('document_id')
        if previous and previous!=document_id:
            raise HTTPException(409,'Задание уже связано с другим документом')
        matches['document_id']=document_id
        store.update(job_id,status='imported',stage='Создан непроведённый счёт в 1С',matches=json.dumps(matches))
        return {'ok':True,'document_id':document_id}

    @app.post('/jobs/{job_id}/retry',dependencies=secured)
    def retry(job_id:str,base:str):
        get_job(job_id,base)
        if not store.retry(job_id,base): raise HTTPException(409,'Повтор доступен для ошибки, ожидания входа или завершённого задания, которое ещё не импортировано')
        store.worker_state('ready','Повтор обработки запрошен')
        return {'ok':True}

    return app

app=create_app()
