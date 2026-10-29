"""Explicit musical-alarm phrases; ordinary Alice alarms are not intercepted."""
import re


UNITS=dict(zip(('ноль','один','два','три','четыре','пять','шесть','семь','восемь','девять',
                'десять','одиннадцать','двенадцать','тринадцать','четырнадцать','пятнадцать',
                'шестнадцать','семнадцать','восемнадцать','девятнадцать'),range(20)))
UNITS.update({'одна':1,'одну':1,'две':2})
TENS={'двадцать':20,'тридцать':30,'сорок':40,'пятьдесят':50}


def number(words):
    tokens=words.split()
    if len(tokens)==1:
        if tokens[0].isdigit():return int(tokens[0])
        return UNITS.get(tokens[0],TENS.get(tokens[0]))
    if len(tokens)==2 and tokens[0] in TENS and 0<UNITS.get(tokens[1],0)<10:
        return TENS[tokens[0]]+UNITS[tokens[1]]
    return None


def parse(phrase):
    if phrase in ('используй эту песню для будильника','сделай эту песню звуком будильника'):
        return {'action':'alarm_prepare'}
    prefix=r'поставь (?:музыкальный )?будильник '
    if re.fullmatch(prefix+r'через минуту с этой песней',phrase):return {'action':'set_alarm','delay_minutes':1}
    relative=re.fullmatch(prefix+r'через (.+?) минут(?:у|ы)? с этой песней',phrase)
    if relative:
        minutes=number(relative[1])
        if minutes is not None and 1<=minutes<=180:return {'action':'set_alarm','delay_minutes':minutes}
        return None
    absolute=re.fullmatch(prefix+r'на (.+?) с этой песней',phrase)
    if not absolute:return None
    text=absolute[1];period=None
    for suffix in ('утра','вечера','дня','ночи'):
        if text.endswith(' '+suffix):text=text[:-len(suffix)-1];period=suffix;break
    tokens=text.split();hour=number(text);minute=0
    if hour is not None and hour>23:return None
    if hour is None:
        candidates=[]
        for split in range(1,len(tokens)):
            h=number(' '.join(tokens[:split]));m=number(' '.join(tokens[split:]))
            if h is not None and m is not None and 0<=h<24 and 0<=m<60:candidates.append((h,m))
        if len(candidates)!=1:return None
        hour,minute=candidates[0]
    if not 0<=hour<24:return None
    if period:
        if not 1<=hour<=12:return None
        if period in ('вечера','дня') and hour<12:hour+=12
        if period in ('утра','ночи') and hour==12:hour=0
    return {'action':'set_alarm','time':f'{hour:02}:{minute:02}'}
