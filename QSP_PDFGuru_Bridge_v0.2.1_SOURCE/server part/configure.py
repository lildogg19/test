"""Create a new local .env with a random token; never overwrite credentials."""
from pathlib import Path
import secrets

root=Path(__file__).parent
env=root/'.env'
if not env.exists():
    text=(root/'.env.example').read_text(encoding='utf-8').replace('CHANGE_TO_A_RANDOM_LONG_SECRET',secrets.token_urlsafe(36))
    env.write_text(text,encoding='utf-8')
    print('Created .env with a random API token. Set PDF Guru email/password locally.')
else:
    print('Existing .env preserved.')
