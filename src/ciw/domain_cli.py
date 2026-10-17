"""Optional private provider dispatch; saved files never install or select code."""
import argparse
import json
import sys


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv in (["--help"], ["-h"]):
        parser=argparse.ArgumentParser(prog="net domains",description=__doc__)
        parser.add_argument("command",choices=["bind","example","run","demo","inspect","verify","replay"],
                            help="Requires explicitly installed notation-domains; no automatic installation")
        parser.print_help()
        return 0
    try:
        from notation_domains.cli import main as domain_main
    except ModuleNotFoundError as exc:
        if exc.name not in {"notation_domains", "notation_domains.cli"}:
            raise
        print(json.dumps({"status":"REFUSE", "reason":"Install the private notation-domains provider explicitly; no automatic installation"}), file=sys.stderr)
        return 2
    return domain_main(argv)
