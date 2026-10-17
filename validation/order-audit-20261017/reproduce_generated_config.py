"""Run an inert package-shadowing probe against an actual generated MCP config.

Usage: python reproduce_generated_config.py /absolute/path/to/mcp-config.json
No MCP server, provider or equipment operation is started by the marker package.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

config = json.loads(Path(sys.argv[1]).read_text())['mcpServers']['ncp-operational']
with tempfile.TemporaryDirectory() as directory:
    package = Path(directory) / 'ciw'
    package.mkdir()
    (package / '__init__.py').write_text('')
    (package / 'net.py').write_text("print('AUDIT_CURRENT_DIRECTORY_PACKAGE_SHADOWED_INSTALLED_NCP')\n")
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    result = subprocess.run([config['command'], *config['args']], cwd=directory,
                            env=env, text=True, capture_output=True, timeout=20)
    print(json.dumps({'isolated_flag_present': '-I' in config['args'],
                      'exit_code': result.returncode, 'stdout': result.stdout.strip(),
                      'shadowing_reproduced': 'AUDIT_CURRENT_DIRECTORY' in result.stdout}, indent=2))
