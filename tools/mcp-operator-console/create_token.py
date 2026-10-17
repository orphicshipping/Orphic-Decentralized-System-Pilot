"""Create a new private token file without printing the secret or replacing a file."""
import argparse
import os
from pathlib import Path
import secrets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(secrets.token_urlsafe(48) + '\n')
    print('Private token file created. Read it locally to unlock your console.')


if __name__ == '__main__':
    main()
