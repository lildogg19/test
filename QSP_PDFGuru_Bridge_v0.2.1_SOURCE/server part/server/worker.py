import json
import os
import threading
from pathlib import Path
from .browser import PDFGuruBrowser, LoginRequired, BrowserError
from .invoice import parse_invoice
from .matching import match_invoice

class Worker:
    def __init__(self, settings, store, browser_factory=PDFGuruBrowser):
        self.settings=settings; self.store=store; self.browser_factory=browser_factory
        self.stop=threading.Event(); self.thread=None; self.lock_file=None

    def acquire_lock(self):
        handle=open(self.store.root / 'worker.lock','a+b')
        try:
            if os.name=='nt':
                import msvcrt
                handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            handle.close(); return False
        self.lock_file=handle
        return True

    def run_one(self, browser, job):
        try:
            prompt=(Path(__file__).parents[1] / 'prompt.txt').read_text(encoding='utf-8-sig')
            raw=browser.recognize(Path(job['source']),prompt,
                lambda stage:self.store.update(job['id'],stage=stage))
            invoice,errors=parse_invoice(raw)
            matches=match_invoice(self.store,job['base'],invoice)
            invoice['validation_errors']=errors
            # Every document still requires operator review, even when totals match.
            self.store.update(job['id'],status='completed',stage='Распознано; требуется проверка и сопоставление в 1С',
                result=json.dumps(invoice,ensure_ascii=False),matches=json.dumps(matches,ensure_ascii=False),error=None)
            self.store.worker_state('ready','Готов к следующему документу')
        except LoginRequired as exc:
            self.store.update(job['id'],status='awaiting_login',stage='Требуется авторизация',error='login_required')
            self.store.worker_state('awaiting_login',str(exc))
        except Exception as exc:
            # Do not publish exception bodies, HTML, credentials or document contents.
            message=str(exc) if isinstance(exc,BrowserError) else 'Ошибка браузера или проверки ответа; требуется повтор/ручная проверка'
            self.store.update(job['id'],status='failed',stage=message,error=type(exc).__name__)
            self.store.worker_state('ready','Предыдущее задание завершилось ошибкой')

    def run(self):
        if not self.acquire_lock():
            return
        browser=self.browser_factory(self.settings)
        self.store.recover()
        self.store.worker_state('ready','Обработчик запущен')
        try:
            while not self.stop.is_set():
                queue=self.store.queue('__worker__')
                if queue['worker']['status']=='awaiting_login':
                    self.stop.wait(2); continue
                job=self.store.claim()
                if job:
                    self.store.worker_state('busy','Обрабатывается один документ; остальные в очереди')
                    self.run_one(browser,job)
                else:
                    self.stop.wait(1)
        finally:
            browser.close()
            self.store.worker_state('offline','Обработчик остановлен')
            self.lock_file.close()

    def start(self):
        self.thread=threading.Thread(target=self.run,name='pdfguru-worker',daemon=True)
        self.thread.start()

