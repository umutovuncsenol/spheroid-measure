"""Per-photo labels (day / shape / well size / concentration) read from the file name.

File names look like  "I018_o 600 50 d5_1_1.jpg", "OVAL 50 600 D1 3 KUYU.jpg", "PRAMT_50_600_D1_2.jpg".
Nothing here is authoritative: the researcher can edit every label, and a label that cannot be
read stays None so the UI marks the photo "needs labels".
"""
import re

DAYS = tuple(range(1, 22))   # days read from file names; any day can be typed in by hand
SHAPES = ('Piramit', 'Oval', 'Yamuk')   # usual shapes (suggestions); any name can be typed in
SIZES = (600, 400, 200)          # usual values: used to read file names and as suggestions in the UI;
CONCS = (50, 100, 150, 200)      # any other positive number can be typed in by hand
EMPTY = dict(day=None, shape=None, size=None, conc=None)

_DAY = re.compile(r'(?:^|[^a-z0-9])(?:d|day|gun|gün)[\s_\-]?(\d{1,2})(?![0-9])', re.I)   # d5, DAY1, Day_3, gün 7


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
        if not re.search(r'\d{2,3}', head):   # 'NM-A1-DAY1 o 400 200': size / concentration come after the day
            head = stem[dm.end():]
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
        out['day'] = _day(d['day'])
    if 'shape' in d:
        out['shape'] = _text(d['shape'], 'shape')
    if 'size' in d:
        out['size'] = _number(d['size'], 'well size')
    if 'conc' in d:
        out['conc'] = _number(d['conc'], 'concentration')
    return out


def _day(v):
    """A typed-in day: 'D5', 'day 5', 'Gün 5' or just 5 -> 5."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    t = re.sub(r'^\s*(?:d|day|gun|gün)[\s._-]*', '', str(v).strip(), flags=re.I)
    if not re.fullmatch(r'\d{1,3}', t):
        raise ValueError(f'day must be a whole number (got "{v}")')
    return int(t)


def _text(v, what):
    """A typed-in name. The usual shapes are written the usual way ('oval' -> 'Oval')."""
    if v is None or not str(v).strip():
        return None
    t = re.sub(r'\s+', ' ', str(v).strip())
    if len(t) > 40:
        raise ValueError(f'{what} is too long (40 characters at most)')
    for known in SHAPES:
        if t.lower() == known.lower():
            return known
    return t


def _number(v, what):
    """A typed-in label value: empty -> None, otherwise a positive number (whole numbers stay ints)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        x = float(str(v).strip().replace(',', '.'))
    except ValueError:
        raise ValueError(f'{what} must be a number (got "{v}")') from None
    if not (0 < x < 1e6):
        raise ValueError(f'{what} must be a positive number')
    return int(x) if x == int(x) else round(x, 4)
