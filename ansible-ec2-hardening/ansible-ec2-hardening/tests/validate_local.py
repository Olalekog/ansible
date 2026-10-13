"""Offline smoke tests; does not connect to EC2 or change host services."""
from pathlib import Path
import shutil
import subprocess
import tempfile

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{args[0]} exited {result.returncode}: {result.stderr}")
    return result.stdout


def main():
    files = list(ROOT.rglob("*.yml"))
    for file in files:
        yaml.safe_load(file.read_text())
    print(f"PASS: parsed {len(files)} YAML files")
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(ROOT / "roles/ssh_hardening/templates"),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    defaults = yaml.safe_load((ROOT / "roles/ssh_hardening/defaults/main.yml").read_text())
    expected = defaults["ssh_hardening_expected"].copy()
    # Resolve the two Ansible boolean-derived settings for each supported policy.
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    with tempfile.TemporaryDirectory(prefix="ec2-hardening-test-") as directory:
        tmp = Path(directory)
        key = tmp / "host_key"
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key))
        for forwarding in (False, True):
            expected["allowtcpforwarding"] = "yes" if forwarding else "no"
            expected["allowagentforwarding"] = "yes" if forwarding else "no"
            block = env.get_template("hardening.conf.j2").render(
                ssh_hardening_expected=expected
            )
            include = tmp / "cloud-init.conf"
            include.write_text("PermitRootLogin yes\nPasswordAuthentication yes\n")
            cfg = tmp / "sshd_config"
            cfg.write_text(
                block + f"HostKey {key}\nInclude {include}\n"
                "PermitRootLogin yes\nPasswordAuthentication yes\n"
            )
            run(sshd, "-t", "-f", str(cfg))
            for user in ("ubuntu", "ec2-user", "root"):
                result = run(
                    sshd, "-T", "-f", str(cfg), "-C",
                    f"user={user},host=controller,addr=192.0.2.10,laddr=192.0.2.20,lport=22",
                ).lower().splitlines()
                for name, value in expected.items():
                    assert f"{name} {str(value).lower()}" in result, (name, value)
            # Prove the effective-policy assertion would detect Match exceptions.
            with cfg.open("a") as file:
                file.write("Match User root\n    PermitRootLogin yes\n")
            result = run(sshd, "-T", "-f", str(cfg), "-C",
                         "user=root,host=controller,addr=192.0.2.10").splitlines()
            assert "permitrootlogin yes" in result
        print("PASS: OpenSSH syntax, first-value Include precedence, forwarding variants,")
        print("      administrator/root policies and conflicting Match detection")

        ubuntu = yaml.safe_load((ROOT / "roles/automatic_updates/tasks/ubuntu.yml").read_text())
        apt_content = next(
            task["ansible.builtin.copy"]["content"] for task in ubuntu
            if "ansible.builtin.copy" in task
        )
        apt_cfg = tmp / "apt.conf"
        apt_cfg.write_text(apt_content)
        # Isolate this test from the controller's existing APT fragments.
        result = run("apt-config", "-o", "Dir::Etc::parts=-",
                     "-o", "Dir::Etc::main=-", "-c", str(apt_cfg), "dump")
        assert 'Unattended-Upgrade::Automatic-Reboot "false";' in result
        assert '${distro_id}:${distro_codename}-security' in result
        assert 'APT::Periodic::Unattended-Upgrade "1";' in result
        print("PASS: APT security-upgrade configuration parsing")

        jail_env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        template = (ROOT / "roles/fail2ban/templates/sshd.local.j2").read_text()
        jail = jail_env.from_string(template).render(
            fail2ban_connection={"stdout": "192.0.2.10 50000 192.0.2.20 2222"},
            fail2ban_banaction="nftables-multiport", fail2ban_maxretry=5,
            fail2ban_findtime=600, fail2ban_bantime=3600,
            fail2ban_trusted_ips=["192.0.2.10/32"],
        )
        import configparser
        parsed = configparser.ConfigParser()
        parsed.read_string(jail)
        assert parsed["sshd"]["port"] == "2222"
        assert parsed["sshd"]["backend"] == "systemd"
        assert parsed["sshd"]["ignoreip"] == "127.0.0.1/8 ::1 192.0.2.10/32"
        print("PASS: jail template, custom SSH port and trusted-source rendering")


if __name__ == "__main__":
    main()
