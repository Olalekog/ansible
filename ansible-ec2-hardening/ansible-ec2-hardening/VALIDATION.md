# Validation performed

Validated in the build environment on 2026-10-12:

- `ansible-playbook site.yml --syntax-check`: passed.
- `ansible-inventory --graph`: both sample groups resolved under `ec2_fleet`.
- `python tests/validate_environments.py`: dev/UAT/prod inventory isolation,
  effective batch sizes, ban settings and shared forwarding policy checked using
  Ansible inventory resolution; playbook syntax passed for all three inventories.
- `ansible-playbook site.yml --list-tasks --tags ssh,fail2ban,updates`: passed.
- `python tests/validate_local.py`: passed YAML parsing, rendered OpenSSH
  syntax/effective-setting tests for Ubuntu/RHEL login users and root, first-value
  precedence over later Includes, forwarding variants, conflicting Match-rule
  detection, APT parsing, and fail2ban jail rendering with a custom SSH port.

The smoke test requires PyYAML, Jinja2, OpenSSH (`sshd`/`ssh-keygen`),
`apt-config`, and the local OpenSSH privilege-separation directory (`/run/sshd`
on the build environment). It starts no SSH daemon and changes no host services.

Not performed: live Ubuntu/RHEL EC2 deployments, fail2ban packet-blocking tests,
DNF security installations, SSH connection recovery tests, fleet idempotency or
a full CIS audit. No AWS inventory or credentials were provided. Follow the
README's canary and runtime validation steps before rolling out to production.
