"""Full-session M30 startup for existing jobs. Never invokes another job."""
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import os,sys,shutil,json

def is_active(now=None):
    return (now or datetime.now(ZoneInfo('Asia/Seoul'))).date().isoformat()>='2026-11-05'

def run(args,now=None):
    if not args:raise ValueError('ORIGINAL_ENTRYPOINT_REQUIRED')
    root=Path('/app/m30') if is_active(now) else Path('/app')
    if is_active(now):
        engine=os.environ.get('V8_ENGINE_PATH')
        if engine=='/app/v8_engine':os.environ['V8_ENGINE_PATH']='/app/m30/v8_engine'
        os.environ['STOCKBOT_TIMEFRAME_CONTRACT']='KRX_M30_V1'
        os.environ['STOCKBOT_BAR_INTERVAL_MINUTES']='30'
        # Downloaded presentation packages retain their original Drive IDs and
        # all non-timeframe modules. Patch only their parser and labels locally.
        if args[0]=='-c':
            code=args[1]
            for destination,name in [('/tmp/v8_html_app','v8html'),('/tmp/pre_spark','prespark')]:
                anchor="zipfile.ZipFile(io.BytesIO(r.content)).extractall('"+destination+"')"
                if anchor in code:
                    injection="\nimport shutil\n"
                    for file in ['parser.py','email_render.py','render.py']:
                        injection+="shutil.copyfile('/app/m30_delivery/"+name+"/v8pdf/"+file+"','"+destination+"/v8pdf/"+file+"')\n"
                    code=code.replace(anchor,anchor+injection)
            args=[args[0],code,*args[2:]]
    os.chdir(root)
    # Keep existing GCS state, mail receipts, environment, budgets and args.
    print(json.dumps({'timeframe_contract':os.environ.get('STOCKBOT_TIMEFRAME_CONTRACT','M45_ARCHIVE'),
        'application_root':str(root),'original_entrypoint':args[0]},ensure_ascii=False),flush=True)
    os.execv(sys.executable,[sys.executable,*args])

if __name__=='__main__':run(sys.argv[1:])
