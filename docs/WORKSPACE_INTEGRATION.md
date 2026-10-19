# Workspace integration profile

`v0.27-w.1` adds an opt-in profile for a trusted local controller that wants a
mobile model to inspect a specific task workspace. The default installation
does not enable this profile and retains the normal `v0.27` tool settings.

## Security contract

- Bind the gateway to loopback and configure token authentication.
- Set `workspace.allowed_root` to the parent directory containing permitted
  task workspaces. Requests identify a project and a specific workspace root;
  the service rejects paths outside the configured parent.
- The profile exposes only the file read tool. Shell, file writes, document
  writes, web access, and other tools are disabled in the runtime policy.
- Session context and persistent memory are scoped by project identifier.
- Keep the gateway token and workspace root in the local, untracked runtime
  configuration. Never copy credentials into task payloads or public logs.
- `GET /api/agent-card` returns an authenticated, path-free capability record
  only when this profile is enabled. A same-device Host can use it to discover
  the Agent and advertise it as a conversation-only local model. The standard
  gateway listens on `127.0.0.1:8787`; custom ports must be configured locally.
- Configure the local controller's token-file option to a separate owner-only
  file containing the same gateway token. The controller probes the card
  locally, then sends only the allowlisted Agent identity in its heartbeat.
  The token and workspace path never go to the platform registry.

See `config.workspace.example.yaml` for a generic configuration example.
For an authenticated OpenAI-compatible model, set `llm.api_key_file` to a
current-user-owned file with mode `600` or `400`, or set the
`MOBILE_AGENT_LLM_API_KEY_FILE` environment variable to that file's path; keep
the key itself out of configuration files.
The local controller should call the gateway root URL (for example,
`http://127.0.0.1:8787`) rather than the underlying model server's `/v1` URL.
