"""Small data-only source programs: HTTPS -> JSON/RSS/CSV -> checked fields.

No generated Python, evaluation, filesystem or Android access. Documentation
must have actually exposed an endpoint before a model may use it. Successful
schema checks do NOT prove that a provider or model interpretation is truthful.
"""
from __future__ import annotations
import csv
import hashlib
from html.parser import HTMLParser
import io
import json
import math
import re
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET
from source_http import SourceError, SECRET, clean_url, endpoint, fetch

NAME = re.compile(r'^[a-z][a-z0-9_]{0,31}$')
KINDS = {'string': str, 'number': (int, float), 'boolean': bool, 'list': list, 'object': dict}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.links, self.hidden = [], [], 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.hidden += 1
        if tag == 'a':
            self.links.extend(v for k, v in attrs if k == 'href' and v)
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.hidden = max(0, self.hidden - 1)
    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def documentation(doc):
    page = Page()
    if doc.media_type == 'text/html':
        page.feed(doc.body)
        text = ' '.join(page.text)
    else:
        text = doc.body
    # Include absolute endpoint examples in code blocks, not only clickable links.
    links = [doc.url] + page.links + re.findall(r'https://[^\s<>"\'`\\]+', text)
    allowed = set()
    for link in links[:400]:
        try:
            allowed.add(endpoint(urljoin(doc.url, link.rstrip('.,;)'))))
        except SourceError:
            continue
    return {'url': doc.url, 'text': text[:18000], 'endpoints': sorted(allowed)[:150],
            'digest': doc.digest, 'retrieved_at': doc.retrieved_at}


def primitive(value):
    if type(value) not in (str, int, float, bool) or (isinstance(value, str) and len(value) > 300):
        raise SourceError('parameter_type')
    if isinstance(value, float) and not math.isfinite(value):
        raise SourceError('nonfinite_number')
    if SECRET.search(str(value)):
        raise SourceError('secret_parameter')
    return value


def reference(path, context):
    if not isinstance(path, str) or not 1 <= len(path) <= 250:
        raise SourceError('invalid_reference')
    value = context
    for part in path.split('.'):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise SourceError('missing_field')
    return value


def typed(value, kind):
    return kind in KINDS and isinstance(value, KINDS[kind]) and not (kind == 'number' and type(value) is bool)


def validate(program, evidence):
    if not isinstance(program, dict) or set(program) != {'name', 'parameters', 'steps', 'outputs'}:
        raise SourceError('program_schema')
    if len(canonical(program)) > 12000 or SECRET.search(canonical(program)):
        raise SourceError('program_size_or_secret')
    if not isinstance(program['name'], str) or not 1 <= len(program['name']) <= 100:
        raise SourceError('program_name')
    params = program['parameters']
    if not isinstance(params, dict) or len(params) > 6 or any(not NAME.fullmatch(k) or v not in ('string', 'number', 'boolean') for k, v in params.items()):
        raise SourceError('parameters_schema')
    if not isinstance(evidence, list) or not evidence or len(evidence) > 3:
        raise SourceError('missing_documentation')
    endpoints = {u for d in evidence for u in d['endpoints']}
    steps = program['steps']
    if not isinstance(steps, list) or not 1 <= len(steps) <= 4:
        raise SourceError('step_limit')
    seen = {'params'}
    used_params = set()
    for step in steps:
        if not isinstance(step, dict) or set(step) != {'id', 'url', 'query', 'format', 'checks'}:
            raise SourceError('step_schema')
        if not NAME.fullmatch(str(step['id'])) or step['id'] in seen:
            raise SourceError('step_id')
        url = clean_url(step['url'])
        if urlsplit(url).query or endpoint(url) not in endpoints:
            raise SourceError('undocumented_endpoint')
        if step['format'] not in ('json', 'rss', 'csv', 'text'):
            raise SourceError('format')
        if not isinstance(step['query'], dict) or len(step['query']) > 16:
            raise SourceError('query_schema')
        for key, value in step['query'].items():
            if not isinstance(key, str) or not 1 <= len(key) <= 80:
                raise SourceError('query_key')
            if isinstance(value, dict):
                if set(value) != {'ref'} or not isinstance(value['ref'], str) or value['ref'].split('.')[0] not in seen:
                    raise SourceError('forward_reference')
                if value['ref'].startswith('params.'):
                    name = value['ref'][7:]
                    if name not in params:
                        raise SourceError('unknown_parameter')
                    used_params.add(name)
            else:
                primitive(value)
        checks = step['checks']
        if not isinstance(checks, list) or not 1 <= len(checks) <= 12:
            raise SourceError('checks_required')
        for check in checks:
            if not isinstance(check, dict) or set(check) - {'path', 'type', 'min_items', 'max_items', 'equals'} or not {'path', 'type'} <= set(check):
                raise SourceError('check_schema')
            if not isinstance(check['path'], str) or not check['path'].startswith(step['id'] + '.') or check['type'] not in KINDS:
                raise SourceError('check_reference')
            for key in ('min_items', 'max_items'):
                if key in check and (type(check[key]) is not int or not 0 <= check[key] <= 100):
                    raise SourceError('check_count')
        seen.add(step['id'])
    if used_params != set(params):
        raise SourceError('unused_parameter')
    outputs = program['outputs']
    if not isinstance(outputs, list) or not 1 <= len(outputs) <= 8:
        raise SourceError('output_limit')
    for item in outputs:
        if not isinstance(item, dict) or set(item) != {'label', 'ref'} or not isinstance(item['label'], str) or not 1 <= len(item['label']) <= 80:
            raise SourceError('output_schema')
        if not isinstance(item['ref'], str) or item['ref'].split('.')[0] not in seen - {'params'}:
            raise SourceError('output_reference')
    return program


def parse_document(doc, kind):
    if kind == 'json':
        if not (doc.media_type.endswith('/json') or doc.media_type.endswith('+json')):
            raise SourceError('expected_json')
        try:
            return json.loads(doc.body, parse_constant=lambda _: (_ for _ in ()).throw(SourceError('nonfinite_json')))
        except (ValueError, RecursionError):
            raise SourceError('invalid_json') from None
    if kind == 'rss':
        if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', doc.body, re.I):
            raise SourceError('xml_entities')
        try:
            root = ET.fromstring(doc.body)
        except ET.ParseError:
            raise SourceError('invalid_xml') from None
        def local(tag):
            return tag.rsplit('}', 1)[-1]
        items = []
        for node in root.iter():
            if local(node.tag) not in ('item', 'entry'):
                continue
            item = {}
            for child in node:
                key = local(child.tag)
                if key in ('title', 'description', 'summary', 'pubDate', 'updated', 'published', 'link'):
                    item[key] = (child.get('href') or ''.join(child.itertext()))[:700]
            items.append(item)
            if len(items) == 10:
                break
        return {'items': items}
    if kind == 'csv':
        rows = []
        for row in csv.DictReader(io.StringIO(doc.body)):
            if len(row) > 30:
                raise SourceError('csv_columns')
            rows.append(row)
            if len(rows) >= 50:
                break
        return {'rows': rows}
    return {'text': documentation(doc)['text'][:5000]}


def execute(program, params, evidence, *, reader=fetch, cancelled=lambda: False):
    validate(program, evidence)
    if not isinstance(params, dict) or set(params) != set(program['parameters']):
        raise SourceError('missing_parameters')
    for name, kind in program['parameters'].items():
        primitive(params[name])
        if not typed(params[name], kind):
            raise SourceError('parameter_type')
    context, sources = {'params': params}, []
    for step in program['steps']:
        if cancelled():
            raise SourceError('cancelled')
        query = {k: primitive(reference(v['ref'], context)) if isinstance(v, dict) else v for k, v in step['query'].items()}
        url = step['url'] + ('?' + urlencode(query) if query else '')
        doc = reader(clean_url(url), cancelled=cancelled)
        context[step['id']] = parse_document(doc, step['format'])
        for check in step['checks']:
            value = reference(check['path'], context)
            if not typed(value, check['type']):
                raise SourceError('result_type')
            if 'equals' in check and value != check['equals']:
                raise SourceError('result_mismatch')
            if 'min_items' in check and (not isinstance(value, list) or len(value) < check['min_items']):
                raise SourceError('no_matching_result')
            if 'max_items' in check and (not isinstance(value, list) or len(value) > check['max_items']):
                raise SourceError('ambiguous_result')
        sources.append({'url': doc.url, 'retrieved_at': doc.retrieved_at, 'sha256': doc.digest})
    if cancelled():
        raise SourceError('cancelled')
    fields = [{'label': item['label'], 'value': reference(item['ref'], context)} for item in program['outputs']]
    lines = []
    for item in fields:
        value = item['value']
        rendered = canonical(value) if isinstance(value, (dict, list)) else str(value)
        lines.append(item['label'] + ': ' + rendered[:300])
    # Read time and provider's own update time are intentionally not conflated.
    reply = '\n'.join(lines)[:1000] + '\nПолучено: ' + sources[-1]['retrieved_at']
    reply += '\nИсточники: ' + ' ; '.join(dict.fromkeys(s['url'] for s in sources))
    return {'reply': reply[:1500], 'fields': fields, 'sources': sources}
