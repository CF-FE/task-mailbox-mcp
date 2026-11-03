"""Real authenticated HTTP, encrypted subscription and automatic local delivery.

All accounts, records and callbacks are synthetic. This is not cloud Dot evidence.
"""
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
import httpx2 as httpx
from server import PublicPrefix, create_server
from smoke_event_service import Lifecycle
from smoke_mcp import run_http


async def main():
    fixture = Lifecycle()
    fixture.setUp()
    try:
        auth = StaticTokenVerifier(tokens={'synthetic-token': {
            'client_id': 'synthetic-http', 'scopes': [], 'login': 'test-owner', 'sub': 'native-account'}})
        server = create_server(fixture.mailbox, auth, 'test-owner', fixture.service)
        app = PublicPrefix(server.http_app(path='/mcp', stateless_http=True, json_response=True), '/mailbox')

        async def check(origin):
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                async def rpc(method, params):
                    reply = await client.post(origin + '/mailbox/mcp', json={
                        'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}, headers={
                        'Authorization': 'Bearer synthetic-token', 'Accept': 'application/json, text/event-stream',
                        'MCP-Protocol-Version': '2025-11-25', 'MCP-Method': method})
                    assert reply.status_code == 200, reply.status_code
                    data = reply.json()
                    assert 'error' not in data, data.get('error', {}).get('code')
                    return data['result']

                async def tool(name, arguments):
                    result = await rpc('tools/call', {'name': name, 'arguments': arguments})
                    assert not result.get('isError'), name
                    return result['structuredContent']

                init = await rpc('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {},
                    'clientInfo': {'name': 'synthetic-http', 'version': '1'}})
                assert init['capabilities']['events'] == {}
                assert len((await rpc('tools/list', {}))['tools']) == 9
                assert (await rpc('events/list', {}))['events'][0]['name'] == 'mailbox.result_ready'
                await rpc('events/subscribe', fixture.params)
                await tool('submit_job', {'job_id': 'job-1', 'payload': fixture.payload,
                                          'payload_hash': fixture.payload_hash})
                published = await tool('publish_result', {'job_id': 'job-1', 'event_id': 'event-1',
                    'status': 'REPORTED_COMPLETED', 'result': {'answer': 'synthetic response'}})
                # Exercise the actual service lifespan worker, with no direct tick.
                for _ in range(160):
                    if len(fixture.calls) >= 2:
                        break
                    await asyncio.sleep(0.1)
                assert len(fixture.calls) == 2, 'automatic local event delivery timed out'
                event = json.loads(fixture.calls[-1][1])
                assert event['data']['result_hash'] == published['result_hash']
                read = await tool('get_result', {'job_id': 'job-1', 'version': 1})
                assert read == published
                ack = await tool('ack_result_read', {'job_id': 'job-1', 'event_id': 'read-1',
                    'version': 1, 'result_hash': read['result_hash']})
                assert ack['event_type'] == 'RESULT_READ_ACKNOWLEDGED'
                params = {**fixture.params, 'delivery': {k: v for k, v in fixture.params['delivery'].items() if k != 'secret'}}
                assert await rpc('events/unsubscribe', params) == {}
                print('PASS: authenticated HTTP subscription, automatic result event, exact read/ACK and unsubscribe (synthetic only)')

        await run_http(app, check)
    finally:
        fixture.tearDown()


if __name__ == '__main__':
    asyncio.run(main())
