"""The optional read client advertises the real backend schemas and hints."""
import json
from pathlib import Path
import re
import unittest

from ciw.offline_intake import TOOLS as INTAKE
from ciw.domain_read_profile import TOOLS as DOMAIN
from ciw.proof_profile import TOOLS as PROOF

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / 'tools/campaign-client'


class CampaignClientMetadata(unittest.TestCase):
    def test_exact_backend_metadata_and_no_mutation_surface(self):
        text = (CLIENT/'src/metadata.ts').read_text()
        match = re.fullmatch(r'// Backend metadata snapshot; tests compare every schema and hint with Python\.\nexport const TOOL_METADATA = (.*) as const;\n', text, re.S)
        self.assertIsNotNone(match)
        actual = json.loads(match.group(1))
        expected = {name: {key: row[key] for key in ('inputSchema','outputSchema','annotations')}
                    for name,row in {**INTAKE,**DOMAIN,**PROOF}.items()}
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 10)
        self.assertTrue(all(row['annotations']['readOnlyHint'] is True and
                            row['annotations']['destructiveHint'] is False for row in actual.values()))

    def test_theme_is_exact_retained_terminal_kit_and_static_page_has_no_endpoint(self):
        self.assertEqual((CLIENT/'web/theme.css').read_bytes(),
            (ROOT/'tools/odrp-private-host-candidate/panel/web/theme.css').read_bytes())
        page = (CLIENT/'web/index.html').read_text()
        self.assertIn('data-orphic-theme="odsp-terminal"',page)
        self.assertIn("connect-src 'none'",page)
        self.assertNotIn('http://',page)
        self.assertNotIn('https://',page)

    def test_compiler_is_pinned_development_dependency_only(self):
        package=json.loads((CLIENT/'package.json').read_text())
        lock=json.loads((CLIENT/'package-lock.json').read_text())
        compiler=json.loads((CLIENT/'compiler-lock.json').read_text())
        self.assertEqual(package['devDependencies'],{'typescript':compiler['version']})
        self.assertNotIn('dependencies',package)
        self.assertEqual(lock['packages']['node_modules/typescript']['integrity'],compiler['registry_integrity'])
        self.assertTrue(compiler['archive_url'].startswith('https://registry.npmjs.org/typescript/'))
        self.assertLess(compiler['archive_bytes']+compiler['extracted_bytes'],40*1024*1024)


if __name__ == '__main__':
    unittest.main(verbosity=2)
