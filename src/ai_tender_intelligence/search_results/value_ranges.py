"""Owner-approved EUR presentation brackets only; never ranking or storage."""
from decimal import Decimal, InvalidOperation
import re

MISSING = 'Не е посочена'


def eur_range(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d+)? EUR', value):
        return MISSING
    try:
        amount = Decimal(value[:-4])
    except InvalidOperation:
        return MISSING
    if amount < 100_000:
        return 'До 100 хил. EUR'
    if amount < 500_000:
        return '100–500 хил. EUR'
    if amount < 2_000_000:
        return '500 хил. – 2 млн. EUR'
    return 'Над 2 млн. EUR'
