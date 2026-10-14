"""Per-photo labels (day / shape / well size / concentration) read from the file name.

File names look like  "I018_o 600 50 d5_1_1.jpg", "OVAL 50 600 D1 3 KUYU.jpg", "PRAMT_50_600_D1_2.jpg".
Nothing here is authoritative: the researcher can edit every label, and a label that cannot be
read stays None so the UI marks the photo "needs labels".
"""
import re

DAYS = (1, 3, 5, 7)
SHAPES = ('Piramit', 'Oval', 'Yamuk')
SIZES = (600, 400, 200)
CONCS = (50, 100, 150, 200)
EMPTY = dict(day=None, shape=None, size=None, conc=None)

_DAY = re.compile(r'(?:^|[^a-z0-9])d(\d{1,2})(?![0-9])', re.I)


def _shape(tok):
    t = tok.lower()
    if t.startswith('ov') or t == 'o':
        return 'Oval'
    if t.startswith('yam') or t == 'y':
        return 'Yamuk'
    if t == 'p' or t.startswith('pr') or t.startswith('pi') or t.startswith('p_'):
        return 'Piramit'
    return None


def parse_labels(filename):
    out = dict(EMPTY)
    stem = re.sub(r'\.[A-Za-z0-9]+$', '', filename or '')
    stem = re.sub(r'^I\d{3,4}_', '', stem)  # archive id prefix added to example files
    # shape: first alphabetic token (separators: space, underscore, dash)
    toks = re.split(r'[\s_\-]+', stem)
    for tok in toks[:3]:
        if tok.isalpha():
            if tok.lower() == 'p' and len(toks) > 2 and toks[1].lower() == 'ram':  # P_RAM_T
                out['shape'] = 'Piramit'
            else:
                out['shape'] = _shape(tok)
            break
    dm = _DAY.search(stem)
    head = stem
    if dm:
        d = int(dm.group(1))
        out['day'] = d if d in DAYS else None
        head = stem[:dm.start()]
    nums = [int(n) for n in re.findall(r'(?<![0-9])(\d{2,3})(?![0-9])', head)]
    size = conc = None
    twos = 0
    for n in nums:
        if n in (400, 600):
            size = size or n
        elif n in (50, 100, 150):
            conc = conc or n
        elif n == 200:
            twos += 1
    if twos:
        if twos >= 2:
            size, conc = size or 200, conc or 200
        elif size and not conc:
            conc = 200
        elif conc and not size:
            size = 200
        # a lone 200 with nothing else is ambiguous -> stays unknown
    out['size'], out['conc'] = size, conc
    return out


def labels_complete(lb):
    return bool(lb) and all(lb.get(k) is not None for k in ('day', 'shape', 'size', 'conc'))


def clean_labels(d):
    """Validate user-supplied labels (partial dict). Returns only the valid, normalised keys."""
    out = {}
    if 'day' in d:
        out['day'] = None if d['day'] in (None, '') else int(d['day'])
        if out['day'] is not None and out['day'] not in DAYS:
            raise ValueError('day must be one of 1, 3, 5, 7')
    if 'shape' in d:
        out['shape'] = d['shape'] or None
        if out['shape'] is not None and out['shape'] not in SHAPES:
            raise ValueError('shape must be Piramit, Oval or Yamuk')
    if 'size' in d:
        out['size'] = None if d['size'] in (None, '') else int(d['size'])
        if out['size'] is not None and out['size'] not in SIZES:
            raise ValueError('well size must be 600, 400 or 200')
    if 'conc' in d:
        out['conc'] = None if d['conc'] in (None, '') else int(d['conc'])
        if out['conc'] is not None and out['conc'] not in CONCS:
            raise ValueError('concentration must be 50, 100, 150 or 200')
    return out
