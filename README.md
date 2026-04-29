# github-actions

Personal reusable GitHub Actions.

## tailscale-ssh

`tailscale-ssh` enables Tailscale SSH access to a running GitHub Actions
runner. It is intended for manual debugging in my own repositories.

```yaml
- uses: claytono/github-actions/tailscale-ssh@main
  with:
    oauth-client-id: ${{ secrets.TAILSCALE_SSH_OAUTH_CLIENT_ID }}
    oauth-secret: ${{ secrets.TAILSCALE_SSH_OAUTH_CLIENT_SECRET }}
    mode: blocking
```

The action supports two modes:

- `blocking`: print SSH connection details and wait until the continue file is
  touched or the timeout expires.
- `background`: print SSH connection details and return immediately. Later
  workflow steps must keep the job alive.

The action prints the exact SSH command and continue command in the workflow
logs. Use the printed absolute continue-file path; do not assume
`GITHUB_WORKSPACE` is set in an interactive SSH session.

### Tailscale Policy

The runner joins the tailnet with `tag:github-actions-ssh`. The tailnet policy
must allow members to SSH to that tag as a non-root user.

```json
{
  "action": "check",
  "src": ["autogroup:member"],
  "dst": ["tag:github-actions-ssh"],
  "users": ["autogroup:nonroot"]
}
```

The runner is ephemeral and disappears when the job ends.
