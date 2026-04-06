# Identity Model

The public memory API supports these identity fields:

- `tenant_id`
- `user_id`
- `agent_id`
- `session_id`
- `scope`

Recommended rules:

- `tenant_id / user_id / agent_id / session_id` are hard isolation boundaries
- `scope` is a soft visibility label defined by the upstream business layer
- do not rely on `agent_id` alone in multi-user systems

## Recommended Usage

- single-user app: keep one `user_id`, leave `tenant_id` empty or defaulted
- multi-user app: always pass `user_id` explicitly
- multi-agent system: use different `agent_id` values per agent
- shared business space: use `scope` such as `private`, `shared`, or `team:*`
