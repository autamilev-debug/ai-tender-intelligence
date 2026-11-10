"""Reuse acquisition parser containment, without network or inherited credentials."""
import base64,json,sys
from ..acquisition.validate_worker import contain


def contain_extraction():
    contain()
    def deny(event,args):
        if event.startswith('socket.') or event in {'subprocess.Popen','os.system'}:
            raise RuntimeError('EXTRACTION_EXTERNAL_ACTION_DENIED')
    sys.addaudithook(deny)


def main():
    try:
        contain_extraction()
        from .models import Limits
        from .engine import extract
        request=json.loads(sys.stdin.buffer.read(30*1024*1024))
        result=extract(base64.b64decode(request['bytes'],validate=True),request['format'],Limits(**request['limits']))
    except BaseException:
        result={'status':'FAILED','blocks':[],'units':[],'flags':['EXTRACTION_PROCESS_FAILED']}
    sys.stdout.write(json.dumps(result,ensure_ascii=True,allow_nan=False))


if __name__=='__main__':main()
