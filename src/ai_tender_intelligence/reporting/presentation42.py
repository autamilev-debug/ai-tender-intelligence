"""Shared HTML-to-plain-text email conversion."""
from html.parser import HTMLParser

class _PlainEmail(HTMLParser):
    """Same visible content and official URLs, without CSS or internal evidence."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[];self.hidden=0;self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag in {'head','script','style'}:self.hidden+=1
        if self.hidden:return
        if tag in {'p','h1','h2','h3','tr','ul','table'}:self.parts.append('\n')
        if tag=='li':self.parts.append('\n• ')
        if tag=='br':self.parts.append('\n')
        if tag=='a':self.links.append(dict(attrs).get('href',''))
    def handle_endtag(self,tag):
        if tag in {'head','script','style'}:
            self.hidden-=1;return
        if self.hidden:return
        if tag=='a' and self.links:
            url=self.links.pop()
            if url.startswith('https://'):self.parts.append(' ('+url+')')
        if tag in {'td','th'}:self.parts.append(' ')
        if tag in {'p','h1','h2','h3','li','tr','ul','table'}:self.parts.append('\n')
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)
