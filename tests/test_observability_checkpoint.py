import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'mac'))
from observability_checkpoint import checkpoint_batch


class CheckpointTests(unittest.TestCase):
    def test_original_execution_checkpoint_retry_uses_stable_ids(self):
        settings = {'source_id': 'source', 'project_id': 'project', 'sessions': {
            'native-session': {'task_id': 'task', 'run_id': 'run', 'execution_id': 'execution'},
        }}
        checkpoint = {'checkpoint_id': 'result-ready', 'entity_type': 'task',
                      'observed_at': '2026-11-14T10:00:00+08:00',
                      'payload': {'reported_status': 'REPORTED_COMPLETED'},
                      'evidence_ref': 'native-result:original'}
        batch = checkpoint_batch(settings, 'native-session', checkpoint)
        self.assertEqual(batch, checkpoint_batch(settings, 'native-session', checkpoint))
        event = batch['events'][0]
        self.assertEqual((event['task_id'], event['run_id'], event['execution_id']), ('task', 'run', 'execution'))
        self.assertEqual(event['evidence'][0]['authority'], 'self_report')
        self.assertEqual(event['event_type'], 'activity')
        changed = checkpoint_batch(settings, 'native-session', {**checkpoint, 'observed_at': '2026-11-14T10:01:00+08:00'})
        self.assertEqual(event['event_id'], changed['events'][0]['event_id'])
        self.assertNotEqual(batch['batch_id'], changed['batch_id'])
        for bad in ({**checkpoint, 'entity_id': 'different-task'},
                    {**checkpoint, 'payload': {'formal_status': 'CLOSED'}},
                    {**checkpoint, 'observed_at': '2026-11-14T10:00:00'},
                    {**checkpoint, 'entity_type': 'workflow'}):
            with self.assertRaises(ValueError):
                checkpoint_batch(settings, 'native-session', bad)
