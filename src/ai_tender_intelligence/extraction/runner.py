"""Bounded subprocess I/O with a temporary output file rather than unbounded PIPE buffering."""
import base64,json,os,subprocess,sys,tempfile
from dataclasses import asdict
from .models import Limits,canonical
from ..acquisition.models import Bounds


def run(raw,kind,limits=Limits()):
    failed=lambda code:{'status':'FAILED','blocks':[],'units':[],'flags':[code]}
    if not raw or len(raw)>Bounds().file_bytes:return failed('EXTRACTION_INPUT_BOUND')
    env={k:os.environ[k] for k in ['SystemRoot','WINDIR'] if k in os.environ}
    env['PYTHONIOENCODING']='utf-8'
    request=canonical({'bytes':base64.b64encode(raw).decode(),'format':kind,'limits':asdict(limits)})
    try:
        with tempfile.TemporaryFile() as output:
            p=subprocess.Popen([sys.executable,'-I','-m','ai_tender_intelligence.extraction.worker'],
                stdin=subprocess.PIPE,stdout=output,stderr=subprocess.DEVNULL,env=env)
            try:p.communicate(request,timeout=12)
            except subprocess.TimeoutExpired:
                p.kill();p.communicate();return failed('EXTRACTION_TIMEOUT')
            if p.returncode:return failed('EXTRACTION_PROCESS_FAILED')
            output.seek(0)
            # ensure_ascii transmission can be up to six bytes per Unicode character.
            raw_result=output.read(limits.output_bytes*6+1)
            if len(raw_result)>limits.output_bytes*6:return failed('EXTRACTION_OUTPUT_BOUND')
            result=json.loads(raw_result)
            if len(canonical(result))>limits.output_bytes:return failed('EXTRACTION_OUTPUT_BOUND')
            return result
    except (ValueError,OSError):return failed('EXTRACTION_PROCESS_FAILED')
