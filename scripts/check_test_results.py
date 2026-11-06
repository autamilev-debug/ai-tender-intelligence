"""Prevent an apparently green CI run with skipped database integration tests."""
import argparse
import xml.etree.ElementTree as ET


def check(path, minimum=25):
    cases = list(ET.parse(path).getroot().iter('testcase'))
    if len(cases) < minimum or any(
        c.find(tag) is not None for c in cases for tag in ('skipped', 'failure', 'error')
    ):
        raise ValueError('Integration tests missing, skipped or unsuccessful')
    return len(cases)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('path')
    parser.add_argument('--minimum', type=int, default=25)
    args = parser.parse_args()
    print('Executed PostgreSQL integration tests:', check(args.path, args.minimum))
