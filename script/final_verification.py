"""Full acceptance verification for the built patch in asset/.

Checks: Arc integrity (no padding), call-chain completeness, resource
classification rules, resource pairing, and rebuild idempotency (SHA256
stability).
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tool import arcbuild, ws2  # noqa: E402

ASSET = ROOT / 'asset'

CALL_CHAIN = {
    'CCA0025C_en.ws2': ('CNR0001_EN', 'CCA0029_EN'),
    'CCB1014C_en.ws2': ('CNR0002_EN', 'CCB0020_EN'),
    'CCB2013_en.ws2': ('CNR0003_EN', 'CCB2014_EN'),
    'CCB2101_en.ws2': ('CNR0004_EN', 'CCB2019_EN'),
    'CCC3027_en.ws2': ('CNR0006_EN', 'CCC3028_EN'),
    'CCC4022_en.ws2': ('CNR0007_EN', 'CCC4023_EN'),
    'CCD0022A_en.ws2': ('CNR0008_EN', 'CCD0000_EN'),
    'CCD1001B_en.ws2': ('CNR00009_EN', 'CCD1001C_EN'),
    'CCD4003A_en.ws2': ('CNR00010_EN', 'CCD0023A_EN'),
    'CCD5001A_en.ws2': ('CNR00011_EN', 'CCD5001B_EN'),
}
# CCC0000_en has two H insertions (branching entry point).
CCC0000_ENTRY = 'CCC0000_en.ws2'
CCC0000_HS = ['CNR0005_EN', 'CNR0105_EN']
CCC0000_EXITS = {'CNR0005_EN': 'CCC0001_EN', 'CNR0105_EN': 'CCC0002_EN'}

failures = []
passes = 0


def ok(msg):
    global passes
    passes += 1
    print('[OK] %s' % msg)


def fail(msg):
    failures.append(msg)
    print('[FAIL] %s' % msg)


def name_of(entry):
    return entry[0].decode('utf-16le')


def load_arc(path):
    return {name_of(m): m[1] for m in arcbuild.read_raw(path)}


def check_arc_integrity():
    print('\n== Arc integrity ==')
    for f in ['Rio.arc', 'Graphic.arc', 'Chip2.arc', 'Voice.arc', 'Fonts.arc',
              'Script.arc', 'SysGraphic.arc']:
        path = ASSET / f
        if not path.exists():
            fail('%s missing from asset/' % f)
            continue
        try:
            count, size, end = arcbuild.verify(path)
            ok('%s verified (%d members, %d bytes, no padding)' % (f, count, size))
        except Exception as e:
            fail('%s failed verify(): %s' % (f, e))


def check_call_chain(rio):
    print('\n== Call chain ==')
    names_upper = {n.upper(): n for n in rio.keys()}

    for entry, (h_target, exit_target) in CALL_CHAIN.items():
        data = rio.get(entry)
        if data is None:
            fail('entry script missing: %s' % entry)
            continue
        jumps = set(ws2.extract_all_jumps(ws2.decode(data)))
        if h_target not in jumps:
            fail('%s does not jump to %s' % (entry, h_target))
        else:
            ok('%s -> %s' % (entry, h_target))

        h_ws2 = (h_target + '.ws2').upper()
        if h_ws2 not in names_upper:
            fail('H scene script missing: %s' % h_ws2)
            continue
        h_data = rio[names_upper[h_ws2]]
        h_jumps = set(ws2.extract_all_jumps(ws2.decode(h_data))) - {'LAYER_ORDER'}
        if exit_target not in h_jumps:
            fail('%s does not exit to %s (found %s)' % (h_target, exit_target, sorted(h_jumps)))
        else:
            ok('%s -> %s (exit)' % (h_target, exit_target))
        if (exit_target + '.ws2').upper() not in names_upper:
            fail('exit target missing: %s.ws2' % exit_target)

    data = rio.get(CCC0000_ENTRY)
    if data is None:
        fail('entry script missing: %s' % CCC0000_ENTRY)
    else:
        jumps = set(ws2.extract_all_jumps(ws2.decode(data)))
        for h_target in CCC0000_HS:
            if h_target not in jumps:
                fail('%s does not jump to %s' % (CCC0000_ENTRY, h_target))
                continue
            ok('%s -> %s' % (CCC0000_ENTRY, h_target))
            h_ws2 = (h_target + '.ws2').upper()
            if h_ws2 not in names_upper:
                fail('H scene script missing: %s' % h_ws2)
                continue
            h_jumps = set(ws2.extract_all_jumps(ws2.decode(rio[names_upper[h_ws2]]))) - {'LAYER_ORDER'}
            exit_target = CCC0000_EXITS[h_target]
            if exit_target not in h_jumps:
                fail('%s does not exit to %s (found %s)' % (h_target, exit_target, sorted(h_jumps)))
            else:
                ok('%s -> %s (exit)' % (h_target, exit_target))


def check_lng_pairing(rio):
    print('\n== LNG pairing ==')
    cnr_ws2 = [n for n in rio if n.upper().startswith('CNR') and n.lower().endswith('.ws2')]
    missing = [n for n in cnr_ws2 if n[:-4] + '.lng' not in rio]
    if missing:
        fail('CNR scripts missing paired .lng: %s' % missing)
    else:
        ok('all %d CNR scripts have a paired .lng' % len(cnr_ws2))


def check_resource_classification(graphic, chip2):
    print('\n== Resource classification ==')
    bad_chip2 = [n for n in chip2 if not n.upper().startswith('EVCC') and not n.upper().startswith('CN_EVCC')]
    if bad_chip2:
        fail('Chip2.arc has non-EVCC members: %s' % bad_chip2[:10])
    else:
        ok('Chip2.arc contains only EVCC*/CN_EVCC* (%d members)' % len(chip2))

    bad_graphic = [n for n in graphic if n.upper().startswith('CN_EVCC')]
    if bad_graphic:
        fail('Graphic.arc contains CN_EVCC* members (should be in Chip2.arc): %s' % bad_graphic)
    else:
        ok('Graphic.arc contains no CN_EVCC* members')

    if 'CN_SGCC0020.PNG' not in graphic:
        fail('Graphic.arc missing CN_SGCC0020.PNG')
    else:
        ok('Graphic.arc contains CN_SGCC0020.PNG')


def check_resource_pairing(rio, graphic, chip2):
    print('\n== Resource pairing (CNR scripts) ==')
    available = set(n.upper() for n in graphic) | set(n.upper() for n in chip2)
    cnr_ws2 = [n for n in rio if n.upper().startswith('CNR') and n.lower().endswith('.ws2')]

    placeholders = {'BLACK', 'WHITE'}
    missing_refs = []
    for name in cnr_ws2:
        dec = ws2.decode(rio[name])
        for ref in ws2.extract_png_refs(dec):
            if ref.upper() in placeholders:
                continue
            fname = ref.upper() + '.PNG'
            if fname not in available:
                missing_refs.append((name, ref))
    if missing_refs:
        fail('unresolved PNG refs in CNR scripts: %s' % missing_refs[:10])
    else:
        ok('all CNR script PNG refs resolve in Graphic.arc + Chip2.arc')


def check_idempotency():
    print('\n== Idempotency (rebuild stability) ==')
    for f in ['Rio.arc', 'Graphic.arc', 'Chip2.arc']:
        path = ASSET / f
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        members = arcbuild.read_raw(path)
        arcbuild.write_arc(members, path)
        after = hashlib.sha256(path.read_bytes()).hexdigest()
        if before != after:
            fail('%s SHA256 changed after re-read/re-write (%s -> %s)' % (f, before[:12], after[:12]))
        else:
            ok('%s SHA256 stable across rebuild (%s)' % (f, before[:12]))


def main():
    check_arc_integrity()

    rio = load_arc(ASSET / 'Rio.arc')
    graphic = load_arc(ASSET / 'Graphic.arc')
    chip2 = load_arc(ASSET / 'Chip2.arc')

    check_call_chain(rio)
    check_lng_pairing(rio)
    check_resource_classification(graphic, chip2)
    check_resource_pairing(rio, graphic, chip2)
    check_idempotency()

    print('\n== Summary ==')
    print('%d checks passed, %d failed' % (passes, len(failures)))
    if failures:
        print('[FAIL] verification failed')
        sys.exit(1)
    print('[OK] all checks passed')


if __name__ == '__main__':
    main()
