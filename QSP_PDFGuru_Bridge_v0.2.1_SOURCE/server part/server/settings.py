from pathlib import Path
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', env_prefix='QSP_', extra='ignore')
    api_token: SecretStr = SecretStr('')
    pdfguru_email: str = ''
    pdfguru_password: SecretStr = SecretStr('')
    data_dir: Path = ROOT / 'data'
    browser_channel: str = 'chromium'
    browser_headless: bool = True
    worker_enabled: bool = True
    response_timeout_seconds: int = 240
    max_upload_mb: int = 20
    max_pdf_pages: int = 15
    host: str = '127.0.0.1'
    port: int = 8020

