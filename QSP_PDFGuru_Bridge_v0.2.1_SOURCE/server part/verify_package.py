from pathlib import Path
import ast
import json
import re
import xml.etree.ElementTree as E

root=Path(__file__).parent
for file in (root/'server').glob('*.py'):
    ast.parse(file.read_text(encoding='utf-8'))
files=list((root/'onec/extension').rglob('*.xml'))
for file in files:E.parse(file)
form=E.parse(root/'onec/extension/DataProcessors/QSPPDF_Счета/Forms/Форма/Ext/Form.xml')
ns={'f':'http://v8.1c.ru/8.3/xcf/logform'}
module=(root/'onec/FormModule.bsl').read_text(encoding='utf-8-sig')
methods=set(re.findall(r'(?:Процедура|Функция)\s+(\w+)\s*\(',module))
for node in form.findall('.//f:Event',ns)+form.findall('.//f:Action',ns):
    assert node.text in methods,node.text
ids=[n.attrib['id'] for n in form.iter() if 'id' in n.attrib]
assert len(ids)==len(set(ids)),'Repeated form IDs'
attrs={n.attrib['name'] for n in form.findall('./f:Attributes/f:Attribute',ns)}
for n in form.findall('.//f:DataPath',ns):assert n.text.split('.')[0] in attrs,n.text
for name in ['BridgeModule.bsl','FormModule.bsl']:
    text=(root/'onec'/name).read_text(encoding='utf-8-sig')
    assert len(re.findall(r'^\s*Процедура\b',text,re.M))==len(re.findall(r'^\s*КонецПроцедуры\b',text,re.M)),name
    assert len(re.findall(r'^\s*Функция\b',text,re.M))==len(re.findall(r'^\s*КонецФункции\b',text,re.M)),name
assert 'РежимЗаписиДокумента.Проведение' not in (root/'onec/BridgeModule.bsl').read_text(encoding='utf-8')
assert 'pdfguru_password' not in (root/'server/app.py').read_text(encoding='utf-8')
print(f'Python syntax, {len(files)} XML files, form actions/data paths/IDs and BSL procedure boundaries OK.')
print('This is a structural check; 1C compilation and standalone browser login remain unverified.')
