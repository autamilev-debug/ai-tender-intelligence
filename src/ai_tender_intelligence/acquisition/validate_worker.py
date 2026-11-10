"""Credential-free, time/memory-bounded validation process. No converter or network tools."""
import base64,json,os,sys

def contain():
    if os.name=='nt':
        import ctypes as c
        from ctypes import wintypes as w
        class IO(c.Structure):_fields_=[(n,c.c_ulonglong) for n in ['ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount']]
        class Basic(c.Structure):_fields_=[('PerProcessUserTimeLimit',c.c_longlong),('PerJobUserTimeLimit',c.c_longlong),('LimitFlags',w.DWORD),('MinimumWorkingSetSize',c.c_size_t),('MaximumWorkingSetSize',c.c_size_t),('ActiveProcessLimit',w.DWORD),('Affinity',c.c_size_t),('PriorityClass',w.DWORD),('SchedulingClass',w.DWORD)]
        class Extended(c.Structure):_fields_=[('BasicLimitInformation',Basic),('IoInfo',IO),('ProcessMemoryLimit',c.c_size_t),('JobMemoryLimit',c.c_size_t),('PeakProcessMemoryUsed',c.c_size_t),('PeakJobMemoryUsed',c.c_size_t)]
        kernel=c.WinDLL('kernel32',use_last_error=True)
        kernel.CreateJobObjectW.restype=w.HANDLE;kernel.CreateJobObjectW.argtypes=[c.c_void_p,w.LPCWSTR]
        kernel.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD]
        kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];kernel.GetCurrentProcess.restype=w.HANDLE
        global _job
        _job=kernel.CreateJobObjectW(None,None);limits=Extended();limits.BasicLimitInformation.LimitFlags=0x100|0x8
        limits.BasicLimitInformation.ActiveProcessLimit=1;limits.ProcessMemoryLimit=512*1024*1024
        if not _job or not kernel.SetInformationJobObject(_job,9,c.byref(limits),c.sizeof(limits)) or not kernel.AssignProcessToJobObject(_job,kernel.GetCurrentProcess()):
            raise RuntimeError('VALIDATOR_ISOLATION_UNAVAILABLE')
    else:
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU,(8,8))

def main():
    try:
        contain()
        def deny_external(event,args):
            if event.startswith("socket.") or event in {"subprocess.Popen","os.system"}:raise RuntimeError("VALIDATOR_EXTERNAL_ACTION_DENIED")
        sys.addaudithook(deny_external)
        from .models import Bounds,AcquisitionError
        from .validation import inspect
        request=json.loads(sys.stdin.buffer.read(30*1024*1024))
        raw=base64.b64decode(request['bytes'],validate=True)
        result=inspect(raw,request['name'],Bounds(**request['bounds']))
    except BaseException as e:
        result={'kind':'UNKNOWN','valid':False,'error':str(e) if e.__class__.__name__=='AcquisitionError' else 'VALIDATOR_FAILED','children':[]}
    sys.stdout.write(json.dumps(result,ensure_ascii=True))
if __name__=='__main__':main()
