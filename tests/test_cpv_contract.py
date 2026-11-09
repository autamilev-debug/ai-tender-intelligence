"""Synthetic response bodies for the deterministic CPV validation boundary."""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock

import pytest

from ai_tender_intelligence.catalogue.source import CPVEntry, CatalogueError
from ai_tender_intelligence.catalogue.validation import CatalogueSnapshot
from ai_tender_intelligence.classification.contract import (
    VERSION, ClassificationError, Reply, Request, encoded, validate_response,
)
from helpers import classification_response


@pytest.fixture
def contract_case():
    entries = (
        CPVEntry('45261910', '6', 'Synthetic roof repair'),
        CPVEntry('72262000', '9', 'Synthetic software development'),
    )
    snapshot = CatalogueSnapshot('synthetic-catalogue', {entry.code: entry for entry in entries})
    categories = tuple(
        {'category_key': key, 'activity': entry.description_bg}
        for key, entry in zip(('construction', 'software'), entries)
    )
    candidates = {
        category['category_key']: [{'identifier': entry.identifier, 'description_bg': entry.description_bg}]
        for category, entry in zip(categories, entries)
    }
    return Request(snapshot.version_id, categories, candidates, None), snapshot


def test_valid_empty_selection_has_terminal_no_match_reason(contract_case):
    request, snapshot = contract_case
    body = classification_response(request)
    body['selections'] = []
    for review in body['category_reviews']:
        review['no_match_reason'] = 'The candidate is not supported by the activity.'
    with pytest.raises(ClassificationError, match='^NO_SUPPORTED_MATCH$'):
        validate_response(Reply(encoded(body)), request, snapshot)


@pytest.mark.parametrize(('defect', 'reason'), [
    ('missing_review', 'CATEGORY_COVERAGE'),
    ('duplicate_review', 'CATEGORY_COVERAGE'),
    ('wrong_category', 'CATEGORY_COVERAGE'),
    ('foreign_candidate', 'CATEGORY_COVERAGE'),
    ('null_explanation', 'CATEGORY_COVERAGE'),
    ('blank_explanation', 'INVALID_RESPONSE'),
    ('oversized_explanation', 'INVALID_RESPONSE'),
    ('non_string_explanation', 'INVALID_RESPONSE'),
])
def test_empty_selection_requires_consistent_reviews(contract_case, defect, reason):
    request, snapshot = contract_case
    body = classification_response(request)
    body['selections'] = []
    reviews = body['category_reviews']
    for review in reviews:
        review['no_match_reason'] = 'No supported candidate.'
    if defect == 'missing_review':
        reviews.pop()
    elif defect == 'duplicate_review':
        reviews.append(deepcopy(reviews[0]))
    elif defect == 'wrong_category':
        reviews[0]['category_key'] = 'unrequested'
    elif defect == 'foreign_candidate':
        reviews[0]['considered_codes'] = reviews[1]['considered_codes']
    else:
        reviews[0]['no_match_reason'] = {
            'null_explanation': None,
            'blank_explanation': '  ',
            'oversized_explanation': 'x' * 1001,
            'non_string_explanation': [],
        }[defect]
    with pytest.raises(ClassificationError, match=f'^{reason}$'):
        validate_response(Reply(encoded(body)), request, snapshot)


@pytest.mark.parametrize(('defect', 'reason'), [
    ('duplicate', 'INVALID_CODE_SET'),
    ('unknown', 'CODE_NOT_FOUND'),
    ('wrong_check_digit', 'CHECK_DIGIT_MISMATCH'),
    ('missing_check_digit', 'INVALID_IDENTIFIER'),
])
def test_nonempty_selection_preserves_safe_catalogue_failures(contract_case, defect, reason):
    request, snapshot = contract_case
    body = classification_response(request)
    first = body['selections'][0]
    if defect == 'duplicate':
        body['selections'].append(deepcopy(first))
    else:
        first['identifier'] = {
            'unknown': '99999999-9',
            'wrong_check_digit': first['identifier'][:-1] + '0',
            'missing_check_digit': first['identifier'][:-2],
        }[defect]
    with pytest.raises(ClassificationError, match=f'^{reason}$'):
        validate_response(Reply(encoded(body)), request, snapshot)


def test_unrelated_catalogue_error_is_not_exposed(contract_case):
    request, snapshot = contract_case
    failing = Mock(version_id=snapshot.version_id)
    failing.validate_set.side_effect = CatalogueError('DATABASE_OPERATION_FAILED')
    with pytest.raises(ClassificationError, match='^INVALID_RESPONSE$'):
        validate_response(Reply(encoded(classification_response(request))), request, failing)


@pytest.mark.parametrize('mode', ['single', 'both_categories', 'one_category_no_match'])
def test_supported_selections_are_verified(contract_case, mode):
    request, snapshot = contract_case
    if mode == 'single':
        request = replace(request, categories=request.categories[:1])
    body = classification_response(request)
    if mode == 'one_category_no_match':
        body['selections'].pop()
        body['category_reviews'][1]['no_match_reason'] = 'No supported software candidate.'
    expected = ('45261910', '72262000') if mode == 'both_categories' else ('45261910',)
    assert validate_response(Reply(encoded(body)), request, snapshot) == (expected, body)


@pytest.mark.parametrize(('defect', 'reason'), [
    ('malformed_json', 'INVALID_RESPONSE'),
    ('duplicate_json_key', 'INVALID_RESPONSE'),
    ('unsupported_quote', 'UNSUPPORTED_ACTIVITY'),
    ('contradictory_no_match', 'CATEGORY_COVERAGE'),
])
def test_response_validation_still_rejects_invalid_content(contract_case, defect, reason):
    request, snapshot = contract_case
    body = classification_response(request)
    if defect == 'unsupported_quote':
        body['selections'][0]['supports'][0]['activity_quote'] = 'Unstated activity'
    elif defect == 'contradictory_no_match':
        body['category_reviews'][0]['no_match_reason'] = 'No supported candidate.'
    raw = encoded(body)
    if defect == 'malformed_json':
        raw = raw[:-1]
    elif defect == 'duplicate_json_key':
        raw = b'{"schema_version":"' + VERSION.encode('ascii') + b'",' + raw[1:]
    with pytest.raises(ClassificationError, match=f'^{reason}$'):
        validate_response(Reply(raw), request, snapshot)
