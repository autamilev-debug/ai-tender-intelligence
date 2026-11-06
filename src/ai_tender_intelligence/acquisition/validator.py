import base64,json,os,subprocess,sys
from dataclasses import asdict
from .models import Bounds,AcquisitionError

def validate(raw,name,bounds=Bounds()):
    if not raw or len(raw)>bounds.file_bytes:return {'kind':'UNKNOWN','valid':False,'error':'FILE_SIZE_BOUND','children':[]}
    env={k:os.environ[k] for k in ['SystemRoot','WINDIR'] if k in os.environ}
    env['PYTHONIOENCODING']='utf-8'
    request=json.dumps({'bytes':base64.b64encode(raw).decode(),'name':name,'bounds':asdict(bounds)}).encode()
    try:
        p=subprocess.run([sys.executable,'-I','-m','ai_tender_intelligence.acquisition.validate_worker'],input=request,
                         stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,timeout=12)
        if p.returncode or len(p.stdout)>256000:raise ValueError()
        return json.loads(p.stdout)
    except subprocess.TimeoutExpired:return {'kind':'UNKNOWN','valid':False,'error':'VALIDATION_TIMEOUT','children':[]}
    except (ValueError,OSError):return {'kind':'UNKNOWN','valid':False,'error':'VALIDATOR_FAILED','children':[]}
