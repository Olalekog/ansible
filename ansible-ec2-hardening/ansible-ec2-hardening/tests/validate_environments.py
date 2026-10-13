"""Validate environment isolation and variable resolution without EC2 access."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

for env, batch, bantime in [('dev', 2, 600), ('uat', 1, 1800), ('prod', 1, 3600)]:
    inventory = f'inventory/{env}/hosts.yml'
    output = subprocess.check_output(
        [sys.executable, '-m', 'ansible.cli.inventory', '-i', inventory,
         '--playbook-dir', '.', '--list'], cwd=ROOT, text=True,
    )
    data = json.loads(output)
    hosts = data['_meta']['hostvars']
    assert set(hosts) == {f'{env}-ubuntu-canary', f'{env}-rhel-canary'}
    for host, variables in hosts.items():
        assert variables['hardening_environment'] == env, host
        assert variables['hardening_batch_size'] == batch, host
        assert variables['fail2ban_bantime'] == bantime, host
        assert variables['fail2ban_findtime'] == 600, host
        assert variables['fail2ban_maxretry'] == 5, host
        assert variables['ssh_allow_tcp_forwarding'] is False, host
        assert variables['ssh_allow_agent_forwarding'] is False, host
        assert variables['fail2ban_trusted_ips'] == [], host
    subprocess.run(
        [sys.executable, '-m', 'ansible.cli.playbook', '-i', inventory,
         'site.yml', '--syntax-check'], cwd=ROOT, check=True,
    )
    print(f'PASS: {env} inventory isolation, effective variables and playbook syntax')
