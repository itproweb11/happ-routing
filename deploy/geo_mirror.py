#!/usr/bin/env python3
"""Geo mirror for maxiproxy.net. Python 3 stdlib + curl/nginx/certbot/cron."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid

REPO = "itproweb11/happ-routing"
IP = "45.91.53.149"
DOMAINS = ("geosite.maxiproxy.net", "geoip.maxiproxy.net")
FILES = ("geosite.dat", "geoip.dat")
PORT = 8443
ROOT = Path("/var/lib/happ-geo")
APP = Path("/usr/local/lib/happ-geo/geo_mirror.py")
NGINX = Path("/etc/nginx/conf.d/happ-geo.conf")
CRON = Path("/etc/cron.d/happ-geo")
HOOK = Path("/etc/letsencrypt/renewal-hooks/deploy/happ-geo-reload")
CERT = "happ-geo-maxiproxy"
MARKER = "# Managed by happ-routing/deploy/geo_mirror.py"
MAX_SIZE = 64 * 1024 * 1024


def run(argv, *, capture=False, timeout=300):
    return subprocess.run(argv, check=True, text=True, capture_output=capture,
                          timeout=timeout)


def curl_args():
    return ["curl", "--fail", "--silent", "--show-error", "--location",
            "--proto", "=https", "--proto-redir", "=https",
            "--connect-timeout", "10", "--max-time", "60",
            "--retry", "2", "--retry-delay", "2", "--retry-max-time", "180",
            "--user-agent", "happ-geo-mirror/1"]


def github_json(url):
    result = run(curl_args() + ["--max-filesize", "2097152", url],
                 capture=True, timeout=210)
    return json.loads(result.stdout)


def upstream():
    api = "https://api.github.com/repos/" + REPO
    ref = github_json(api + "/git/ref/heads/main")
    commit = ref["object"]["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("Invalid GitHub commit SHA")
    entries = github_json(api + "/contents/release?ref=" + commit)
    metadata = {item["name"]: item for item in entries if item.get("name") in FILES}
    for name in FILES:
        item = metadata.get(name, {})
        if (item.get("type") != "file" or not isinstance(item.get("size"), int)
                or not 1024 <= item["size"] <= MAX_SIZE
                or not re.fullmatch(r"[0-9a-f]{40}", item.get("sha", ""))):
            raise RuntimeError("Missing or invalid upstream metadata: " + name)
    return commit, metadata


def verify(path, metadata):
    if not path.is_file() or path.stat().st_size != metadata["size"]:
        raise RuntimeError("Wrong file size: " + str(path))
    blob = hashlib.sha1()
    blob.update(("blob " + str(metadata["size"]) + "\0").encode())
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            blob.update(chunk)
    if blob.hexdigest() != metadata["sha"]:
        raise RuntimeError("Git blob checksum mismatch: " + str(path))


def download(url, path):
    run(curl_args() + ["--max-filesize", str(MAX_SIZE), "--output", str(path), url],
        timeout=210)


def sync(root=ROOT):
    """Publish a checked pair with one symlink replacement; failures retain current."""
    root = Path(root)
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    root.chmod(0o755)
    with (root / "sync.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Sync already running; skipped", flush=True)
            return
        commit, metadata = upstream()
        current = root / "current"
        if current.exists() and not current.is_symlink():
            raise RuntimeError("Refusing to replace a non-symlink: " + str(current))
        try:
            existing = json.loads((current / "manifest.json").read_text())
            if existing["commit"] == commit:
                for name in FILES:
                    verify(current / name, metadata[name])
                print(dt.datetime.now(dt.timezone.utc).isoformat(), "unchanged", commit,
                      flush=True)
                return
        except (OSError, ValueError, KeyError, RuntimeError):
            pass
        releases = root / "releases"
        releases.mkdir(mode=0o755, exist_ok=True)
        releases.chmod(0o755)
        stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
        pointer = root / (".current-" + uuid.uuid4().hex)
        try:
            for name in FILES:
                url = "https://raw.githubusercontent.com/" + REPO + "/" + commit + "/release/" + name
                download(url, stage / name)
                verify(stage / name, metadata[name])
                (stage / name).chmod(0o644)
            manifest = {"commit": commit, "files": {
                name: {"size": metadata[name]["size"], "sha": metadata[name]["sha"]}
                for name in FILES}, "updated": dt.datetime.now(dt.timezone.utc).isoformat()}
            (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            (stage / "manifest.json").chmod(0o644)
            stage.chmod(0o755)
            destination = releases / (commit + "-" + uuid.uuid4().hex[:8])
            os.replace(stage, destination)
            pointer.symlink_to("releases/" + destination.name)
            os.replace(pointer, current)
            print(manifest["updated"], "published", commit,
                  {name: metadata[name]["size"] for name in FILES}, flush=True)
            # Retain two earlier pairs for rollback, in addition to current.
            old = sorted((p for p in releases.iterdir()
                          if p.is_dir() and not p.is_symlink()
                          and re.fullmatch(r"[0-9a-f]{40}-[0-9a-f]{8}", p.name)
                          and p != destination), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in old[2:]:
                shutil.rmtree(path)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
            if pointer.is_symlink():
                pointer.unlink()


def atomic_write(path, content, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def owned_file(path):
    if path.is_symlink():
        raise RuntimeError("Refusing to replace a symlink: " + str(path))
    if path.exists() and MARKER not in path.read_text():
        raise RuntimeError("Existing unmanaged file; refusing to overwrite: " + str(path))


def nginx_config(tls):
    text = MARKER + "\nserver {\n    listen 80;\n    server_name " + " ".join(DOMAINS) + ";\n"
    text += "    location ^~ /.well-known/acme-challenge/ {\n        root " + str(ROOT / "acme") + ";\n        try_files $uri =404;\n    }\n"
    text += "    location / { return 404; }\n}\n"
    if tls:
        text += "server {\n    listen " + str(PORT) + " ssl;\n    server_name " + " ".join(DOMAINS) + ";\n"
        text += "    ssl_certificate /etc/letsencrypt/live/" + CERT + "/fullchain.pem;\n"
        text += "    ssl_certificate_key /etc/letsencrypt/live/" + CERT + "/privkey.pem;\n"
        text += "    ssl_protocols TLSv1.2 TLSv1.3;\n    root " + str(ROOT / "current") + ";\n"
        text += "    autoindex off;\n    open_file_cache off;\n    gzip off;\n"
        for name in FILES:
            text += "    location = /" + name + " {\n        try_files $uri =404;\n        types { }\n        default_type application/octet-stream;\n        add_header Cache-Control \"no-cache\";\n        add_header X-Content-Type-Options nosniff always;\n    }\n"
        text += "    location / { return 404; }\n}\n"
    return text


def install_nginx(content):
    old = NGINX.read_text() if NGINX.exists() else None
    atomic_write(NGINX, content)
    try:
        run(["nginx", "-t"])
        run(["systemctl", "reload", "nginx"])
    except Exception:
        if old is None:
            NGINX.unlink(missing_ok=True)
        else:
            atomic_write(NGINX, old)
        # Restore the previous configuration; never restart the VPN service.
        if subprocess.run(["nginx", "-t"], check=False).returncode == 0:
            subprocess.run(["systemctl", "reload", "nginx"], check=False)
        raise


def install():
    if os.geteuid() != 0:
        raise RuntimeError("Run this installer as root")
    for command in ("curl", "nginx", "certbot", "systemctl", "ss"):
        if not shutil.which(command):
            raise RuntimeError("Missing command: " + command)
    if not Path("/usr/bin/python3").is_file():
        raise RuntimeError("/usr/bin/python3 is required")
    if not shutil.which("cron"):
        raise RuntimeError("Install cron first: apt-get update && apt-get install -y cron")
    logrotate = Path("/etc/logrotate.d/happ-geo")
    for path in (NGINX, CRON, HOOK, APP, logrotate):
        owned_file(path)
    if ROOT.is_symlink():
        raise RuntimeError("State directory must not be a symlink")
    if ROOT.exists() and (ROOT.stat().st_uid != 0 or ROOT.stat().st_mode & 0o022):
        raise RuntimeError("State directory must be owned by root and not writable by others")
    for hostname in DOMAINS:
        addresses = {r[4][0] for r in socket.getaddrinfo(hostname, None, socket.AF_INET)}
        if addresses != {IP}:
            raise RuntimeError(hostname + " must have DNS-only A record " + IP + "; got " + str(addresses))
        try:
            ipv6 = {r[4][0] for r in socket.getaddrinfo(hostname, None, socket.AF_INET6)}
        except socket.gaierror as exc:
            if exc.errno not in (socket.EAI_NONAME, socket.EAI_NODATA):
                raise
            ipv6 = set()
        if ipv6:
            raise RuntimeError("Unexpected AAAA for " + hostname + ": " + str(ipv6) + "; this setup serves IPv4")
    run(["nginx", "-t"])
    config = run(["nginx", "-T"], capture=True).stdout
    if not re.search(r"include\s+/etc/nginx/conf\.d/\*\.conf\s*;", config):
        raise RuntimeError("nginx must include /etc/nginx/conf.d/*.conf inside http {}")
    if not NGINX.exists():
        names = re.findall(r"\bserver_name\s+([^;]+);", config)
        if any(name in " ".join(names).split() for name in DOMAINS):
            raise RuntimeError("A geo hostname is already configured in another nginx file")
    listeners = run(["ss", "-H", "-lntp", "sport = :" + str(PORT)], capture=True).stdout
    if listeners.strip() and (not NGINX.exists() or '"nginx"' not in listeners):
        raise RuntimeError("Port 8443 is already in use; stopping before changes")
    if Path("/etc/letsencrypt/renewal/" + CERT + ".conf").exists() and not NGINX.exists():
        raise RuntimeError("Certificate name already exists outside this installation")

    print("Preflight passed. Downloading and verifying the first geo pair.", flush=True)
    sync()
    (ROOT / "acme").mkdir(mode=0o755, exist_ok=True)
    (ROOT / "acme").chmod(0o755)
    backup = Path("/root/happ-geo-backups") / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup.mkdir(mode=0o700, parents=True)
    for path in (NGINX, CRON, HOOK, APP, logrotate):
        if path.exists():
            target = backup / (path.name + ".backup")
            shutil.copyfile(path, target)
            target.chmod(0o600)
    # Keep TLS serving on reruns. New installations start with ACME-only HTTP.
    if not NGINX.exists():
        install_nginx(nginx_config(False))
    print("Requesting the HTTPS certificate through the existing nginx on port 80.", flush=True)
    run(["certbot", "certonly", "--webroot", "-w", str(ROOT / "acme"),
         "--cert-name", CERT, "-d", DOMAINS[0], "-d", DOMAINS[1],
         "--non-interactive", "--agree-tos", "--register-unsafely-without-email",
         "--keep-until-expiring"], timeout=300)
    install_nginx(nginx_config(True))
    source = Path(__file__).read_text()
    atomic_write(APP, source, 0o755)
    hook = "#!/bin/sh\n" + MARKER + "\nset -eu\n"
    hook += 'if [ "${RENEWED_LINEAGE:-}" = "/etc/letsencrypt/live/' + CERT + '" ]; then\n'
    hook += "  /usr/sbin/nginx -t\n  /usr/bin/systemctl reload nginx\nfi\n"
    atomic_write(HOOK, hook, 0o755)
    cron = MARKER + "\nSHELL=/bin/sh\nPATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
    cron += "*/30 * * * * root /usr/bin/python3 " + str(APP) + " sync >>/var/log/happ-geo-sync.log 2>&1\n"
    cron += "17 3 * * * root /usr/bin/certbot renew --cert-name " + CERT + " --quiet >>/var/log/happ-geo-cert.log 2>&1\n"
    atomic_write(CRON, cron)
    if logrotate.parent.exists():
        atomic_write(logrotate, MARKER + "\n/var/log/happ-geo-sync.log /var/log/happ-geo-cert.log {\n    weekly\n    rotate 4\n    compress\n    missingok\n    notifempty\n    create 0640 root root\n}\n")
    run(["systemctl", "enable", "--now", "cron"])
    if shutil.which("ufw"):
        status = run(["ufw", "status"], capture=True).stdout
        if "Status: active" in status:
            run(["ufw", "allow", str(PORT) + "/tcp", "comment", "Happ geo files"])
    print("\nLOCAL HTTPS CHECKS (public reachability still needs a client check):", flush=True)
    for hostname, name in zip(DOMAINS, FILES):
        url = "https://" + hostname + ":" + str(PORT) + "/" + name
        run(["curl", "--fail", "--silent", "--show-error", "--noproxy", "*",
             "--resolve", hostname + ":" + str(PORT) + ":127.0.0.1",
             "--connect-timeout", "5", "--max-time", "30", "--output", "/dev/null",
             "--write-out", url + " HTTP %{http_code}; bytes %{size_download}\\n", url])
    print("\nInstalled. Geo sync: every 30 minutes; certificate check: daily.", flush=True)
    print("Previous managed files, if any:", backup, flush=True)
    print("Current pair:", (ROOT / "current").resolve(), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "sync"))
    args = parser.parse_args()
    try:
        if args.action == "install":
            install()
        else:
            if os.geteuid() != 0:
                raise RuntimeError("Run sync as root")
            sync()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print("ERROR:", error, file=sys.stderr, flush=True)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr[-3000:], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
