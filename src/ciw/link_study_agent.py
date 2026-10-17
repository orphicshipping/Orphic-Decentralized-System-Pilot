"""Host-selected MCP execution of the existing retained Link scalar study."""
from pathlib import Path

from . import link_contract as c, link_study as study
from .agent_api import AgentHost, parse
from .control_contracts import save_new
from .control_plane import plan_graph


def validate_graph(value, registry):
    """Only the existing prerequisite chain, without injected result parameters."""
    plan_graph(value, registry)
    if value['model_id'] != 'link-scalar-study.v1' or value['parameters'] or value['parent_checkpoint'] is not None:
        raise ValueError('Link study requires its fixed model and context')
    nodes = {n['node_id']: n for n in value['nodes']}
    seen = set()
    for node in nodes.values():
        op = node['operation_id']
        if op not in study.OPS or op in seen or node['parameters']:
            raise ValueError('Link study requires distinct operations without literal prerequisites')
        seen.add(op)
        i = study.OPS.index(op)
        if i == 0:
            if node['inputs']:
                raise ValueError('Decoder reads only its retained source')
        else:
            field = study._FIELDS[i]
            edge = node['inputs'].get(field)
            if (set(node['inputs']) != {field} or not isinstance(edge, dict)
                    or edge.get('port') != 'result'
                    or nodes.get(edge.get('node_id'), {}).get('operation_id') != study.OPS[i-1]):
                raise ValueError('Link study requires the actual preceding operation result')


class LinkStudyHost(AgentHost):
    """Keep exact request bytes alongside the unchanged native source wrapper."""

    def __init__(self, **kwargs):
        self._recovery = None
        self._requests = {}
        sources = []
        for raw in kwargs['inputs'].values():
            value = parse(raw)
            if value.get('schema') == study.SCHEMA:
                study.validate_request(value)
                self._requests[c.bytes_ref(raw)] = value
            elif 'run_id' in value:
                sources.append(value)
            elif value.get('schema') == 'ciw.experiment.v1':
                validate_graph(value, kwargs['registry'])
        if not sources or not self._requests:
            raise ValueError('Link host requires an exact request and its native source')
        for source in sources:
            self._source(source)
        super().__init__(**kwargs)

    def _source(self, source):
        request = study.source_request(source)
        retained = self._requests.get(source['metadata']['input_bytes_ref'])
        if retained is None or not study._equal(request, retained):
            raise ValueError('Link source differs from the exact host-bound request')

    def validate_plan(self, source, graph):
        self._source(source)
        validate_graph(graph, self._registry)
        return super().validate_plan(source, graph)


def prepare(raw, destination):
    """Create a profile from explicit retained request bytes; never acquire data."""
    request = study.validate_request(c.loads(raw, study.MAX_BYTES))
    source = study.make_source(request, c.bytes_ref(raw))
    root = Path(destination).expanduser().absolute()
    root.mkdir(parents=False, exist_ok=False)
    with (root / 'request.json').open('xb') as stream:
        stream.write(raw)
    save_new(root / 'source.json', source)
    save_new(root / 'experiment.json', study.graph())
    save_new(root / 'profile.json', {
        'schema': 'ciw.agent-host-profile.v1',
        'inputs': {'request': 'request.json', 'source': 'source.json', 'baseline': 'experiment.json'},
        'output_dir': 'agent-output', 'allow_operations': list(study.OPS),
        'candidate_domains': {}, 'comparisons': {}, 'check_suites': {},
        'max_executions': 8, 'max_nodes': 4})
    return root / 'profile.json'


def demo_config(destination):
    from .link_study_cli import example
    return prepare(c.bounded(example(), study.MAX_BYTES), destination)
