import re
from difflib import SequenceMatcher

def normalize(value):
    return ' '.join(re.sub(r'[^\w]+',' ',(value or '').casefold().replace('ё','е')).split())

def source_key(item):
    return '|'.join(normalize(item.get(k)) for k in ('name','sku','unit'))

def unique_result(candidates, method):
    return {'status':'matched' if len(candidates)==1 else 'ambiguous' if candidates else 'unmatched',
            'id':candidates[0]['id'] if len(candidates)==1 else None,
            'method':method,'candidates':[{k:r.get(k) for k in ('id','name','sku','unit','service')} for r in candidates[:10]]}

def party_match(party, refs):
    value = party.get('bin') or ''
    if re.fullmatch(r'\d{12}',value):
        candidates = [r for r in refs if r.get('bin')==value]
        # A name must never override an explicitly read but unmatched BIN.
        return unique_result(candidates,'bin')
    candidates = [r for r in refs if normalize(r['name'])==normalize(party.get('name')) and normalize(r['name'])]
    result = unique_result(candidates,'name_requires_confirmation')
    if result['status']=='matched':
        result['status']='suggested'; result['id']=None
    return result

def item_match(item, refs, confirmed):
    key = source_key(item)
    if key in confirmed:
        found=[r for r in refs if r['id']==confirmed[key]]
        if found:
            return unique_result(found,'confirmed_supplier_mapping')
    sku=normalize(item.get('sku'))
    if sku:
        found=[r for r in refs if normalize(r.get('sku'))==sku]
        if found:
            return unique_result(found,'sku')
    name=normalize(item.get('name')); unit=normalize(item.get('unit'))
    found=[r for r in refs if name and normalize(r['name'])==name and unit and normalize(r.get('unit'))==unit]
    if found:
        return unique_result(found,'exact_name_and_unit')
    suggestions=sorted(refs,key=lambda r:SequenceMatcher(None,name,normalize(r['name'])).ratio(),reverse=True)
    suggestions=[r for r in suggestions if name and SequenceMatcher(None,name,normalize(r['name'])).ratio()>=0.55][:5]
    result=unique_result(suggestions,'suggestion_requires_confirmation')
    result['status']='suggested' if suggestions else 'unmatched'; result['id']=None
    return result

def match_invoice(store, base, invoice):
    refs=store.refs(base)
    supplier=party_match(invoice['supplier'],refs['suppliers'])
    organization=party_match(invoice['buyer'],refs['organizations'])
    confirmed=store.mappings(base,supplier['id']) if supplier['id'] else {}
    items=[{'line':i,'source_key':source_key(row),**item_match(row,refs['items'],confirmed)} for i,row in enumerate(invoice['items'])]
    return {'supplier':supplier,'organization':organization,'items':items,
            'all_matched':supplier['status']=='matched' and organization['status']=='matched' and all(x['status']=='matched' for x in items)}
