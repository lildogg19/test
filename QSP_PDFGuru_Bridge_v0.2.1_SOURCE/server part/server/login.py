from .settings import Settings
from .store import Store
from .worker import Worker
from .browser import PDFGuruBrowser

def main():
    settings=Settings(browser_headless=False)
    store=Store(settings.data_dir)
    guard=Worker(settings,store)
    if not guard.acquire_lock():
        raise SystemExit('Stop the running bridge before manual login')
    browser=PDFGuruBrowser(settings)
    try:
        browser.start()
        page=browser.context.new_page()
        page.goto('https://pdfguru.com/app/ai-chat')
        print('Complete login in the browser. Do not send passwords to the chat.')
        input('When Add file is visible, press Enter here: ')
        if not page.get_by_role('button',name='Add file',exact=True).is_visible():
            raise SystemExit('Login was not verified')
        print('Authorized browser profile saved locally. Restart the bridge.')
    finally:
        browser.close(); guard.lock_file.close()

if __name__=='__main__': main()
