"""Local parsers. Locations are source coordinates, never invented PDF page numbers."""
from io import BytesIO, StringIO
from datetime import date, datetime, time
from html.parser import HTMLParser
import math
import re
import struct
import zipfile
from .engine import usable, BoundReached
from ..acquisition.validation import xml
from ..discovery.ted import load_json

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def pdf(raw, b):
    import pdfplumber
    b.flag('PDF_READING_ORDER_APPROXIMATE')
    b.flag('PDF_UNRULED_TABLE_COVERAGE_NOT_ESTABLISHED')
    with pdfplumber.open(BytesIO(raw), unicode_norm=None) as doc:
        total = len(doc.pages)
        for number, page in enumerate(doc.pages[:b.limits.pages], 1):
            unit = {'page': number, 'status': 'PENDING', 'total_pages': total}
            b.units.append(unit)
            try:
                text = b.clean(page.extract_text(x_tolerance=3, y_tolerance=3) or '')
                unit['characters'] = len(text)
                unit['image_count'] = len(page.images)
                good = usable(text)
                unit['status'] = ('READABLE_TEXT' if good else 'IMAGE_ONLY' if not text.strip() and page.images
                                  else 'NO_TEXT' if not text.strip() else 'LOW_TEXT_QUALITY')
                if not good:
                    b.flag(unit['status'], gap=True)
                if text.strip():
                    for index, paragraph in enumerate(re.split(r'\n\s*\n', text), 1):
                        if paragraph.strip():
                            b.add('TEXT', {'page': number, 'text_block': index}, text=paragraph,
                                  quality='READABLE_TEXT' if good else 'UNCERTAIN')
                tables = page.find_tables(table_settings={'vertical_strategy': 'lines', 'horizontal_strategy': 'lines'})
                unit['tables_identified'] = len(tables)
                unit['tables_extracted'] = 0
                for index, table in enumerate(tables, 1):
                    rows = []
                    for ri, row in enumerate(table.extract(), 1):
                        if ri > b.limits.rows:
                            raise BoundReached()
                        cells = []
                        for ci, value in enumerate(row, 1):
                            b.cell()
                            cells.append({'column': ci, 'text': b.clean(value or '')})
                        rows.append({'row': ri, 'cells': cells})
                    if not any(c['text'].strip() for r in rows for c in r['cells']):
                        b.flag('IDENTIFIED_TABLE_UNREADABLE', gap=True)
                    else:
                        b.add('TABLE', {'page': number, 'table': index, 'bbox': list(table.bbox)},
                              rows=rows, header_status='NOT_INTERPRETED', method='RULED_GRID_CANDIDATE')
                        unit['tables_extracted'] += 1
            except BoundReached:
                unit['status'] = 'TRUNCATED'
                raise
            except Exception:
                unit['status'] = 'PAGE_PARSER_FAILED'
                b.flag('PAGE_PARSER_FAILED', gap=True)
            finally:
                page.close()
        if total > b.limits.pages:
            b.flag('PAGE_LIMIT_TRUNCATION', gap=True)


def word_text(node):
    values = []
    for n in node.iter():
        if n.tag == W+'t':
            values.append(n.text or '')
        elif n.tag == W+'tab':
            values.append('\t')
        elif n.tag in {W+'br', W+'cr'}:
            values.append('\n')
    return ''.join(values)


def docx(raw, b):
    with zipfile.ZipFile(BytesIO(raw)) as z:
        styles = {}
        if 'word/styles.xml' in z.namelist():
            for s in xml(z.read('word/styles.xml')):
                name = s.find(W+'name')
                if name is not None:
                    styles[s.get(W+'styleId')] = name.get(W+'val')
        parts = ['word/document.xml'] + sorted(n for n in z.namelist() if
            re.fullmatch(r'word/(?:header\d+|footer\d+|footnotes|endnotes)\.xml', n))
        b.flag('WORD_PAGINATION_NOT_AVAILABLE')
        if len(parts)>1:b.flag('WORD_NONBODY_PARTS_LISTED_SEPARATELY')
        for part in parts:
            root = xml(z.read(part))
            # These may hide or reorder meaningful evidence; never silently call complete.
            if any(n.tag in {W+'ins', W+'del', W+'sdt', W+'altChunk', W+'txbxContent', W+'drawing', W+'object'} for n in root.iter()):
                b.flag('WORD_COMPLEX_CONTENT_NOT_FULLY_COVERED', gap=True)
            body = root.find(W+'body') if part == 'word/document.xml' else root
            if body is None:
                b.flag('WORD_BODY_MISSING', gap=True)
                continue
            b.units.append({'part': part, 'status': 'EXTRACTED_STRUCTURAL_CONTENT'})

            def visit(parent, path):
                for index, node in enumerate(parent, 1):
                    loc = {**path, 'xml_child': index}
                    if node.tag == W+'p':
                        text = b.clean(word_text(node))
                        if text.strip():
                            style = node.find(W+'pPr/'+W+'pStyle')
                            outline = node.find(W+'pPr/'+W+'outlineLvl')
                            name = styles.get(style.get(W+'val')) if style is not None else None
                            heading = outline is not None or bool(name and re.match(r'(?i)(heading|заглавие)', name))
                            b.add('HEADING' if heading else 'TEXT', loc, text=text, style=name,
                                  outline_level=outline.get(W+'val') if outline is not None else None)
                    elif node.tag == W+'tbl':
                        rows = [];nested_tables=[]
                        for ri, row in enumerate(node.findall(W+'tr'), 1):
                            if ri > b.limits.rows or b.cells+len(row.findall(W+'tc'))>b.limits.cells:
                                if rows:b.add('TABLE',loc,rows=rows,header_status='EXPLICIT_MARKERS_ONLY',truncated=True)
                                raise BoundReached()
                            cells = []
                            for ci, cell in enumerate(row.findall(W+'tc'), 1):
                                b.cell()
                                span = cell.find(W+'tcPr/'+W+'gridSpan')
                                merge = cell.find(W+'tcPr/'+W+'vMerge')
                                text = b.clean('\n'.join(word_text(p) for p in cell.findall(W+'p')))
                                cells.append({'column': ci, 'text': text,
                                              'grid_span': span.get(W+'val') if span is not None else None,
                                              'vertical_merge': merge.get(W+'val', 'continue') if merge is not None else None})
                                for ni, nested in enumerate(cell.findall(W+'tbl'), 1):
                                    # Nesting is retained as a separate table with a full cell path.
                                    nested_tables.append((nested,{**loc, 'nested_in': [ri, ci, ni]}))
                            rows.append({'row': ri, 'header_marked': row.find(W+'trPr/'+W+'tblHeader') is not None, 'cells': cells})
                        if rows:
                            b.add('TABLE', loc, rows=rows, header_status='EXPLICIT_MARKERS_ONLY')
                        for nested,nested_loc in nested_tables:visit([nested],nested_loc)
                    elif node.tag in {W+'sdt', W+'sdtContent'}:
                        visit(node, {**loc, 'container_path': str(path)})
                    elif node.tag in {W+'footnote',W+'endnote'}:
                        visit(node,{**loc,'note_id':node.get(W+'id')})
                    elif any(n.tag==W+'t' for n in node.iter()):
                        b.flag('UNHANDLED_WORD_TEXT_CONTAINER',gap=True)
            visit(body, {'part': part})


def value(v):
    if isinstance(v, (datetime, date, time)):
        return {'type': type(v).__name__, 'value': v.isoformat()}
    if isinstance(v, float) and not math.isfinite(v):
        return {'type': 'nonfinite', 'value': str(v)}
    return {'type': type(v).__name__, 'value': v}


def clean_value(v,b):
    data=value(v)
    if isinstance(data['value'],str):data['value']=b.clean(data['value'])
    return data


def excel_column(n):
    result = ''
    while n:
        n, rem = divmod(n-1, 26)
        result = chr(65+rem)+result
    return result


def workbook_layout(raw):
    """Cross-check worksheet declarations against actual XML cell coordinates."""
    import posixpath
    s='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    relationship='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
    result={}
    with zipfile.ZipFile(BytesIO(raw)) as z:
        links={r.get('Id'):r for r in xml(z.read('xl/_rels/workbook.xml.rels'))}
        for sheet in xml(z.read('xl/workbook.xml')).find(s+'sheets'):
            rel=links.get(sheet.get(relationship))
            if rel is None or rel.get('TargetMode')=='External':continue
            target=rel.get('Target','')
            path=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
            if not path.startswith('xl/') or '..' in path.split('/'):continue
            node=xml(z.read(path));nr=nc=0;uncertain=False
            for cell in node.iter(s+'c'):
                address=cell.get('r','');m=re.fullmatch(r'([A-Z]{1,3})([1-9]\d{0,6})',address)
                if not m:uncertain=True;continue
                col=0
                for ch in m[1]:col=col*26+ord(ch)-64
                nr=max(nr,int(m[2]));nc=max(nc,col)
            result[sheet.get('name')]={'rows':nr,'columns':nc,'coordinate_gap':uncertain,
                'merged_ranges':[m.get('ref') for m in node.iter(s+'mergeCell')],'part':path,'state':sheet.get('state','visible')}
    return result


def xlsx(raw, b):
    import openpyxl
    books = []
    try:
        for data_only in (False, True):
            books.append(openpyxl.load_workbook(BytesIO(raw), read_only=True, data_only=data_only, keep_links=False))
        source, cache = books
        layouts=workbook_layout(raw)
        for si, ws in enumerate(source.worksheets):
            if si >= b.limits.sheets:
                b.flag('SHEET_LIMIT_TRUNCATION', gap=True)
                break
            total_rows, total_cols = ws.max_row or 0, ws.max_column or 0
            layout=layouts.get(ws.title)
            if layout is None:
                b.flag('WORKSHEET_LAYOUT_UNAVAILABLE',gap=True)
                continue
            if layout['coordinate_gap']:b.flag('WORKSHEET_CELL_LOCATORS_UNCERTAIN',gap=True)
            if layout['rows']>total_rows or layout['columns']>total_cols:
                b.flag('WORKSHEET_DIMENSION_MISMATCH',gap=True)
            total_rows=max(total_rows,layout['rows']);total_cols=max(total_cols,layout['columns'])
            ws.reset_dimensions();cache[ws.title].reset_dimensions()
            unit = {'sheet': ws.title, 'total_sheets': len(source.worksheets), 'declared_rows': total_rows,
                    'declared_columns': total_cols, 'status': 'EXTRACTED', 'formula_mode': 'FORMULA_AND_AVAILABLE_CACHE',
                    'source_part':layout['part'],'sheet_state':layout['state'],'merged_ranges':layout['merged_ranges']}
            b.units.append(unit)
            if not total_rows or not total_cols:
                unit['status'] = 'EMPTY_SHEET'
                continue
            if total_rows > b.limits.rows or total_cols > b.limits.columns:
                b.flag('SHEET_DIMENSION_TRUNCATION', gap=True)
                unit['status'] = 'TRUNCATED'
            nr, nc = min(total_rows,b.limits.rows), min(total_cols,b.limits.columns)
            rows = []
            for ri, (sr, cr) in enumerate(zip(ws.iter_rows(max_row=nr,max_col=nc),cache[ws.title].iter_rows(max_row=nr,max_col=nc)),1):
                if b.cells+nc>b.limits.cells:
                    if rows:b.add('TABLE',{'sheet':ws.title,'range':f'A1:{excel_column(nc)}{ri-1}'},rows=rows,header_status='NOT_INTERPRETED',truncated=True)
                    unit['status']='TRUNCATED'
                    raise BoundReached()
                cells = []
                for ci, (cell, cached) in enumerate(zip(sr,cr),1):
                    b.cell()
                    if cell.value is None:
                        continue
                    loc = {'sheet': ws.title, 'row': ri, 'column': ci, 'cell': f'{excel_column(ci)}{ri}'}
                    formula = cell.data_type == 'f'
                    actual = cell.value
                    if formula and not isinstance(actual,str):
                        b.flag('COMPLEX_FORMULA_TEXT_UNAVAILABLE',gap=True)
                        actual = None
                    if formula and cached.value is None:
                        b.flag('FORMULA_CACHED_RESULT_MISSING',gap=True)
                    if formula:b.flag('FORMULA_CACHE_FRESHNESS_NOT_ESTABLISHED')
                    data = clean_value(cached.value if formula else actual,b)
                    cells.append({'locator': loc, **data, 'formula_present': formula,
                                  'formula_text': b.clean(actual) if formula and actual else None,
                                  'cached_result': clean_value(cached.value,b) if formula else None})
                if cells:
                    rows.append({'row': ri, 'cells': cells})
            if rows:
                b.add('TABLE', {'sheet': ws.title, 'range': f'A1:{excel_column(nc)}{nr}'},
                      rows=rows, header_status='NOT_INTERPRETED', method='WORKSHEET_CELL_GRID')
        b.flag('WORKBOOK_DISPLAY_FORMATTING_NOT_RENDERED')
    finally:
        for book in books:
            book.close()


def xls_formula_cells(raw, b):
    """Locate BIFF5/8 FORMULA records; expression tokens are not evaluated/decoded."""
    import olefile
    with olefile.OleFileIO(BytesIO(raw)) as ole:
        stream = ole.openstream('Workbook' if ole.exists('Workbook') else 'Book').read()
    positions, formulas = {}, set()
    pos = 0
    index = 0
    records = []
    while pos + 4 <= len(stream):
        op, size = struct.unpack_from('<HH', stream, pos)
        if op == size == 0:
            break
        if pos+4+size > len(stream) or len(records) >= 100000:
            raise ValueError('BIFF_STRUCTURE_BOUND')
        data = stream[pos+4:pos+4+size]
        if op == 0x85 and size >= 6 and data[5] == 0:
            positions[struct.unpack_from('<I', data)[0]] = index
            index += 1
        records.append((pos,op,data))
        pos += 4+size
    sheet = None
    for pos, op, data in records:
        if pos in positions:
            sheet = positions[pos]
        if op == 0x06 and len(data) >= 20 and sheet is not None:
            row, col = struct.unpack_from('<HH',data)
            formulas.add((sheet,row,col))
    return formulas


def xls(raw, b):
    import xlrd
    book = xlrd.open_workbook(file_contents=raw, on_demand=True, formatting_info=True,
                              logfile=StringIO(), ignore_workbook_corruption=False)
    try:
        known_formulas = book.biff_version in {50,70,80}
        formula_cells = xls_formula_cells(raw,b) if known_formulas else set()
        if not known_formulas:
            b.flag('XLS_FORMULA_LOCATIONS_UNAVAILABLE',gap=True)
        for si in range(min(book.nsheets,b.limits.sheets)):
            sheet = book.sheet_by_index(si)
            b.units.append({'sheet':sheet.name,'total_sheets':book.nsheets,'declared_rows':sheet.nrows,
                            'declared_columns':sheet.ncols,'status':'EXTRACTED','biff_version':book.biff_version})
            nr,nc = min(sheet.nrows,b.limits.rows),min(sheet.ncols,b.limits.columns)
            if nr < sheet.nrows or nc < sheet.ncols:
                b.flag('SHEET_DIMENSION_TRUNCATION',gap=True)
            rows = []
            for ri in range(nr):
                if b.cells+nc>b.limits.cells:
                    if rows:b.add('TABLE',{'sheet':sheet.name,'range':f'A1:{excel_column(nc)}{ri}'},rows=rows,header_status='NOT_INTERPRETED',truncated=True)
                    b.units[-1]['status']='TRUNCATED'
                    raise BoundReached()
                cells = []
                for ci in range(nc):
                    b.cell()
                    cell = sheet.cell(ri,ci)
                    if cell.ctype in {xlrd.XL_CELL_EMPTY,xlrd.XL_CELL_BLANK}:
                        continue
                    formula = (si,ri,ci) in formula_cells
                    data = value(cell.value)
                    if isinstance(data['value'],str):data['value']=b.clean(data['value'])
                    if formula:
                        b.flag('XLS_BINARY_FORMULA_TEXT_NOT_DECODED',gap=True)
                    cells.append({'locator':{'sheet':sheet.name,'row':ri+1,'column':ci+1,'cell':f'{excel_column(ci+1)}{ri+1}'},
                                  **data,'excel_cell_type':cell.ctype,'formula_present':formula if known_formulas else None,
                                  'formula_text':None,'cached_result':data if formula else None,
                                  'date_mode':book.datemode if cell.ctype==xlrd.XL_CELL_DATE else None})
                if cells:rows.append({'row':ri+1,'cells':cells})
            if rows:b.add('TABLE',{'sheet':sheet.name,'range':f'A1:{excel_column(nc)}{nr}'},rows=rows,
                          merged_ranges=[list(r) for r in sheet.merged_cells],header_status='NOT_INTERPRETED',method='WORKSHEET_CELL_GRID')
            book.unload_sheet(si)
        if book.nsheets>b.limits.sheets:b.flag('SHEET_LIMIT_TRUNCATION',gap=True)
        b.flag('WORKBOOK_DISPLAY_FORMATTING_NOT_RENDERED')
    finally:
        book.release_resources()


def structured_xml(raw,b):
    root=xml(raw)
    def walk(node,path):
        if node.text and node.text.strip():b.add('TEXT',{'xml_path':path},text=b.clean(node.text))
        for name,text in sorted(node.attrib.items()):
            b.add('ATTRIBUTE',{'xml_path':path,'attribute':name},text=b.clean(text))
        counts={}
        for child in node:
            counts[child.tag]=counts.get(child.tag,0)+1
            cp=path+'/'+child.tag+'['+str(counts[child.tag])+']'
            walk(child,cp)
            if child.tail and child.tail.strip():b.add('TEXT',{'xml_path':cp,'text_position':'tail'},text=b.clean(child.tail))
    walk(root,'/'+root.tag+'[1]')
    b.units.append({'root':root.tag,'status':'STRUCTURAL_LEAVES_EXTRACTED'})


class OfficialHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.text=[];self.hidden=0;self.tables=False;self.active=False
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style'}:self.hidden+=1;self.active=True
        if tag=='table':self.tables=True
        if tag in {'p','div','li','tr','br','h1','h2','h3'}:self.text.append('\n')
    def handle_endtag(self,tag):
        if tag in {'script','style'}:self.hidden=max(0,self.hidden-1)
        if tag in {'p','div','li','tr','h1','h2','h3'}:self.text.append('\n')
    def handle_data(self,data):
        if not self.hidden:self.text.append(data)


def structured_json(raw,b):
    obj=load_json(raw)
    if isinstance(obj,dict) and {'Id','Title','Text'}<=obj.keys() and not str(obj.get('Text') or '').strip():
        b.flag('ANNOUNCEMENT_BODY_EMPTY',gap=True)
    def walk(value,path,depth=0):
        if depth>64:raise BoundReached()
        if isinstance(value,dict):
            for key,item in value.items():walk(item,path+'/'+str(key).replace('~','~0').replace('/','~1'),depth+1)
        elif isinstance(value,list):
            for index,item in enumerate(value):walk(item,path+'/'+str(index),depth+1)
        elif value is not None:
            text=b.clean(value)
            if isinstance(value,str) and re.search(r'<\s*\w+',value):
                html=OfficialHTML();html.feed(text);html.close();text=''.join(html.text)
                if html.tables:b.flag('HTML_TABLE_STRUCTURE_NOT_EXTRACTED',gap=True)
                if html.active:b.flag('HTML_ACTIVE_CONTENT_OMITTED',gap=True)
            if text.strip():b.add('TEXT',{'json_pointer':path},text=text)
    walk(obj,'')
    b.units.append({'structured_source':'JSON','status':'STRUCTURAL_LEAVES_EXTRACTED'})
