# Readiness pack: pypa/packaging#1449

- Status: **evidence assembled**
- Issue: https://github.com/pypa/packaging/issues/1449 (state: open, observed 2026-10-15 12:14 UTC)
- Code pinned to commit `ef837aad3127167671eb37b9f6bd8a94ec7ce26e`

## Candidate code (lexical match, review before trusting)
- `src.packaging.markers.InvalidMarker` (class) - name 'InvalidMarker' appears in the issue title
- `src.packaging.requirements.InvalidRequirement` (class) - name 'InvalidRequirement' appears in the issue title
- `src.packaging.metadata.Metadata.from_email` (method) - full dotted name 'src.packaging.metadata.Metadata.from_email' in issue text
- `src.packaging.pylock.Pylock.from_dict` (method) - full dotted name 'src.packaging.pylock.Pylock.from_dict' in issue text
- `src.packaging.markers.Marker.__init__` (method) - full dotted name 'src.packaging.markers.Marker.__init__' in issue text

## Static test candidates for `src.packaging.markers.InvalidMarker` (not executed)
- `tests.test_markers.TestOperatorEvaluation.test_prefers_pep440` [EXACT] via `src.packaging.markers.Marker.__init__`
- `tests.test_markers.TestOperatorEvaluation.test_new_string_rules` [EXACT] via `src.packaging.markers.Marker.__init__`
- `tests.test_markers.TestOperatorEvaluation.test_fails_when_undefined` [EXACT] via `src.packaging.markers.Marker.__init__`
- `tests.test_markers.TestOperatorEvaluation.test_arbitrary_equality_on_non_version_key_is_undefined` [EXACT] via `src.packaging.markers.Marker.__init__`
- `tests.test_markers.TestOperatorEvaluation.test_allows_prerelease` [EXACT] via `src.packaging.markers.Marker.__init__`
- `tests.test_markers.TestMarker.test_parses_valid` [EXACT] via `src.packaging.markers.Marker.__init__`
- `tests.test_markers.TestMarker.test_parses_invalid` [EXACT] via `src.packaging.markers.Marker.__init__`
- `tests.test_markers.TestMarker.test_parses_invalid_trailing_line_break` [EXACT] via `src.packaging.markers.Marker.__init__`

## Static test candidates for `src.packaging.requirements.InvalidRequirement` (not executed)
- `tests.test_metadata.TestMetadata.test_pep808_dynamic_with_static_values` [EXACT] via `src.packaging.requirements.Requirement.__init__`
- `tests.test_requirements.test_basic_valid_requirement_parsing` [EXACT] via `src.packaging.requirements.Requirement.__init__`
- `tests.test_requirements.test_normalized_requirements` [EXACT] via `src.packaging.requirements.Requirement.__init__`
- `tests.test_requirements.test_requirement_marker_with_embedded_double_quote_round_trips` [EXACT] via `src.packaging.requirements.Requirement.__init__`
- `tests.test_requirements.TestRequirementParsing.test_valid_marker` [EXACT] via `src.packaging.requirements.Requirement.__init__`
- `tests.test_requirements.TestRequirementParsing.test_file_url` [EXACT] via `src.packaging.requirements.Requirement.__init__`
- `tests.test_requirements.TestRequirementParsing.test_error_when_suffixed_with_line_break` [EXACT] via `src.packaging.requirements.Requirement.__init__`
- `tests.test_requirements.TestRequirementParsing.test_error_when_url_embeds_line_break` [EXACT] via `src.packaging.requirements.Requirement.__init__`

## Static test candidates for `src.packaging.metadata.Metadata.from_email` (not executed)
- `tests.test_metadata.TestMetadata.test_from_email` [EXACT]
- `tests.test_metadata.TestMetadata.test_from_email_empty_import_name` [EXACT]
- `tests.test_metadata.TestMetadata.test_from_email_validate_returns_valid_metadata` [EXACT]
- `tests.test_metadata.TestMetadata.test_from_email_unparsed` [EXACT]
- `tests.test_metadata.TestMetadata.test_from_email_validate` [EXACT]
- `tests.test_metadata.TestMetadata.test_from_email_description_content_type_lowercase_charset` [EXACT]
- `tests.test_metadata.TestMetadata.test_from_email_unparsed_valid_field_name` [EXACT]
- `tests.test_metadata.TestMetadata.test_from_email_empty_metadata_uses_from_email_error_message` [EXACT]

## Static test candidates for `src.packaging.pylock.Pylock.from_dict` (not executed)
- `tests.test_pylock.test_toml_roundtrip` [EXACT]
- `tests.test_pylock.test_pylock_version` [EXACT]
- `tests.test_pylock.test_pylock_unsupported_version` [EXACT]
- `tests.test_pylock.test_pylock_invalid_version` [EXACT]
- `tests.test_pylock.test_pylock_unexpected_type` [EXACT]
- `tests.test_pylock.test_pylock_bool_rejected_for_int` [EXACT]
- `tests.test_pylock.test_pylock_missing_version` [EXACT]
- `tests.test_pylock.test_pylock_missing_created_by` [EXACT]

## Static test candidates for `src.packaging.markers.Marker.__init__` (not executed)
- `tests.test_markers.TestOperatorEvaluation.test_prefers_pep440` [EXACT]
- `tests.test_markers.TestOperatorEvaluation.test_new_string_rules` [EXACT]
- `tests.test_markers.TestOperatorEvaluation.test_fails_when_undefined` [EXACT]
- `tests.test_markers.TestOperatorEvaluation.test_arbitrary_equality_on_non_version_key_is_undefined` [EXACT]
- `tests.test_markers.TestOperatorEvaluation.test_allows_prerelease` [EXACT]
- `tests.test_markers.TestMarker.test_parses_valid` [EXACT]
- `tests.test_markers.TestMarker.test_parses_invalid` [EXACT]
- `tests.test_markers.TestMarker.test_parses_invalid_trailing_line_break` [EXACT]

## Contribution rules found
- `CONTRIBUTING.rst`

Competing-PR search: verified (open PR bodies only, at most 10 results; not proof of availability).

## Open PRs referencing this issue (competing work)
- #1450 fix(markers): raise InvalidMarker for deeply nested markers (open) https://github.com/pypa/packaging/pull/1450

## Unknowns and limits
- 5% of call sites in this snapshot are statically unresolved (dynamic or opaque targets); another 35% are external (stdlib/third-party), not part of this snapshot's graph; some dynamic/chained calls are omitted entirely. Paths are a starting point, not a blast radius.
- 1 open PR(s) reference this issue; competing work likely exists.

## Suggested next investigation
1. Read `src.packaging.markers.InvalidMarker` and its callers first (highest lexical match to the issue text).
2. Read CONTRIBUTING.rst for contribution rules before writing code.
3. Skim test candidate `tests.test_markers.TestOperatorEvaluation.test_prefers_pep440` to see expected behavior (static match, not executed).
4. Verify the bug reproduces at the pinned commit ef837aad3127 before changing code.

## Citations (validated against pinned source)
- `src/packaging/markers.py:62-67` (hash `f3b48a592b7d6582`) https://github.com/pypa/packaging/blob/ef837aad3127167671eb37b9f6bd8a94ec7ce26e/src/packaging/markers.py#L62-L67
- `src/packaging/requirements.py:27-33` (hash `939d47a0e54728a3`) https://github.com/pypa/packaging/blob/ef837aad3127167671eb37b9f6bd8a94ec7ce26e/src/packaging/requirements.py#L27-L33
- `src/packaging/metadata.py:889-923` (hash `3ff033ff6170f70a`) https://github.com/pypa/packaging/blob/ef837aad3127167671eb37b9f6bd8a94ec7ce26e/src/packaging/metadata.py#L889-L923
- `src/packaging/pylock.py:608-614` (hash `c2f75f38765e6a9a`) https://github.com/pypa/packaging/blob/ef837aad3127167671eb37b9f6bd8a94ec7ce26e/src/packaging/pylock.py#L608-L614
- `src/packaging/markers.py:401-429` (hash `572f8fc1bde1706a`) https://github.com/pypa/packaging/blob/ef837aad3127167671eb37b9f6bd8a94ec7ce26e/src/packaging/markers.py#L401-L429

---
Read-only analysis. No tests were executed, no claims made on GitHub, no patches generated. Static paths are not proof of bug causality.