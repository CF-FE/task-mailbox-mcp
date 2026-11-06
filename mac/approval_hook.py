"""Activity-triggered projection from one explicitly configured coordinator CLI.

No task text, user answer or transcript content is collected. The coordinator
owns source verification and approval state; this adapter only maps its bounded
projection into the current native hook output. It cannot wake an idle window.
"""

import json
import re
import subprocess

from config import approval_workflow_settings, in_scope


MAX_OUTPUT = 32 * 1024


def run_approval_hook(config, event):
    """Failure is local to approvals and must not suppress the original hook."""
    try:
        if (not isinstance(event, dict) or event.get('hook_event_name') not in
                ('SessionStart', 'UserPromptSubmit', 'Stop') or not in_scope(config, event.get('cwd'))):
            return {}
        settings = approval_workflow_settings(config)
        if settings is None:
            return {}
        argv, timeout = settings
        data = {key: event[key] for key in ('session_id', 'turn_id', 'cwd', 'transcript_path', 'hook_event_name')
                if key in event}
        if (any(not isinstance(data.get(key), str) or not data[key] or len(data[key]) > 4096
                for key in ('session_id', 'cwd', 'transcript_path', 'hook_event_name')) or
                ('turn_id' in data and (not isinstance(data['turn_id'], str) or len(data['turn_id']) > 128))):
            return {}
        result = subprocess.run(argv, input=json.dumps(data, ensure_ascii=False).encode(),
                                capture_output=True, timeout=timeout, check=False)
        if result.returncode or len(result.stdout) > MAX_OUTPUT:
            return {}
        value = json.loads(result.stdout)
        if (not isinstance(value, dict) or set(value) !=
                {'schema_version', 'system_message', 'additional_context', 'intake'} or
                type(value['schema_version']) is not int or value['schema_version'] != 1 or
                not isinstance(value['additional_context'], str) or len(value['additional_context']) > 16384 or
                (value['system_message'] is not None and (not isinstance(value['system_message'], str)
                                                         or len(value['system_message']) > 16384))):
            return {}
        counts = value['intake']
        if (not isinstance(counts, dict) or len(counts) > 32 or any(
                not isinstance(key, str) or re.fullmatch('[a-z_]{1,40}', key) is None or
                type(count) is not int or count < 0 for key, count in counts.items())):
            return {}
        # OUTPUT_PREPARED/ACCEPTED only: stdout is not a native delivery ACK and
        # does not establish who authored an eventual user decision.
        return value
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return {}
