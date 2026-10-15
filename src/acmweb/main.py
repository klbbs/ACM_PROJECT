import argparse
import uvicorn
from .app import create_app


def main():
    parser=argparse.ArgumentParser(description="ACM Lab 网页服务")
    parser.add_argument("--host",default="127.0.0.1")
    parser.add_argument("--port",type=int,default=8000)
    args=parser.parse_args()
    from .settings import Settings
    settings=Settings()
    if args.host not in {"127.0.0.1","localhost","::1"}:
        settings.production=True
    uvicorn.run(create_app(settings),host=args.host,port=args.port,access_log=False)


if __name__=="__main__":main()
