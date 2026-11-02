"""Tracking identity validation and report binding; no GitHub calls or writes."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'mac'))
from tracking import attach_tracking, validate_tracking


def tracking():
    return {
        'repository': 'Owner-1/repo._-2',
        'project_url': 'https://github.com/orgs/Planning-Team/projects/3',
        'item_id': 1,
        'issue_url': 'https://github.com/owner-1/REPO._-2/issues/7',
        'iteration': {'field_id': 2, 'iteration_id': 'iteration-3'},
    }


def draft_tracking():
    value = tracking()
    del value['issue_url']
    value['draft_id'] = 'DI_draft-1_Ab2'
    return value


class TrackingTests(unittest.TestCase):
    def test_valid_binding_accepts_distinct_project_owner_and_issue_case(self):
        original = tracking()
        self.assertEqual(validate_tracking(original), original)
        personal = tracking()
        personal['project_url'] = 'https://github.com/users/Separate-Owner/projects/12'
        self.assertEqual(validate_tracking(personal), personal)

    def test_validation_returns_independent_tracking_and_iteration(self):
        original = tracking()
        expected = deepcopy(original)
        validated = validate_tracking(original)
        self.assertIsNot(validated, original)
        self.assertIsNot(validated['iteration'], original['iteration'])
        validated['iteration']['iteration_id'] = 'other-iteration'
        validated['repository'] = 'other/repo'
        self.assertEqual(original, expected)

    def test_required_tracking_fields_and_unknown_fields_are_rejected(self):
        for name in ('repository', 'project_url', 'item_id', 'iteration'):
            value = tracking()
            del value[name]
            with self.subTest(missing=name), self.assertRaises(ValueError):
                validate_tracking(value)
        value = tracking()
        value['report_route'] = 'unrelated-route'
        with self.assertRaises(ValueError):
            validate_tracking(value)

    def test_formal_project_draft_is_preserved_without_fabricated_issue(self):
        original = draft_tracking()
        before = deepcopy(original)
        validated = validate_tracking(original)
        self.assertEqual(validated, before)
        self.assertNotIn('issue_url', validated)
        attached = attach_tracking({'output': 'saved draft work'}, original)
        self.assertEqual(attached['task_tracking'], before)
        attached['task_tracking']['iteration']['iteration_id'] = 'other'
        self.assertEqual(original, before)

    def test_issue_or_draft_source_is_exclusive_and_required(self):
        missing = tracking()
        del missing['issue_url']
        both = tracking()
        both['draft_id'] = 'DI_draft-1'
        for value in (missing, both):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_tracking(value)

    def test_draft_id_requires_bounded_ascii_node_id(self):
        for invalid in ('', 'DI_', 'di_node', 'D_node', 'DI_node/child',
                        'DI_node?x', 'DI_node#x', 'DI_é', ' DI_node',
                        'DI_node ', 'DI_node\n', 'DI_node\t', 'DI_node\x00',
                        'DI_node\x7f', 'DI_' + 'x' * 126, None, True, 1):
            value = draft_tracking()
            value['draft_id'] = invalid
            with self.subTest(draft_id=invalid), self.assertRaises(ValueError):
                validate_tracking(value)
        for suffix in ('x', 'x' * 125):
            value = draft_tracking()
            value['draft_id'] = 'DI_' + suffix
            self.assertEqual(validate_tracking(value), value)

    def test_tracking_must_be_a_json_object(self):
        for value in (None, [], '', 1, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_tracking(value)

    def test_repository_cannot_smuggle_other_paths_or_invalid_names(self):
        for repository in ('owner', '/repo', 'owner/', 'owner/repo/extra',
                           'owner_name/repo', 'owner/re po', 'owner/repo?x=1',
                           'owner/repo#part', 'owner/repo%2Fextra', ' owner/repo',
                           'owner/repo\n', '所有者/repo'):
            value = tracking()
            value['repository'] = repository
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                validate_tracking(value)

    def test_item_and_iteration_field_ids_require_positive_real_integers(self):
        for invalid in (True, False, 0, -1, 1.0, '1', None):
            for field in ('item_id', 'field_id'):
                value = tracking()
                target = value if field == 'item_id' else value['iteration']
                target[field] = invalid
                with self.subTest(field=field, value=invalid), self.assertRaises(ValueError):
                    validate_tracking(value)

    def test_project_url_requires_exact_github_project_resource(self):
        urls = (
            'http://github.com/orgs/Planning-Team/projects/3',
            'https://github.com.evil.test/orgs/Planning-Team/projects/3',
            'https://github.com@evil.test/orgs/Planning-Team/projects/3',
            'https://name@github.com/orgs/Planning-Team/projects/3',
            'https://github.com:443/orgs/Planning-Team/projects/3',
            'https://github.com/orgs/Planning-Team/projects/3?view=1',
            'https://github.com/orgs/Planning-Team/projects/3#view',
            'https://github.com/orgs/Planning-Team/projects/3/',
            'https://github.com/orgs/Planning-Team/projects/3/items/1',
            'https://github.com/orgs/Planning-Team/projects/0',
            'https://github.com/orgs/Planning-Team/projects/-1',
            'https://github.com/teams/Planning-Team/projects/3',
            'https://github.com/orgs/Planning_Team/projects/3',
            'https://github.com/orgs/Planning%2DTeam/projects/3',
            ' https://github.com/orgs/Planning-Team/projects/3',
            'https://github.com/orgs/Planning-Team/projects/3\n',
        )
        for url in urls:
            value = tracking()
            value['project_url'] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_tracking(value)

    def test_issue_url_requires_matching_repository_and_exact_issue_resource(self):
        urls = (
            'https://github.com/other/repo._-2/issues/7',
            'https://github.com/Owner-1/other/issues/7',
            'http://github.com/Owner-1/repo._-2/issues/7',
            'https://github.com.evil.test/Owner-1/repo._-2/issues/7',
            'https://name@github.com/Owner-1/repo._-2/issues/7',
            'https://github.com:443/Owner-1/repo._-2/issues/7',
            'https://github.com/Owner-1/repo._-2/issues/7?x=1',
            'https://github.com/Owner-1/repo._-2/issues/7#issuecomment-1',
            'https://github.com/Owner-1/repo._-2/issues/7/',
            'https://github.com/Owner-1/repo._-2/issues/7/extra',
            'https://github.com/Owner-1/repo._-2/pull/7',
            'https://github.com/Owner-1/repo._-2/issues/0',
            'https://github.com/Owner-1/repo._-2/issues/-1',
            'https://github.com/Owner-1/%72epo._-2/issues/7',
            'https://github.com/Owner-1/repo._-2/issues/7\n',
        )
        for url in urls:
            value = tracking()
            value['issue_url'] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_tracking(value)

    def test_iteration_requires_exactly_one_complete_form(self):
        invalid = (
            {}, None, [], 'iteration-3',
            {'field_id': 2}, {'iteration_id': 'iteration-3'},
            {'field_id': 2, 'iteration_id': 'iteration-3', 'extra': 1},
            {'state': 'UNSCHEDULED'}, {'reason': 'pending'},
            {'state': 'SCHEDULED', 'reason': 'pending'},
            {'state': 'UNSCHEDULED', 'reason': 'pending', 'extra': 1},
            {'field_id': 2, 'iteration_id': 'iteration-3',
             'state': 'UNSCHEDULED', 'reason': 'pending'},
        )
        for iteration in invalid:
            value = tracking()
            value['iteration'] = iteration
            with self.subTest(iteration=iteration), self.assertRaises(ValueError):
                validate_tracking(value)

    def test_iteration_id_is_bounded_nonblank_and_has_no_controls(self):
        for invalid in ('', '   ', 'x' * 129, 'id\nnext', 'id\tpart', 'id\x00part',
                        'id\x7fpart', 3, True, None):
            value = tracking()
            value['iteration']['iteration_id'] = invalid
            with self.subTest(iteration_id=invalid), self.assertRaises(ValueError):
                validate_tracking(value)
        value = tracking()
        value['iteration']['iteration_id'] = 'x' * 128
        self.assertEqual(validate_tracking(value), value)

    def test_unscheduled_reason_is_explicit_bounded_and_preserved(self):
        value = tracking()
        value['iteration'] = {'state': 'UNSCHEDULED', 'reason': 'not assigned yet'}
        self.assertEqual(validate_tracking(value), value)
        for invalid in ('', '   ', 'x' * 513, None, True, 3):
            value['iteration']['reason'] = invalid
            with self.subTest(reason=invalid), self.assertRaises(ValueError):
                validate_tracking(value)
        value['iteration']['reason'] = 'x' * 512
        self.assertEqual(validate_tracking(value), value)

    def test_legacy_report_keeps_shape_but_returns_new_top_level_dict(self):
        original = {'status': 'UNKNOWN', 'output': {'native_reference': 'request-1'}}
        before = deepcopy(original)
        attached = attach_tracking(original, None)
        self.assertEqual(attached, before)
        self.assertNotIn('task_tracking', attached)
        self.assertIsNot(attached, original)
        self.assertEqual(original, before)

    def test_attach_preserves_result_and_copies_bound_tracking(self):
        original = {'output': 'saved output', 'native_reference': 'request-1'}
        association = tracking()
        before_result, before_tracking = deepcopy(original), deepcopy(association)
        attached = attach_tracking(original, association)
        self.assertEqual(attached, {**before_result, 'task_tracking': before_tracking})
        self.assertEqual(original, before_result)
        self.assertEqual(association, before_tracking)
        attached['task_tracking']['iteration']['iteration_id'] = 'other'
        self.assertEqual(association, before_tracking)

    def test_matching_report_tracking_is_validated_and_not_mutated(self):
        association = tracking()
        original = {'output': 'saved output', 'task_tracking': deepcopy(association)}
        before = deepcopy(original)
        attached = attach_tracking(original, association)
        self.assertEqual(attached, before)
        self.assertIsNot(attached, original)
        self.assertIsNot(attached['task_tracking'], original['task_tracking'])
        attached['task_tracking']['iteration']['iteration_id'] = 'other'
        self.assertEqual(original, before)

    def test_report_cannot_replace_any_part_of_bound_association(self):
        replacements = {
            'repository': 'other/repo._-2',
            'project_url': 'https://github.com/orgs/Planning-Team/projects/4',
            'item_id': 2,
            'issue_url': 'https://github.com/Owner-1/repo._-2/issues/8',
            'iteration': {'field_id': 2, 'iteration_id': 'iteration-other'},
        }
        for name, replacement in replacements.items():
            existing = tracking()
            existing[name] = replacement
            if name == 'repository':
                existing['issue_url'] = 'https://github.com/other/repo._-2/issues/7'
            result = {'output': 'saved output', 'task_tracking': existing}
            before = deepcopy(result)
            with self.subTest(field=name), self.assertRaises(ValueError):
                attach_tracking(result, tracking())
            self.assertEqual(result, before)

    def test_report_cannot_use_boolean_equality_to_forge_binding(self):
        for field in ('item_id', 'field_id'):
            association = tracking()
            association['iteration']['field_id'] = 1
            forged = deepcopy(association)
            target = forged if field == 'item_id' else forged['iteration']
            target[field] = True
            self.assertEqual(forged, association)  # Python equality alone is insufficient.
            with self.subTest(field=field), self.assertRaises(ValueError):
                attach_tracking({'task_tracking': forged}, association)

    def test_report_cannot_change_draft_or_switch_source_kind(self):
        changed = draft_tracking()
        changed['draft_id'] = 'DI_another-draft'
        pairs = ((draft_tracking(), changed),
                 (draft_tracking(), tracking()),
                 (tracking(), draft_tracking()))
        for bound, reported in pairs:
            original = {'output': 'saved output', 'task_tracking': reported}
            before = deepcopy(original)
            with self.subTest(bound=bound, reported=reported), self.assertRaises(ValueError):
                attach_tracking(original, bound)
            self.assertEqual(original, before)

    def test_report_tracking_is_validated_before_comparing_with_stored_binding(self):
        invalid = tracking()
        invalid['iteration'] = {'state': 'UNSCHEDULED', 'reason': '   '}
        for reported in (invalid, None, [], '', True):
            original = {'output': 'saved output', 'task_tracking': reported}
            before = deepcopy(original)
            with self.subTest(reported=reported), self.assertRaises(ValueError):
                attach_tracking(original, tracking())
            self.assertEqual(original, before)


if __name__ == '__main__':
    unittest.main()
