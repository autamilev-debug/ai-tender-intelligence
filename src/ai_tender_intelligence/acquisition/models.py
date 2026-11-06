from dataclasses import dataclass,field
from datetime import datetime,timezone
import re
from urllib.parse import urlsplit

class AcquisitionError(ValueError):
    """A fixed safe code only, never a raw provider message or signed URL."""

@dataclass(frozen=True)
class Bounds:
    # Reviewable engineering choices, not provider capacities or approved policy.
    file_bytes:int=20*1024*1024
    total_bytes:int=64*1024*1024
    expanded_bytes:int=64*1024*1024
    entries:int=200
    files:int=100
    depth:int=2
    ratio:int=200
    requests:int=80
    metadata_rows:int=100
    seconds:int=180
    def __post_init__(self):
        ceilings={'file_bytes':20*1024*1024,'total_bytes':64*1024*1024,'expanded_bytes':64*1024*1024,
                  'entries':200,'files':100,'depth':2,'ratio':200,'requests':80,'metadata_rows':100,'seconds':180}
        if any(type(getattr(self,k)) is not int or not 1<=getattr(self,k)<=v for k,v in ceilings.items()):
            raise AcquisitionError('INVALID_BOUNDS')

@dataclass(frozen=True)
class Reference:
    kind:str
    tender:int
    identity:str
    filename:str
    document_id:int|None=None
    export_id:int|None=None
    announcement_id:int|None=None
    lot:int|None=None
    applicability:str='UNRESOLVED'
    metadata:dict=field(default_factory=dict)
    inline:bytes|None=field(default=None,repr=False)


def now():return datetime.now(timezone.utc).isoformat()

def integer(value):
    if type(value) is not int or value<=0:raise AcquisitionError('SOURCE_IDENTITY_INVALID')
    return value

def safe_text(value,maximum=20000):
    if not isinstance(value,str) or len(value)>maximum:raise AcquisitionError('SOURCE_TEXT_BOUND')
    # Source prose is data. Signed URL queries never become durable citations.
    return re.sub(r'https?://[^\s<>"\']+',lambda m: m[0].split('?',1)[0].split('#',1)[0],value)

def metadata(row):
    keys=('Id','Guid','CreatedDate','ModifiedDate','Version','PreviousVersionId','IsPreviousVersion','MD5Hash','Size','MimeType')
    value={k:row[k] for k in keys if k in row}
    import json
    if len(json.dumps(value))>8000:raise AcquisitionError('METADATA_BOUND')
    return value
