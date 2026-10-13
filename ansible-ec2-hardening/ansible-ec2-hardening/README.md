# EC2 Ubuntu/RHEL hardening with Ansible

CIS-inspired subset for Ubuntu 22.04/24.04 LTS and RHEL 8/9 EC2 instances.
This project does not claim complete CIS compliance or a benchmark score.
Fail2ban and automatic patching are operational defenses, not a claim that
every CIS profile requires these exact packages. Select your organization's
benchmark version and assess remaining controls separately.

## Implemented controls

| Role | Behavior |
|---|---|
| `ssh_hardening` | Root SSH, password and keyboard-interactive authentication disabled; public keys and PAM enabled; empty passwords and host-based authentication disabled; X11, TCP/agent forwarding and tunnels disabled by default; authentication/session limits, keepalives and verbose logging enforced. |
| `fail2ban` | SSH journal monitoring with systemd backend; native nftables multiport bans; five failures in ten minutes cause a one-hour ban; configurable trusted IPs. |
| `automatic_updates` | Ubuntu unattended-upgrades for security/eligible ESM origins; RHEL DNF automatic security advisory updates; daily vendor timers; no automatic reboots. |

SSH edits are inserted at the beginning of `sshd_config` because OpenSSH
normally uses the first obtained value. This avoids cloud-init's later
Includes overriding the global baseline. Existing files and Include directives
are preserved. Existing `Match` exceptions can still override global settings:
effective checks for the current administrator and root reject conflicting
settings before reload. Audit every other user/address/hostname-specific Match
context yourself. Reverse-DNS `Match Host` rules are not exhaustively covered.

The playbook preserves vendor cryptographic policies, including RHEL's
system-wide crypto-policy configuration; it does not pin ciphers, KEX or MACs
that might conflict with FIPS or newer policies. Review those policies against
your selected CIS profile. Keepalive settings detect unresponsive clients;
they do not guarantee an interactive idle-session timeout.

## What is in this folder

```text
ansible-ec2-hardening/
├── ansible.cfg                  Ansible settings for this project
├── site.yml                     The playbook: checks, the three roles, a final reconnect
├── group_vars/
│   └── ec2_fleet.yml            Empty on purpose (see below)
├── inventory/
│   ├── dev/                     One folder per environment, never combined
│   │   ├── hosts.yml            Which servers, and the user to log in as
│   │   └── group_vars/all.yml   That environment's settings
│   ├── uat/                     Same two files
│   └── prod/                    Same two files
├── roles/
│   ├── ssh_hardening/           Locks down sshd, with rollback
│   ├── fail2ban/                Bans addresses that keep failing to log in
│   └── automatic_updates/       Installs security updates daily, without rebooting
├── tests/                       Offline checks; they never connect to a server
├── README.md                    This file
└── VALIDATION.md                What was and was not tested before delivery
```

### `ansible.cfg`

Read automatically when a command is run from this folder.

| Setting | Effect |
|---|---|
| `inventory = inventory/dev/hosts.yml` | A command with no `-i` works on dev. UAT and prod must be chosen explicitly, so they cannot be reached by forgetting a flag. |
| `roles_path = roles` | Roles are looked up in `roles/`. |
| `host_key_checking = True` | A server whose host key is not already in `known_hosts` is refused. |
| `retry_files_enabled = False` | No `.retry` files are left behind after a failed run. |
| `interpreter_python = auto_silent` | Ansible finds Python on each server by itself, without a warning. |
| `pipelining = True` | Fewer SSH round trips per task, so runs are faster. |

### `site.yml`

The one playbook. It targets the `ec2_fleet` group with sudo (`become: true`)
and works through the servers in batches of `hardening_batch_size`
(`serial`). With `any_errors_fatal: true`, a failure on any server ends the
run, so later batches are never touched.

1. **Checks before anything is changed** (`pre_tasks`, tagged `always` so they
   run even with `--tags`):
   - the inventory in use sets `hardening_environment` to `dev`, `uat` or `prod`;
   - the server is Ubuntu 22.04/24.04 or RHEL 8/9, uses systemd, is reached
     over SSH, and the login user is set and is not `root`;
   - sudo really gives root (`id -u` returns `0`).
2. **The roles**, in this order, each with a tag to run it by itself:
   `ssh_hardening` (`ssh`), `fail2ban` (`fail2ban`), `automatic_updates`
   (`updates`).
3. **Checks afterwards** (`post_tasks`): pending service restarts are applied,
   the SSH connection is closed, and a new one is opened. A server that can no
   longer be logged in to fails here, before the next batch starts.

### `group_vars/ec2_fleet.yml`

Contains only `{}`: it sets nothing. Variables in a `group_vars` folder next to
the playbook outrank the ones in `inventory/<env>/group_vars/all.yml`, so a
value written here would silently replace what each environment chose. Leave it
empty unless a value must be identical everywhere and no environment may change
it.

### `inventory/`

Each environment has the same two files.

**`hosts.yml`** lists the servers. `ec2_fleet` is the parent group the playbook
targets; under it are `ubuntu` (login user `ubuntu`) and `rhel` (login user
`ec2-user`). Each ships with one example server per group, named
`<env>-ubuntu-canary` and `<env>-rhel-canary`. The addresses are placeholders:

| Environment | Ubuntu example | RHEL example |
|---|---|---|
| dev | 10.10.1.10 | 10.10.2.10 |
| uat | 10.20.1.10 | 10.20.2.10 |
| prod | 10.30.1.10 | 10.30.2.10 |

**`group_vars/all.yml`** holds that environment's settings. The values are in
the table under [Dev, UAT and prod variables](#dev-uat-and-prod-variables).

| Variable | Meaning |
|---|---|
| `hardening_environment` | Names the environment. The playbook refuses to run without it. |
| `hardening_batch_size` | How many servers are changed at a time. |
| `fail2ban_maxretry`, `fail2ban_findtime` | This many failed logins within this many seconds triggers a ban. |
| `fail2ban_bantime` | How long a ban lasts, in seconds. |
| `fail2ban_trusted_ips` | Addresses that are never banned. Empty as shipped; fill in your controller and bastion. |
| `ssh_allow_tcp_forwarding`, `ssh_allow_agent_forwarding` | Whether SSH forwarding is allowed. Both `false`. |

### `roles/ssh_hardening/`

| File | Purpose |
|---|---|
| `defaults/main.yml` | The baseline. `ssh_hardening_expected` is the list of sshd settings and the value each must have; it is used both to write the configuration and to check it afterwards. Also the path of `sshd_config` and the service name (`ssh` on Ubuntu, `sshd` on RHEL). |
| `templates/hardening.conf.j2` | Turns that list into `setting value` lines. |
| `tasks/main.yml` | Applies the baseline safely (steps below). |
| `tasks/verify.yml` | Compares every expected setting with what sshd reports as in effect, and fails naming the first one that differs. |

What `tasks/main.yml` does:

1. Checks that the existing `sshd_config` is valid (`sshd -t`), so an already
   broken file is not blamed on this playbook.
2. Reads the whole file into memory, to be able to put it back.
3. Reads `SSH_CONNECTION` from the session to learn the address and port in
   use, and builds two test cases from it: the login user and `root`.
4. Inserts the settings as a marked block at the very top of `sshd_config`.
   The file is only replaced if the result passes `sshd -t`, and a backup copy
   is kept.
5. Asks sshd what would actually apply to each of the two test cases
   (`sshd -T -C`) and runs `verify.yml` on the answers. This is what catches a
   `Match` block that overrides the baseline.
6. Reloads sshd, only if the file changed and every check passed.
7. If any of steps 4 to 6 fails: restores the original file, reloads sshd, and
   stops the run.
8. Drops the SSH connection and reconnects, proving key-based login still
   works before the other roles run.

### `roles/fail2ban/`

| File | Purpose |
|---|---|
| `defaults/main.yml` | Fallback values: five failures in 600 seconds, a 3600-second ban, no trusted addresses, bans applied with `nftables-multiport`. The inventories override the ban time for dev and UAT. |
| `templates/sshd.local.j2` | The `[sshd]` jail: reads the systemd journal, watches the port SSH is really on, and never bans loopback or the trusted addresses. |
| `tasks/main.yml` | Steps below. |
| `handlers/main.yml` | Restarts fail2ban when the jail file changes. |

What `tasks/main.yml` does:

1. Reads the SSH port from the live session, so a non-standard port is handled.
2. Refuses to continue if the ban settings are not positive numbers or the
   trusted addresses are not a list.
3. Installs `fail2ban`, `python3-systemd` and `nftables`. If the repositories
   cannot supply them the run fails; protection is never skipped quietly.
4. Writes the jail to `/etc/fail2ban/jail.d/99-ansible-sshd.local`.
5. Tests the whole fail2ban configuration (`fail2ban-client -t`), then enables
   and starts the service.
6. Applies the restart if one is pending and confirms the `sshd` jail is
   running (`fail2ban-client status sshd`).

### `roles/automatic_updates/`

`tasks/main.yml` only picks the file for the operating system.

| File | What it sets up |
|---|---|
| `tasks/ubuntu.yml` | Installs `unattended-upgrades` and writes `/etc/apt/apt.conf.d/99-ansible-security-upgrades`: only the security and ESM security origins, package lists refreshed and upgrades applied daily, no automatic reboot, unused dependencies left in place. Enables `apt-daily.timer` and `apt-daily-upgrade.timer`. |
| `tasks/rhel.yml` | Installs `dnf-automatic` and writes `/etc/dnf/automatic.conf`: security updates only, downloaded and applied, never rebooting. Turns off the three other dnf-automatic timers so only `dnf-automatic-install.timer` runs. |

This role has no variables: the behaviour is fixed in the two task files.

### `tests/`

Both scripts run on the machine you work from and change nothing on it.

| Script | What it proves | Needs |
|---|---|---|
| `validate_environments.py` | For dev, UAT and prod in turn: the inventory contains exactly its own two servers, each variable resolves to the value in the table below, and the playbook passes a syntax check. | Python with `ansible-core` |
| `validate_local.py` | Every YAML file parses. The rendered SSH block is valid, takes effect for `ubuntu`, `ec2-user` and `root` even when a later `Include` says otherwise, and a conflicting `Match` block is detected. The APT settings parse and contain the security origin with reboots off. The jail template renders a custom port and trusted address correctly. | PyYAML, Jinja2, OpenSSH (`sshd`, `ssh-keygen`), `apt-config`; in practice a Linux machine |

```bash
python tests/validate_environments.py
python tests/validate_local.py
```

Neither replaces a run against a real canary server.

### `VALIDATION.md`

A record of the checks run on this copy before it was delivered, and a list of
what was not tested: no real EC2 server was hardened, no ban was triggered, and
no update was installed.

## Prerequisites

1. Install `ansible-core` in a controller environment compatible with your
   managed hosts' Python versions. RHEL 8's default platform Python may be too
   old for a recent ansible-core: provision a supported Python first and set
   `ansible_python_interpreter`, or use a compatible supported controller/EE.
2. Provide SSH network access (private VPC/VPN/bastion), non-root public-key
   login and sudo. Enroll and verify host keys in `known_hosts`; checking stays
   enabled. This playbook does not provision users, keys or sudo policies.
3. Ubuntu needs the configured repositories to provide fail2ban (typically
   Universe). RHEL needs working RHUI/subscription repositories plus an approved
   EPEL or internal mirror providing fail2ban and dependencies. Repository setup
   is intentionally an explicit AMI/platform prerequisite; package installation
   fails if dependencies are missing. RHEL security filtering requires advisory
   metadata, not just RPMs in a custom repository.
4. Preserve an independent recovery route, such as a tested SSM Session Manager
   session, before a canary. A syntax-valid configuration can still prevent login.
5. Check compatibility: TCP/agent forwarding is disabled and MaxAuthTries is 3.
   Use `IdentitiesOnly=yes` if your agent offers several keys. MFA over
   keyboard-interactive is incompatible with this public-key-only baseline.
6. Review existing `jail.local` / `jail.d/*.local`, SSH Match rules, vendor SSH
   service command-line overrides and APT config snippets. Later fail2ban/APT
   files or daemon command-line flags can override file settings. Do not run
   concurrent configuration tools modifying the same files.

## Configure inventory and run

Extract the archive and change into `ansible-ec2-hardening/`.
Edit `inventory/<env>/hosts.yml` with private IPs or DNS names and real login users.
The default inventory is dev. Select UAT/prod explicitly with `-i`.
Use one environment inventory per invocation; combining them merges groups and
variables and is not a supported rollout workflow.
Set key paths per host/group or pass `--private-key`; never commit private keys.
Set `fail2ban_trusted_ips` to the exact controller and bastion source IPs observed
by the instances. Loopback is exempt by default. Do not exempt an entire VPC
unless your security policy requires it. Security groups should independently
restrict SSH to trusted sources; this project does not change AWS resources.

```bash
ansible-inventory --graph
ansible ec2_fleet -m ansible.builtin.ping --private-key ~/.ssh/fleet.pem
ansible-playbook site.yml --syntax-check

# Preview on a canary (not a simulation of installing missing packages).
ansible-playbook site.yml --limit dev-ubuntu-canary --check --diff \
  --private-key ~/.ssh/fleet.pem

# Apply to one Ubuntu and one RHEL canary separately.
ansible-playbook site.yml --limit dev-ubuntu-canary --private-key ~/.ssh/fleet.pem
ansible-playbook site.yml --limit dev-rhel-canary --private-key ~/.ssh/fleet.pem

# Dev fleet rollout, two hosts at a time; a failure stops subsequent batches.
ansible-playbook site.yml --private-key ~/.ssh/fleet.pem

# Optional: increase only after canary validation.
ansible-playbook site.yml -e hardening_batch_size=5 --private-key ~/.ssh/fleet.pem

# Roles can also be applied independently through tags.
ansible-playbook site.yml --tags ssh --private-key ~/.ssh/fleet.pem
ansible-playbook site.yml --tags fail2ban --private-key ~/.ssh/fleet.pem
ansible-playbook site.yml --tags updates --private-key ~/.ssh/fleet.pem
```

For a bastion, configure `ansible_ssh_common_args` with `ProxyJump` and keep host
key verification for both hops. Fleet targets need outbound package-repository
access or an internal mirror. Ports are not changed; fail2ban uses the actual
SSH session's destination port. If SSH listens on additional ports, adapt the
jail to cover all listeners. HTTP repositories still rely on vendor package
signatures; use your approved repository transport policy.

## Dev, UAT and prod variables

Each environment has its own inventory and `group_vars/all.yml`. Role defaults
supply the shared baseline; inventory variables override those defaults.
The playbook-level `group_vars/ec2_fleet.yml` is intentionally empty to avoid
shadowing environment overrides. Extra variables (`-e`) override inventory.

| Variable | Dev | UAT | Prod |
|---|---|---|---|
| `hardening_environment` | `dev` | `uat` | `prod` |
| `hardening_batch_size` | 2 | 1 | 1 |
| `fail2ban_bantime` (seconds) | 600 | 1800 | 3600 |
| `fail2ban_findtime` (seconds) | 600 | 600 | 600 |
| `fail2ban_maxretry` | 5 | 5 | 5 |
| `fail2ban_trusted_ips` | Fill with dev sources | Fill with UAT sources | Fill with prod sources |
| TCP/agent forwarding | Disabled | Disabled | Disabled |

Root/password SSH access remains disabled, and automatic security updates
without automatic reboots remain enabled across all environments. All IPs are
examples; replace them before use. The ban-duration and batch-size differences
are editable rollout examples, not CIS requirements.

```bash
# Select a single environment explicitly.
ansible-playbook -i inventory/dev/hosts.yml site.yml --private-key ~/.ssh/dev.pem
ansible-playbook -i inventory/uat/hosts.yml site.yml --private-key ~/.ssh/uat.pem
ansible-playbook -i inventory/prod/hosts.yml site.yml --private-key ~/.ssh/prod.pem

# Inspect resolved variables without connecting to EC2.
ansible-inventory -i inventory/prod/hosts.yml --playbook-dir . --host prod-ubuntu-canary

# Production canary first, then production fleet.
ansible-playbook -i inventory/prod/hosts.yml site.yml \
  --limit prod-ubuntu-canary --check --diff --private-key ~/.ssh/prod.pem
ansible-playbook -i inventory/prod/hosts.yml site.yml \
  --limit prod-ubuntu-canary --private-key ~/.ssh/prod.pem
```

Set private key paths through CLI flags or local inventory configuration, and
keep credentials outside the project. If Python paths differ by environment,
add `ansible_python_interpreter` to that environment's group variables. Role
tags (`--tags ssh`, `fail2ban`, `updates`) work with each selected inventory.

## Validation and recovery

The SSH role validates the complete candidate with `sshd -t`, checks effective
settings with `sshd -T -C`, reloads and opens a fresh connection. Reachable
validation/reload errors restore the original SSH content and stop the rollout.
An authentication or network failure after reload cannot reliably be rescued
through the broken SSH channel: use SSM or your recovery route. A backup of
changed SSH content is left by Ansible's `backup: true`; the task result reports
its path. There is no fleet-wide transaction: earlier successful hosts and
changes from other roles are retained on failure.

On each canary, inspect:

```bash
sudo sshd -t
sudo sshd -T | grep -E 'permitrootlogin|passwordauthentication|kbdinteractiveauthentication|maxauthtries'
sudo fail2ban-client -t
sudo fail2ban-client status sshd
sudo nft list ruleset

# Ubuntu: confirm only intended origins and review dry-run output.
sudo apt-config dump | grep -E 'Allowed-Origins|Origins-Pattern|Automatic-Reboot|APT::Periodic'
sudo unattended-upgrade --dry-run --debug
systemctl list-timers 'apt-daily*'

# RHEL: confirm advisories are available and security filter is configured.
sudo dnf updateinfo list security
sudo cat /etc/dnf/automatic.conf
systemctl list-timers 'dnf-automatic*'
journalctl -u dnf-automatic-install.service
```

Validate fail2ban enforcement using a separate disposable test source (never
the trusted controller) and inspect the resulting nftables ban rule. Syntax
checks alone do not prove a ban action works. Existing enabled jails are retained.
No nftables service is enabled here: fail2ban manages its runtime rules. Existing
firewall reloads can affect these rules, so test alongside UFW/firewalld and your
firewall automation. Monitor fail2ban and patch-service failures centrally.

Run the applied playbook again to check idempotency. Package cache refreshes
and external vendor state may vary; configuration tasks should remain unchanged.
Check mode cannot validate newly staged SSH/fail2ban settings or start packages
and timers that do not exist yet. Read-only preflight commands still execute in
check mode; no configuration is applied. Test actual application on disposable
instances before production.

Automatic security updates may restart services even without rebooting. Schedule
them within your workload's maintenance policy. Track Ubuntu's
`/var/run/reboot-required`, and use `dnf needs-restarting -r` on RHEL if its plugin
is installed, to coordinate kernel reboots through your patch process. ESM
updates require an eligible Ubuntu Pro attachment; listing ESM origins does not
enable subscriptions. Some vendored dependencies need separate application
patching.

## References

- [OpenSSH sshd_config](https://man.openbsd.org/sshd_config)
- [Ansible blockinfile and candidate validation](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/blockinfile_module.html)
- [Ubuntu automatic updates](https://ubuntu.com/server/docs/how-to/software/automatic-updates/)
- [RHEL 9 automated updates](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/managing_software_with_the_dnf_tool/assembly_automating-software-updates-in-rhel-9_managing-software-with-the-dnf-tool)
- [Fail2ban upstream configuration](https://github.com/fail2ban/fail2ban/tree/master/config)

See `VALIDATION.md` for checks actually performed on this delivered archive.
