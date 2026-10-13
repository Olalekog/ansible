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
