import re
import time
from pathlib import Path

class LoginRequired(Exception):
    pass

class BrowserError(Exception):
    pass

class PDFGuruBrowser:
    def __init__(self, settings):
        self.settings=settings
        self.runtime=None
        self.context=None

    def start(self):
        if self.context:
            return
        from playwright.sync_api import sync_playwright
        self.runtime=sync_playwright().start()
        self.context=self.runtime.chromium.launch_persistent_context(
            user_data_dir=str(self.settings.data_dir / 'browser-profile'),
            channel=self.settings.browser_channel, headless=self.settings.browser_headless,
            accept_downloads=False, viewport={'width':1200,'height':850})
        self.context.set_default_timeout(20000)

    def authenticate(self, page):
        page.goto('https://pdfguru.com/app/ai-chat', wait_until='domcontentloaded')
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            if page.get_by_role('button',name='Add file',exact=True).is_visible():
                return
            password=page.locator('input[type="password"]')
            email=page.locator('input[type="email"]')
            if password.count() and email.count() and password.first.is_visible() and email.first.is_visible():
                if not self.settings.pdfguru_email or not self.settings.pdfguru_password.get_secret_value():
                    raise LoginRequired('Укажите QSP_PDFGURU_EMAIL и QSP_PDFGURU_PASSWORD в .env либо выполните ручной вход')
                email.first.fill(self.settings.pdfguru_email)
                password.first.fill(self.settings.pdfguru_password.get_secret_value())
                submit=page.get_by_role('button',name=re.compile(r'^(Log in|Sign in|Войти)$',re.I))
                if submit.count()!=1:
                    raise LoginRequired('Форма входа изменилась; требуется ручной вход')
                submit.click()
                break
            entry=page.get_by_role('link',name=re.compile(r'^(Log in|Sign in|Войти)$',re.I))
            if entry.count()==1 and entry.is_visible():
                entry.click()
            page.wait_for_timeout(500)
        try:
            page.get_by_role('button',name='Add file',exact=True).wait_for(state='visible',timeout=25000)
        except Exception:
            raise LoginRequired('PDF Guru не подтвердил вход. Проверьте пароль, CAPTCHA, код подтверждения или подписку') from None

    def recognize(self, source: Path, prompt: str, stage):
        self.start()
        page=self.context.new_page()
        try:
            stage('Проверка авторизации PDF Guru')
            self.authenticate(page)
            # Never reuse old conversation answers or previously attached files.
            if page.locator('code').count():
                raise BrowserError('На странице остался старый диалог. Обработка остановлена во избежание подмены результата')
            stage('Загрузка файла в PDF Guru')
            page.get_by_role('button',name='Add file',exact=True).click()
            with page.expect_file_chooser(timeout=10000) as chooser:
                page.get_by_role('button',name='From my device',exact=True).click()
            chooser.value.set_files(str(source))
            # Successful upload was observed through this status toast in the live account.
            page.get_by_text('Upload complete — your files are ready.',exact=True).wait_for(state='visible',timeout=60000)
            stage('Отправка промпта; ожидание ответа ИИ')
            page.get_by_role('textbox').fill(prompt)
            page.get_by_role('button',name='Send',exact=True).click()
            deadline=time.monotonic()+self.settings.response_timeout_seconds
            previous=None
            stable_since=None
            while time.monotonic()<deadline:
                codes=page.locator('code')
                stop=page.get_by_role('button',name='Stop',exact=True)
                if codes.count() and not stop.is_visible():
                    text=codes.last.inner_text()
                    if text and text==previous:
                        if stable_since and time.monotonic()-stable_since>=2:
                            stage('Ответ получен; проверка JSON')
                            return text
                    else:
                        previous=text; stable_since=time.monotonic()
                page.wait_for_timeout(500)
            raise BrowserError('Истекло время ожидания полного JSON. Повтор выполняется только по команде пользователя')
        finally:
            page.close()

    def close(self):
        if self.context:
            self.context.close(); self.context=None
        if self.runtime:
            self.runtime.stop(); self.runtime=None

