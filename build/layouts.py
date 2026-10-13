#!/usr/bin/env python3
"""Which buildings have floor plans on their landlord's own site, and where.

    python3 -m build.layouts          # writes app/data/layouts.json

The plans themselves are in the apartment database (apartment-data/, stored,
never shown: CLAUDE.md). This writes only what the public map may carry: for a building, the
landlord's own page that shows its plans, and how many plans that page has. The map links
to that page. No plan image or address of one goes into app/.

A plan belongs to a building when the page it was found on is the page a listing for that
building links to (collect/state.db, units.links). A page that leads to no building, or to
more than one, is left out and counted. Standard library only.
"""
import collections, datetime, json, pathlib, sqlite3

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATABASE = pathlib.Path('apartment-data/apartments.db')
STATE = ROOT / 'collect/state.db'
OUT = ROOT / 'app/data/layouts.json'


def key(url):
    return url.split('?')[0].split('#')[0].rstrip('/')


def join(plan_pages, unit_links):
    """plan_pages: {page_url: plans on it}. unit_links: [(building_id, url), ...].
    Returns ({building_id: {'url': page, 'plans': n}}, pages with no building, pages with several)."""
    by_page = collections.defaultdict(set)
    for bid, url in unit_links:
        by_page[key(url)].add(bid)
    out, none, several = {}, 0, 0
    for page, n in plan_pages.items():
        k = key(page)
        owners = set(by_page.get(k, ()))
        if not owners:
            for u, b in by_page.items():
                if u.startswith(k + '/'):
                    owners |= b
        if not owners:
            none += 1
        elif len(owners) > 1:
            several += 1
        elif '/api/' not in page:              # a data address is not a page a person can read
            bid = next(iter(owners))
            if bid not in out or n > out[bid]['plans']:
                out[bid] = {'url': page, 'plans': n}
    return out, none, several


def main():
    db = sqlite3.connect(f'file:{DATABASE}?mode=ro', uri=True)
    pages = collections.Counter(p for (p,) in db.execute(
        "select page_url from asset where kind = 'plan' and status = 'ok' and page_url is not null"))
    st = sqlite3.connect(f'file:{STATE}?mode=ro', uri=True)
    links = [(bid, l['url']) for bid, raw in st.execute('select building_id, links from units')
             for l in json.loads(raw) if l.get('url')]
    out, none, several = join(pages, links)
    doc = {'generated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'counts': {'pages_with_plans': len(pages), 'buildings': len(out), 'pages_no_building': none,
                      'pages_several_buildings': several},
           'buildings': {str(k): v for k, v in sorted(out.items())}}
    OUT.write_text(json.dumps(doc, separators=(',', ':')) + '\n')
    print(f"layouts: {len(out)} buildings from {len(pages)} pages with plans "
          f"({none} pages matched no building, {several} matched several)")


if __name__ == '__main__':
    main()
