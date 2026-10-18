# Workspace integration profile

`v0.27-w` adds an opt-in profile for a trusted local controller that wants a
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

See `config.workspace.example.yaml` for a generic configuration example.
The local controller should call the gateway root URL (for example,
`http://127.0.0.1:8787`) rather than the underlying model server's `/v1` URL.
