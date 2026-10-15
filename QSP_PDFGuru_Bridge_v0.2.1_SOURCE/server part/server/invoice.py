import json
import math
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, field_validator

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)

class Party(Strict):
    name: str | None
    bin: str | None
    address: str | None = None
    kbe: str | None = None
    iban: str | None = None
    bank_name: str | None = None
    bic: str | None = None

class Item(Strict):
    line_no: str | None
    name: str | None
    sku: str | None
    unit: str | None
    quantity: float | None
    price: float | None
    amount: float | None
    vat_rate: float | None
    vat_amount: float | None

class Invoice(Strict):
    document_type: str | None
    supplier: Party
    buyer: Party
    number: str | None
    date: str | None
    currency: str | None
    warehouse: str | None
    responsible_person: str | None
    total_quantity: float | None
    subtotal: float | None
    vat: float | None
    total: float | None
    amount_in_words: str | None
    vat_mode: Literal['without_vat','with_vat','unknown']
    observed_item_count: int | None
    items: list[Item]
    uncertainties: list[str]

def parse_invoice(text):
    # Do not reconstruct broken JSON, digits or missing fields.
    data=Invoice.model_validate_json(text).model_dump()
    for key in ['total_quantity','subtotal','vat','total']:
        if data[key] is not None and not math.isfinite(data[key]):
            raise ValueError('Non-finite number')
    errors=[]
    inferred=[]
    for key in ['number','date','currency']:
        if not data[key]: errors.append('Не распознано поле '+key)
    if data['date']:
        from datetime import date
        try: date.fromisoformat(data['date'])
        except ValueError: errors.append('Некорректная дата')
    for role in ['supplier','buyer']:
        if not data[role]['name']: errors.append('Не распознано наименование '+role)
        value=data[role]['bin']
        if value and (not value.isdigit() or len(value)!=12): errors.append('Некорректный ИИН/БИН '+role)
    if not data['items']: errors.append('Товарные строки отсутствуют')
    if data['observed_item_count']!=len(data['items']): errors.append('Количество строк не согласовано')
    total=Decimal('0'); quantity=Decimal('0'); amounts_complete=True; quantities_complete=True
    for index,item in enumerate(data['items'],1):
        if not item['name']: errors.append(f'Строка {index}: нет наименования')
        for key in ['quantity','price','amount','vat_rate','vat_amount']:
            if item[key] is not None and not math.isfinite(item[key]): raise ValueError('Non-finite number')
        if any(item[k] is None for k in ['quantity','price','amount']):
            errors.append(f'Строка {index}: отсутствуют количество, цена или сумма')
        else:
            if abs(Decimal(str(item['quantity']))*Decimal(str(item['price']))-Decimal(str(item['amount'])))>Decimal('0.02'):
                errors.append(f'Строка {index}: количество × цена не равно сумме')
        if item['amount'] is None: amounts_complete=False
        else: total+=Decimal(str(item['amount']))
        if item['quantity'] is None: quantities_complete=False
        else: quantity+=Decimal(str(item['quantity']))
    if data['total'] is None: errors.append('Нет печатной итоговой суммы')
    elif amounts_complete:
        printed=Decimal(str(data['total']))
        gross_match=abs(total-printed)<=Decimal('0.05')
        net_match=False
        if data['vat_mode']=='with_vat' and all(x['vat_amount'] is not None for x in data['items']):
            net_match=abs(total+sum((Decimal(str(x['vat_amount'])) for x in data['items']),Decimal('0'))-printed)<=Decimal('0.05')
        if not gross_match and not net_match: errors.append('Сумма строк и НДС не согласованы с итогом')
    if data['total_quantity'] is not None and quantities_complete and abs(quantity-Decimal(str(data['total_quantity'])))>Decimal('0.001'):
        errors.append('Количество строк отличается от печатного итога количества')
    if data['vat_mode']=='unknown': errors.append('Не определён режим НДС')
    if data['vat'] is not None and all(x['vat_amount'] is not None for x in data['items']):
        row_vat=sum((Decimal(str(x['vat_amount'])) for x in data['items']),Decimal('0'))
        if abs(row_vat-Decimal(str(data['vat'])))>Decimal('0.05'): errors.append('Сумма НДС строк отличается от печатного НДС')
    numbers=[x['line_no'] for x in data['items']]
    if numbers and all(x is not None and x.isdigit() for x in numbers) and [int(x) for x in numbers]!=list(range(1,len(numbers)+1)):
        errors.append('Нарушена последовательность номеров товарных строк')
    if data['vat_mode']=='without_vat' and (data['vat'] not in (None,0) or any(x['vat_amount'] not in (None,0) or x['vat_rate'] not in (None,0) for x in data['items'])):
        errors.append('Режим Без НДС противоречит суммам/ставкам НДС')
    # Some invoices print only the total VAT. When the printed figures prove one
    # integer rate and line amounts already include VAT, distribute that printed
    # VAT across the lines. The inference is kept visible for operator review.
    if (data['vat_mode']=='with_vat' and data['total'] is not None and data['vat'] is not None
            and data['vat'] > 0 and data['total'] > data['vat']
            and data['items'] and all(x['amount'] is not None for x in data['items'])
            and all(x['vat_rate'] is None and x['vat_amount'] is None for x in data['items'])):
        printed_total=Decimal(str(data['total']))
        printed_vat=Decimal(str(data['vat']))
        gross_total=sum((Decimal(str(x['amount'])) for x in data['items']),Decimal('0'))
        raw_rate=printed_vat/(printed_total-printed_vat)*Decimal('100')
        integer_rate=raw_rate.quantize(Decimal('1'))
        if abs(gross_total-printed_total)<=Decimal('0.05') and abs(raw_rate-integer_rate)<=Decimal('0.0001'):
            allocated=Decimal('0')
            for index,item in enumerate(data['items']):
                item['vat_rate']=float(integer_rate)
                if index == len(data['items'])-1:
                    line_vat=printed_vat-allocated
                else:
                    amount=Decimal(str(item['amount']))
                    line_vat=(amount*integer_rate/(Decimal('100')+integer_rate)).quantize(Decimal('0.01'))
                    allocated+=line_vat
                item['vat_amount']=float(line_vat)
            inferred.append(f'НДС по строкам рассчитан из печатного общего НДС по ставке {integer_rate}% и требует проверки')
    data['uncertainties'].extend(inferred)
    return data,errors
