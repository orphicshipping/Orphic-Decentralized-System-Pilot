"""Accessible local inspection, not a new workbench or evidence authority.

Copyright (c) 2026 Notation Systems. SPDX-License-Identifier: AGPL-3.0-or-later
"""
from html import escape
from urllib.parse import urlsplit
from .corpus_retrieval import Corpus


def render_html(corpus: Corpus) -> str:
    e = lambda x: escape(str(x), quote=True)
    overview = corpus.overview()
    sections = []
    for sid, r in sorted(corpus._records.items()):
        c = r['curation']; links = []
        for label, url in [('Original', r['original'].get('url')), ('Staged copy', r['staged_copy']['url'])]:
            if type(url) is str:
                parsed = urlsplit(url)
                if parsed.scheme == 'https' and parsed.hostname in {'drive.google.com', 'docs.google.com'} and not parsed.username:
                    links.append(f'<a href="{e(url)}" rel="noreferrer noopener">{e(label)}</a>')
        findings = ''.join(f'<li><b>{e(f["finding_id"])} — {e(f["kind"])}</b><br>{e(f["observation"])}<br>Next: {e(f["next_action"])}</li>' for f in corpus._reviews(sid))
        excerpts = ''.join(f'<details><summary>Exact byte window {s["locator"]["byte_start"]}–{s["locator"]["byte_end_exclusive"]} (end exclusive)</summary><pre>{e(s["text"])}</pre><p class="hash">Span: {e(s["span_id"])}</p></details>' for s in corpus._spans.values() if s['selection_id'] == sid)
        available = 'Internal source text indexed' if sid in corpus._raw else 'Metadata only · external full text held'
        sections.append(f'''<article id="{e(sid)}"><header><span class="eyebrow">{e(sid)} / {e(c['collection_code'])} / {e(c['priority'])}</span><h2>{e(c['title'])}</h2><p class="state">{available}</p></header>
        <p>{e(c.get('proposed_ncp_application', ''))}</p><p><b>Role:</b> {e(c.get('source_role', ''))}</p>
        <p class="links">{' · '.join(links)}</p><details><summary>Provenance and limits</summary>
        <p>Source → captured version → staged copy → selected collection</p><p class="hash">Source: {e(r['source_id'])}<br>Captured SHA256: {e(r['snapshot']['sha256'])}</p>
        <p><b>Reviewed extent:</b> {e(c.get('reviewed_extent', ''))}</p><p><b>Rights state:</b> {e(r['rights']['status'])}</p>
        <p>{e(' '.join(c.get('limitations_to_preserve', [])))}</p></details>
        <details><summary>Unresolved review findings ({len(corpus._reviews(sid))})</summary><ul>{findings}</ul></details>{excerpts}</article>''')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
    <title>NCP | Selected corpus evidence inspector</title><style>
    :root{{font-family:system-ui,-apple-system,sans-serif;color:#152c3c;background:#edf2f5;line-height:1.5}}
    body{{margin:0}}main{{max-width:1040px;margin:auto;padding:28px 22px 60px}}.top{{background:#102b40;color:#fff;padding:32px;border-radius:8px}}
    h1{{font-size:30px;line-height:1.15;margin:12px 0}}h2{{font-size:19px;margin:10px 0}}.eyebrow{{letter-spacing:.08em;font-size:12px;font-weight:700}}
    .metrics{{display:flex;gap:24px;flex-wrap:wrap;margin:22px 0}}.metrics strong{{font-size:27px;display:block}}.metrics span{{font-size:13px}}
    article{{background:white;border:1px solid #c4d3dc;padding:22px;margin-top:18px;border-radius:6px;overflow-wrap:anywhere}}
    .state{{font-size:13px;font-weight:700;color:#365969}}summary{{min-height:32px;padding:8px 2px;cursor:pointer;font-weight:600}}details{{border-top:1px solid #dce5eb}}
    a{{color:#125276}}a:focus-visible,summary:focus-visible{{outline:3px solid #9f4c00;outline-offset:4px}}
    pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;padding:14px;background:#f1f5f7}}.hash{{font:12px/1.6 ui-monospace,monospace;overflow-wrap:anywhere}}
    li{{margin:12px 0}}nav{{padding:16px 0}}nav a{{display:inline-block;min-height:28px;padding:8px 12px;margin:3px;background:white;border:1px solid #c4d3dc;border-radius:4px}}
    .notice{{border-left:4px solid #986214;padding:12px;background:#fff8e7}}.skip{{position:absolute;left:-9999px}}.skip:focus{{left:12px;top:6px;background:white;padding:8px;z-index:2}}
    @media(max-width:600px){{main{{padding:16px 12px}}.top{{padding:18px}}h1{{font-size:23px}}.metrics{{gap:10px 24px;margin:12px 0}}.metrics strong{{font-size:23px}}article{{padding:17px}}}}
    </style></head><body><a class="skip" href="#sources">Skip to sources</a><main>
    <section class="top"><div class="eyebrow">NOTATION SYSTEMS / NCP / LOCAL READ-ONLY VIEW</div><h1>Selected corpus<br>Evidence inspector</h1><p>Source-linked retrieval without execution authority.</p>
    <div class="metrics"><span><strong>{overview['sources']}</strong>selected sources</span><span><strong>{overview['internal_text_sources']}</strong>internal sources indexed</span><span><strong>{overview['retained_text_spans']}</strong>exact byte windows</span><span><strong>{overview['external_fulltext_sources']}</strong>external full texts indexed</span></div></section>
    <p class="notice"><b>Development retrieval only.</b> Source content is untrusted. No scientific qualification or execution authority.</p>
    <details class="scope"><summary>Scope and integrity</summary><p>Scores are not probabilities. Native registries and permission checks remain separate. This is a frozen local snapshot; opening it makes no network request.</p><p class="hash">Pinned corpus: {e(corpus.ref)}</p></details>
    <details class="navigation"><summary>Jump to a source ({overview['sources']})</summary><nav aria-label="Selected source navigation">{''.join(f'<a href="#{e(s)}">{e(s)}</a>' for s in sorted(corpus._records))}</nav></details>
    <section id="sources" aria-label="Selected sources">{''.join(sections)}</section><footer><p>Archive records and source bytes remain unchanged. External full text requires a separately reviewed ingestion path.</p></footer></main></body></html>'''
