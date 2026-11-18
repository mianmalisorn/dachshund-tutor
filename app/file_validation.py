"""Bounded format validation, not an antivirus or document sanitiser."""
from pathlib import Path
from zipfile import ZipFile, BadZipFile
import warnings
import xml.etree.ElementTree as ET
from PIL import Image
from pypdf import PdfReader
from fastapi import HTTPException

SUPPORTED = frozenset({'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.docx', '.xlsx', '.pptx', '.odt', '.txt', '.rtf'})
Image.MAX_IMAGE_PIXELS = 20_000_000


def validate_content(path: Path, suffix: str):
    try:
        if suffix in {'.png', '.jpg', '.jpeg', '.webp'}:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(path) as im:
                    expected={'.png':'PNG','.jpg':'JPEG','.jpeg':'JPEG','.webp':'WEBP'}[suffix]
                    if im.format != expected or im.width*im.height > Image.MAX_IMAGE_PIXELS:
                        raise ValueError('image format/size')
                    im.verify()
        elif suffix == '.pdf':
            with path.open('rb') as stream:
                if not stream.read(8).startswith(b'%PDF-'):
                    raise ValueError('PDF signature')
                stream.seek(0)
                reader=PdfReader(stream, strict=True)
                if reader.is_encrypted or not reader.trailer.get('/Root'):
                    raise ValueError('encrypted or invalid PDF')
        elif suffix in {'.docx','.xlsx','.pptx','.odt'}:
            with ZipFile(path) as z:
                infos=z.infolist()
                if len(infos)>2000 or sum(i.file_size for i in infos)>60*1024*1024:
                    raise ValueError('expanded archive too large')
                names=[i.filename for i in infos]
                if len(names)!=len(set(names)) or any(i.flag_bits & 1 for i in infos):
                    raise ValueError('ambiguous or encrypted archive')
                if any('vbaproject' in n.lower() or n.lower().endswith(('.exe','.dll','.js')) for n in names):
                    raise ValueError('active content')
                required={'.docx':'word/document.xml','.xlsx':'xl/workbook.xml',
                          '.pptx':'ppt/presentation.xml','.odt':'content.xml'}[suffix]
                if required not in names:
                    raise ValueError('wrong office format')
                if suffix=='.odt':
                    if z.read('mimetype')!=b'application/vnd.oasis.opendocument.text':
                        raise ValueError('ODT mimetype')
                elif '[Content_Types].xml' not in names or '_rels/.rels' not in names:
                    raise ValueError('Office package metadata')
                if z.testzip() is not None:
                    raise ValueError('corrupt archive')
                for name in names:
                    if name.endswith(('.xml','.rels')):
                        raw=z.read(name)
                        # Reject DTDs/entities, including UTF-16 variants, before XML parsing.
                        probe=raw.replace(b'\x00',b'').upper()
                        if b'<!DOCTYPE' in probe or b'<!ENTITY' in probe:
                            raise ValueError('XML entities')
                        ET.fromstring(raw)
        elif suffix=='.txt':
            raw=path.read_bytes()
            text=raw.decode('utf-8-sig')
            if any(ord(c)<32 and c not in '\r\n\t' for c in text):
                raise ValueError('binary text')
        elif suffix=='.rtf':
            raw=path.read_bytes()
            if not raw.startswith(b'{\\rtf') or b'\\object' in raw.lower() or b'\\objdata' in raw.lower():
                raise ValueError('RTF format or embedded object')
        else:
            raise ValueError('unsupported format')
    except Exception as exc:
        raise HTTPException(400, 'Содержимое файла не соответствует формату, повреждено или содержит запрещённые элементы. Экспортируйте документ заново; защищённые паролем файлы не поддерживаются.') from exc
