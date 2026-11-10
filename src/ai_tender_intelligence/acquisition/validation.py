"""Structural inspection only. No text extraction, Office execution or external references."""
from dataclasses import asdict
from io import BytesIO
import json,re,stat,unicodedata,zipfile
from pathlib import PurePosixPath
import xml.etree.ElementTree as ET
from .models import Bounds,AcquisitionError


def safe_member(name):
    if isinstance(name,str):name=name.replace('\\','/')
    if (not isinstance(name,str) or not name or len(name)>512 or ':' in name or name.startswith('/')
            or any(ord(c)<32 or ord(c)==127 for c in name) or any(p in {'','..','.'} for p in name.rstrip('/').split('/'))):
        raise AcquisitionError('UNSAFE_ARCHIVE_PATH')
    if any(p.rstrip(' .')!=p or p.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))} for p in name.rstrip('/').split('/')):
        raise AcquisitionError('UNSAFE_ARCHIVE_PATH')
    return unicodedata.normalize('NFC',name.rstrip('/')).casefold()


def xml(raw):
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper() or b'\x00' in raw:raise AcquisitionError('UNSAFE_XML')
    try:root=ET.fromstring(raw)
    except (ValueError,ET.ParseError,RecursionError):raise AcquisitionError('INVALID_XML') from None
    pending=[(root,0)];count=0
    while pending:
        node,depth=pending.pop();count+=1
        if count>100000 or depth>64:raise AcquisitionError('XML_STRUCTURE_BOUND')
        pending.extend((c,depth+1) for c in node)
    if root.tag.split('}')[-1].lower() in {'html','script','error','errorresponse'}:raise AcquisitionError('MISLEADING_HTML_OR_ERROR')
    return root


def inspect(raw,name,bounds=Bounds(),depth=0,budget=None):
    if not raw or len(raw)>bounds.file_bytes:raise AcquisitionError('FILE_SIZE_BOUND')
    budget=budget if budget is not None else {'expanded':0,'entries':0,'files':0}
    warnings=[];children=[]
    if raw.startswith(b'%PDF-'):
        from pypdf import PdfReader
        import logging
        logging.getLogger('pypdf').setLevel(logging.CRITICAL)
        if any(t in raw for t in [b'/JavaScript',b'/Launch',b'/EmbeddedFiles',b'/OpenAction']):raise AcquisitionError('PDF_ACTIVE_CONTENT_UNSUPPORTED')
        try:
            reader=PdfReader(BytesIO(raw),strict=True)
            if reader.is_encrypted:raise AcquisitionError('ENCRYPTED_FILE_UNSUPPORTED')
            if not len(reader.pages):raise AcquisitionError('INVALID_PDF')
        except AcquisitionError:raise
        except Exception:raise AcquisitionError('INVALID_PDF') from None
        kind='PDF'
    elif raw.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        import olefile
        try:
            with olefile.OleFileIO(BytesIO(raw),raise_defects=olefile.DEFECT_INCORRECT) as ole:
                names=ole.listdir()
                if len(names)>bounds.entries:raise AcquisitionError('OLE_STRUCTURE_BOUND')
                if any(any(p.upper() in {'VBA','MACROS','_VBA_PROJECT'} for p in path) for path in names):raise AcquisitionError('OFFICE_MACROS_UNSUPPORTED')
                total=sum(ole.get_size(p) for p in names)
                if total>bounds.expanded_bytes:raise AcquisitionError('OLE_STRUCTURE_BOUND')
                for p in names:
                    if len(ole.openstream(p).read())!=ole.get_size(p):raise AcquisitionError('CORRUPT_OLE')
                if ole.exists('WordDocument') and (ole.exists('0Table') or ole.exists('1Table')):
                    if ole.openstream('WordDocument').read(2)!=b'\xec\xa5':raise AcquisitionError('INVALID_DOC')
                    kind='DOC'
                elif ole.exists('Workbook') or ole.exists('Book'):
                    if ole.openstream('Workbook' if ole.exists('Workbook') else 'Book').read(2) not in {b'\x09\x08',b'\x09\x04',b'\x09\x02'}:raise AcquisitionError('INVALID_XLS')
                    kind='XLS'
                else:raise AcquisitionError('UNSUPPORTED_OLE')
        except AcquisitionError:raise
        except Exception:raise AcquisitionError('CORRUPT_OLE') from None
    elif raw.startswith(b'PK'):
        if depth>bounds.depth:raise AcquisitionError('ARCHIVE_DEPTH_BOUND')
        try:
            with zipfile.ZipFile(BytesIO(raw),metadata_encoding='cp866') as z:
                infos=z.infolist();budget['entries']+=len(infos)
                if budget['entries']>bounds.entries:raise AcquisitionError('ARCHIVE_ENTRY_BOUND')
                seen=set();paths={};data={};original_names={}
                for i in infos:
                    # ZipInfo normalizes backslashes/truncates NUL on some OSes.
                    # Inspect the original central-directory spelling as well.
                    key=safe_member(i.orig_filename)
                    if i.orig_filename.replace('\\','/')!=i.filename.replace('\\','/'):raise AcquisitionError('UNSAFE_ARCHIVE_PATH')
                    if key in seen:raise AcquisitionError('ARCHIVE_NAME_COLLISION')
                    if any('/'.join(key.split('/')[:j]) in paths and not paths['/'.join(key.split('/')[:j])] for j in range(1,len(key.split('/')))) or (not i.is_dir() and any(k.startswith(key+'/') for k in paths)):
                        raise AcquisitionError('ARCHIVE_NAME_COLLISION')
                    seen.add(key);paths[key]=i.is_dir()
                    if (stat.S_ISLNK(i.external_attr>>16) or (stat.S_IFMT(i.external_attr>>16) not in {0,stat.S_IFREG,stat.S_IFDIR})
                            or i.flag_bits&1 or i.compress_type not in {zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED}):raise AcquisitionError('UNSUPPORTED_ARCHIVE_ENTRY')
                    budget['expanded']+=i.file_size
                    if i.file_size>bounds.file_bytes or budget['expanded']>bounds.expanded_bytes or i.file_size>max(1,i.compress_size)*bounds.ratio:raise AcquisitionError('ARCHIVE_EXPANSION_BOUND')
                    if i.is_dir():continue
                    with z.open(i) as member:
                        value=member.read(bounds.file_bytes+1)
                        if len(value)!=i.file_size:raise AcquisitionError('ARCHIVE_SIZE_MISMATCH')
                    canonical=i.orig_filename.replace('\\','/')
                    data[canonical]=value;original_names[canonical]=i.orig_filename
                if '[Content_Types].xml' in data:
                    types=xml(data['[Content_Types].xml'])
                    if types.tag!='{http://schemas.openxmlformats.org/package/2006/content-types}Types':raise AcquisitionError('INVALID_OOXML')
                    if any('vbaproject' in n.casefold() for n in data):raise AcquisitionError('OFFICE_MACROS_UNSUPPORTED')
                    for n,v in data.items():
                        if n.endswith(('.xml','.rels')):xml(v)
                    if 'word/document.xml' in data:kind='DOCX';part='word/document.xml';local='document';ns='wordprocessingml';media='wordprocessingml.document'
                    elif 'xl/workbook.xml' in data:kind='XLSX';part='xl/workbook.xml';local='workbook';ns='spreadsheetml';media='spreadsheetml.sheet'
                    else:raise AcquisitionError('UNSUPPORTED_OOXML')
                    root=xml(data[part])
                    if root.tag not in {f'{{http://schemas.openxmlformats.org/{ns}/2006/main}}{local}',f'{{http://purl.oclc.org/ooxml/{ns}/main}}{local}'}:raise AcquisitionError('INVALID_OOXML')
                    if not any(t.attrib.get('PartName')=='/'+part and t.attrib.get('ContentType')=='application/vnd.openxmlformats-officedocument.'+media+'.main+xml' for t in types):raise AcquisitionError('INVALID_OOXML')
                    if '_rels/.rels' not in data:raise AcquisitionError('INVALID_OOXML')
                    rels=xml(data['_rels/.rels'])
                    if not any(t.attrib.get('Type','').endswith('/officeDocument') and t.attrib.get('Target','').lstrip('/')==part and t.attrib.get('TargetMode','Internal')=='Internal' for t in rels):raise AcquisitionError('INVALID_OOXML')
                else:
                    kind='ZIP'
                    for n,v in data.items():
                        budget['files']+=1
                        if budget['files']>bounds.files:raise AcquisitionError('ARCHIVE_FILE_BOUND')
                        try:result=inspect(v,n,bounds,depth+1,budget)
                        except AcquisitionError as e:result={'kind':'UNKNOWN','valid':False,'error':str(e),'children':[]}
                        children.append({'name':n,'archive_name':original_names[n],**result})
        except AcquisitionError:raise
        except Exception:raise AcquisitionError('CORRUPT_ARCHIVE') from None
    elif raw.lstrip().startswith(b'<'):
        xml(raw);kind='XML'
    elif raw.lstrip().startswith((b'{',b'[')):
        from ..discovery.ted import load_json
        try:load_json(raw)
        except ValueError:raise AcquisitionError('INVALID_JSON') from None
        if not name.casefold().endswith('.json'):raise AcquisitionError('MISLEADING_JSON_RESPONSE')
        kind='JSON'
    else:raise AcquisitionError('UNSUPPORTED_OR_MISLEADING_FILE')
    if name.lower().endswith('.pdf') and kind!='PDF':warnings.append('FILENAME_MEDIA_MISMATCH')
    return {'kind':kind,'valid':True,'warnings':warnings,'children':children}
