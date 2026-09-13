"""Read-only TXT, simple DOCX and existing XLSX content packages."""
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET
from ..core import load_excel
from ..models import file_hash

class LocalDocuments:
    provider_id='local_documents_v1'
    def load(self,path):
        path=Path(path).resolve()
        if not path.is_file():raise ValueError('文档不存在：'+str(path))
        if path.stat().st_size>30*1024*1024:raise ValueError('文档过大，首版仅支持30MB以内文档')
        source={'path':str(path),'sha256':file_hash(path),'provider_id':self.provider_id}
        ext=path.suffix.lower()
        if ext=='.xlsx':
            records=load_excel(path)
        else:
            if ext=='.txt':text=path.read_text(encoding='utf-8-sig')
            elif ext=='.docx':text=self._docx(path)
            else:raise ValueError('请选择TXT、DOCX或XLSX文件')
            lines=text.strip().splitlines()
            if not lines:raise ValueError('文档为空')
            # First paragraph is only an editable suggestion; UI asks for review.
            title=lines[0].strip()
            voice='\n'.join(lines[1:]).strip() if len(lines)>1 else text.strip()
            records=[dict(external_id='doc-'+source['sha256'][:12],title=title,voice_text=voice,body='',tags='',enabled=True)]
        for index,record in enumerate(records):
            record['source']=dict(source,record=index+1)
        return records
    def _docx(self,path):
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        with zipfile.ZipFile(path) as z:
            info=z.getinfo('word/document.xml')
            if info.file_size>10*1024*1024:raise ValueError('DOCX正文过大')
            raw=z.read(info)
        if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:raise ValueError('不支持含XML实体的文档')
        root=ET.fromstring(raw)
        for tag in ('tbl','drawing','object','altChunk','txbxContent'):
            if root.find('.//w:'+tag,ns) is not None:
                raise ValueError('DOCX含表格/图片或复杂对象，请另存纯文字后导入，避免遗漏内容')
        paragraphs=[]
        for p in root.findall('.//w:body/w:p',ns):
            parts=[]
            for node in p.iter():
                local=node.tag.rsplit('}',1)[-1]
                if local=='t':parts.append(node.text or '')
                elif local in ('br','cr'):parts.append('\n')
                elif local=='tab':parts.append('\t')
            paragraphs.append(''.join(parts))
        return '\n'.join(paragraphs)
