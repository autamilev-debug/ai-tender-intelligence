"""Optional display-only eForms values from already referenced official evidence."""
from hashlib import sha256
import re
import xml.etree.ElementTree as ET
from ..discovery.ted import NS


def verified_root(raw, expected_hash):
    if (len(raw) > 8_000_000 or sha256(raw).hexdigest() != expected_hash
            or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper() or b'\x00' in raw):
        return None
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError, RecursionError):
        return None
    return root


def amount_value(node):
    amount = node.find('c:RequestedTenderTotal/b:EstimatedOverallContractAmount', NS) if node is not None else None
    if (amount is not None and re.fullmatch(r'\d+(?:\.\d+)?', amount.text or '')
            and re.fullmatch(r'[A-Z]{3}', amount.get('currencyID', ''))):
        return amount.text + ' ' + amount.get('currencyID')
    return None


def procedure_value(raw, expected_hash):
    root = verified_root(raw, expected_hash)
    return amount_value(root.find('c:ProcurementProject', NS)) if root is not None else None


def estimated_values(raw, expected_hash):
    root = verified_root(raw, expected_hash)
    if root is None:
        return {}
    values = {}
    for lot in root.findall('c:ProcurementProjectLot', NS):
        identifier = lot.findtext('b:ID', namespaces=NS)
        amount = amount_value(lot.find('c:ProcurementProject', NS))
        if identifier and amount:
            values[identifier] = amount
    return values
