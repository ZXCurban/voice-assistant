# Autodeploy (staging server, pull model)

The staging server has no public IP, so GitHub Actions cannot reach it over
SSH — `.github/workflows/deploy.yml` stays dormant (self-skips without
`DEPLOY_*` secrets). Instead the server pulls `main` itself.

## Components

- `scripts/deploy.sh [branch]` — pull fast-forward, `docker compose up`
  (`--build` only when the tip changed), wait for `/health`. Refuses to run
  on a dirty working tree. Demo profile: `API_PORT=8010`, `AUTO_MIGRATE`
  and `SEED_DEMO_DATA` come from `docker-compose.yml`.
- `voice-assistant-deploy.service` / `.timer` — user systemd units on the
  server (`~/.config/systemd/user/`), every 3 min (+2 min after boot).
  User lingering is enabled, so the timer survives logout/reboot.

## Operate

```bash
# manual redeploy
API_PORT=8010 ./scripts/deploy.sh main

# timer status and logs (on the server)
systemctl --user list-timers | grep voice-assistant
systemctl --user status voice-assistant-deploy.service
journalctl --user -u voice-assistant-deploy.service -n 30

# rollback to a previous commit
git checkout <sha> && API_PORT=8010 docker compose -p voice-assistant up --build -d
```

## Notes

- Server checkout must stay pristine (no local edits); the script aborts
  otherwise.
- The server authenticates to GitHub over SSH (`git@github.com:...`);
  `git ls-remote` is a quick read-only check that access still works.
- Production rules (`AGENTS.md`, `entrypoint.sh`) forbid auto-migrate/seed —
  this setup is staging/demo only.
