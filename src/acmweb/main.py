import argparse
import os
import uvicorn
from .app import create_app


def main():
    parser=argparse.ArgumentParser(description="ACM Lab 网页服务")
    parser.add_argument("--host",default="127.0.0.1")
    parser.add_argument("--port",type=int,default=8000)
    parser.add_argument('--multi-user',action='store_true',help='启用独立用户、邀请注册和共享资料库')
    args=parser.parse_args()
    from .settings import Settings
    settings=Settings()
    if args.host not in {"127.0.0.1","localhost","::1"}:
        settings.production=True
    if args.multi_user or (settings.production and os.getenv('ACM_MULTI_USER','true').lower()=='true'):
        settings.multi_user=True
    uvicorn.run(create_app(settings),host=args.host,port=args.port,access_log=False,
                limit_concurrency=100,backlog=128,timeout_keep_alive=10,timeout_graceful_shutdown=30,
                proxy_headers=True,forwarded_allow_ips=os.getenv('FORWARDED_ALLOW_IPS','127.0.0.1'),
                server_header=False)


if __name__=="__main__":main()
