import uvicorn
from .settings import Settings

if __name__=='__main__':
    settings=Settings()
    uvicorn.run('server.app:app',host=settings.host,port=settings.port,workers=1,access_log=False)
