# Deploying stats.clinbolt.com

Step-by-step from an empty Oracle Cloud account to a live HTTPS site, on an
**Oracle Cloud Always Free** VM running Ubuntu 22.04 or 24.04. Both free shapes
work — `setup_vm.sh` detects the architecture.

Steps marked **MANUAL** are things only you can do: they happen in the OCI
console, at your domain registrar, or on the VM itself.

**Rough timing:** instance creation 10 min, DNS propagation 5–60 min, bootstrap
3 min, first data build 10–20 min.

---

## What you need

- an Oracle Cloud account (the Always Free tier is enough — a card is required
  at signup for identity verification, but Always Free resources are not charged)
- control of the `clinbolt.com` DNS zone at your registrar
- `ssh`, `scp` and `rsync` on your own machine

---

## 1. MANUAL — create an SSH key pair

Do this first; the instance wizard asks for the public key and you cannot easily
add one afterwards without console access.

```bash
ssh-keygen -t ed25519 -C "stats.clinbolt" -f ~/.ssh/clinbolt_stats
```

Press Enter twice for no passphrase, or set one if you prefer. This writes:

- `~/.ssh/clinbolt_stats` — the **private** key, never share or upload it
- `~/.ssh/clinbolt_stats.pub` — the **public** key, which you paste into OCI

Show the public key so you can copy it:

```bash
cat ~/.ssh/clinbolt_stats.pub
```

On Windows, Git Bash and PowerShell both ship `ssh-keygen`; the path is
`C:\Users\<you>\.ssh\clinbolt_stats`.

---

## 2. MANUAL — create the instance

In the OCI console at <https://cloud.oracle.com>:

**Compute → Instances → Create instance**

**Name:** `stats-clinbolt` · **Compartment:** leave as the root compartment.

### Image and shape

Click **Edit** next to *Image and shape*.

- **Image:** *Canonical Ubuntu* → **24.04** (22.04 is also fine)
- **Shape:** click **Change shape**, then pick one:

| Shape | Free tier | RAM | Notes |
| --- | --- | --- | --- |
| **VM.Standard.A1.Flex** (Ampere, arm64) | 4 OCPU / 24 GB total, always free | set **1 OCPU / 6 GB** | Preferred — far more headroom. Often "out of capacity". |
| **VM.Standard.E2.1.Micro** (AMD, x86_64) | 2 instances, always free | 1 GB (fixed) | Always available. This project is built to run in 1 GB. |

Make sure the shape shows the **"Always Free-eligible"** badge before you
continue. For A1.Flex, set OCPUs to 1 and memory to 6 GB — going above
4 OCPU / 24 GB in total across all your A1 instances leaves the free tier.

> **"Out of host capacity"** when creating an A1 instance is common and is not
> something you did wrong. Try a different Availability Domain in the wizard,
> retry later, or just use E2.1.Micro — the pipeline and the memory cap in
> `stats-refresh.service` are both sized for a 1 GB instance.

### Networking

- **Primary network:** *Create new virtual cloud network* (accept the generated
  names) — this creates a VCN with a **public subnet**
- **Subnet:** *Create new public subnet*
- **Assign a public IPv4 address:** **Yes** ← essential

If you already have a VCN, pick it, but confirm the subnet is **public** and
that a public IP will be assigned.

### SSH keys

Choose **Paste public keys** and paste the contents of
`~/.ssh/clinbolt_stats.pub` from step 1.

### Boot volume

Defaults are fine (about 47–50 GB). The free tier allows 200 GB of block
storage in total, so leave *Specify a custom boot volume size* unchecked.

Click **Create**. Wait for the state to go from PROVISIONING to **RUNNING**,
then copy the **Public IP address** from the instance details page. Everything
below refers to it as `<VM_IP>`.

---

## 3. MANUAL — open ports 80 and 443 in the OCI Security List

**This is the step people miss.** Oracle blocks inbound traffic at the virtual
network level, entirely separately from the VM's own firewall. A fresh VCN
allows SSH (22) and nothing else. Opening ports on the VM alone is not enough.

From the instance page, click the **subnet** link, then the **Security List**
(usually *Default Security List for …*), then **Add Ingress Rules**:

| Stateless | Source CIDR | IP Protocol | Destination Port Range |
| --- | --- | --- | --- |
| No | `0.0.0.0/0` | TCP | `80` |
| No | `0.0.0.0/0` | TCP | `443` |

Leave *Stateless* unchecked. If your instance also uses a **Network Security
Group**, add the same two rules there.

---

## 4. MANUAL — point DNS at the VM

At your registrar, in the DNS records for `clinbolt.com`:

| Type | Name | Value | TTL |
| --- | --- | --- | --- |
| A | `stats` | `<VM_IP>` | 300 (or the lowest offered) |

Do **not** proxy the record through Cloudflare or similar for the first deploy —
Caddy needs to answer an HTTP challenge on port 80 directly to obtain the
certificate. You can enable proxying afterwards.

Confirm from your own machine before going further:

```bash
dig +short stats.clinbolt.com
# must print <VM_IP>
```

Do not move on until that returns the right address. Every later failure looks
the same as a DNS failure, so rule it out first.

---

## 5. MANUAL — confirm SSH access

```bash
ssh -i ~/.ssh/clinbolt_stats ubuntu@<VM_IP> 'echo ok && lsb_release -ds && uname -m'
```

You should see `ok`, the Ubuntu version, and either `x86_64` (E2.1.Micro) or
`aarch64` (A1.Flex). The username is `ubuntu` for Canonical images.

Save the key in your SSH config so every later command is shorter:

```
Host clinbolt-stats
    HostName <VM_IP>
    User ubuntu
    IdentityFile ~/.ssh/clinbolt_stats
```

You can then use `clinbolt-stats` in place of `ubuntu@<VM_IP>` everywhere below,
including `./deploy/deploy.sh clinbolt-stats`.

---

## 6. Bootstrap the VM

Copy the deploy scripts up and run the bootstrap. It is idempotent — re-running
it later is how you pick up changes to the Caddyfile or the systemd units.

```bash
scp -r deploy ubuntu@<VM_IP>:~/stats-clinbolt-deploy
ssh ubuntu@<VM_IP> 'sudo bash ~/stats-clinbolt-deploy/setup_vm.sh'
```

It will:

- install `rsync`, `python3-venv` and Caddy from Caddy's official apt repository
- create the `statsbot` service user and `/var/www/stats.clinbolt.com`
- create the Python virtualenv at `/opt/stats-clinbolt/venv`
- **insert** ACCEPT rules for 80 and 443 **above** the image's default REJECT
  rule, then persist them with `netfilter-persistent`
- install the Caddyfile, validate it, and start Caddy
- install and enable the weekly refresh timer

A warning about a missing `requirements.txt` on the very first run is expected —
step 7 uploads it, and this script can be re-run afterwards.

Caddy requests the certificate as soon as it starts. If DNS and ports are right,
HTTPS works within a minute.

---

## 7. Deploy the site and pipeline

From the repository root on your own machine:

```bash
./deploy/deploy.sh ubuntu@<VM_IP>
```

This rsyncs `site/` and `pipeline/` to the VM, fixes ownership, installs the
Python dependencies and reloads Caddy.

Generated data is **not** uploaded by default — the VM builds its own in step 8,
which keeps the published figures reproducible. On a first deploy you probably
want the site to have data immediately rather than being empty for 20 minutes:

```bash
./deploy/deploy.sh ubuntu@<VM_IP> --with-data
```

That seeds it from your local build (~72 KB), and the scheduled refresh replaces
it on its own later.

---

## 8. Trigger the first data build

A full registry pull is ~600 API pages at about one request per second, so allow
**10–20 minutes**.

```bash
ssh ubuntu@<VM_IP> 'sudo systemctl start stats-refresh.service'
ssh ubuntu@<VM_IP> 'journalctl -u stats-refresh.service -f'
```

You will see page-by-page progress, then a summary with record counts,
exclusions and the output size. The refresh builds into a temp directory,
validates the JSON, and only then swaps it into place, so a failure leaves the
previously published data untouched.

---

## 9. Verify

```bash
# HTTPS works and the certificate is real
curl -I https://stats.clinbolt.com/

# HTTP redirects to HTTPS
curl -I http://stats.clinbolt.com/

# The data is being served
curl -s https://stats.clinbolt.com/dashboards/study-size/data/meta.json

# Security headers and cache policy
curl -sI https://stats.clinbolt.com/assets/css/common.css | grep -i cache-control
curl -sI https://stats.clinbolt.com/ | grep -i strict-transport
```

Then open <https://stats.clinbolt.com/> in a browser with the console open, and
check the landing page and the dashboard at both desktop and mobile widths.

Confirm the weekly timer is armed:

```bash
ssh ubuntu@<VM_IP> 'systemctl list-timers stats-refresh.timer'
```

`NEXT` should show the coming Sunday. The timer runs Sundays at 03:30 UTC with
up to an hour of jitter, and `Persistent=true` means a missed run happens once
the VM is back up.

---

## Routine operations

| Task | Command |
| --- | --- |
| Push code or content changes | `./deploy/deploy.sh ubuntu@<VM_IP>` |
| Rebuild the data now | `ssh ubuntu@<VM_IP> 'sudo systemctl start stats-refresh.service'` |
| Watch a running refresh | `ssh ubuntu@<VM_IP> 'journalctl -u stats-refresh.service -f'` |
| Last refresh result | `ssh ubuntu@<VM_IP> 'systemctl status stats-refresh.service'` |
| Caddy logs | `ssh ubuntu@<VM_IP> 'sudo journalctl -u caddy -n 100'` |
| Reload Caddy after a config change | `ssh ubuntu@<VM_IP> 'sudo systemctl reload caddy'` |

---

## Troubleshooting

### "Out of host capacity" when creating the instance

Oracle has no free Ampere cores in that availability domain right now. It is not
an account problem. Try another Availability Domain in the wizard, retry at a
different time of day, or create a **VM.Standard.E2.1.Micro** instead — this
project is designed to run in its 1 GB.

### The site is unreachable — connection times out

This is the common Oracle problem, and it has **two independent causes**. Check
both; fixing one while the other is still closed looks like no progress at all.

**First, is the VM even receiving traffic?** From your machine:

```bash
curl -v --max-time 10 http://stats.clinbolt.com/
```

A *timeout* means packets are dropped before reaching Caddy → Security List or
iptables. *Connection refused* means they arrive but nothing is listening →
Caddy is down; see below.

**Cause 1 — the OCI Security List (most likely).** Confirm from the VM that
Caddy is listening and the port works locally:

```bash
ssh ubuntu@<VM_IP> 'sudo ss -lntp | grep -E ":(80|443)"'
ssh ubuntu@<VM_IP> 'curl -sI http://localhost/ | head -1'
```

If those succeed but the site times out from outside, traffic is blocked at the
virtual network. Go back to **step 3** and verify the ingress rules exist on the
Security List attached to *this instance's subnet* — and check for a Network
Security Group on the instance's VNIC, which needs the same rules.

**Cause 2 — the instance firewall.** Oracle's Ubuntu images ship an INPUT chain
ending in a REJECT rule, so a rule appended with `-A` never takes effect. Check
where the ACCEPT rules landed:

```bash
ssh ubuntu@<VM_IP> 'sudo iptables -L INPUT -n --line-numbers'
```

The `ACCEPT … tcp dpt:80` and `dpt:443` lines must appear **above** the
`REJECT all` line. If they are missing or below it, re-run `setup_vm.sh`, which
inserts them at the right position, or fix it by hand:

```bash
sudo iptables -I INPUT 6 -p tcp --dport 80 -j ACCEPT     # 6 = the REJECT line number
sudo iptables -I INPUT 6 -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

Without `netfilter-persistent save`, the rules vanish on reboot.

### The certificate will not issue

Caddy needs inbound port **80** reachable from the public internet for the ACME
challenge; 443 alone is not enough.

```bash
ssh ubuntu@<VM_IP> 'sudo journalctl -u caddy -n 50 --no-pager'
```

- `no such host` / NXDOMAIN → DNS is not resolving yet (step 4)
- `timeout during connect` → port 80 is still blocked (step 3)
- `too many certificates already issued` → you have hit a Let's Encrypt rate
  limit for the name; wait it out rather than retrying in a loop

Check that DNS resolves *from the VM itself*:

```bash
ssh ubuntu@<VM_IP> 'dig +short stats.clinbolt.com'
```

### The dashboard page loads but says the data could not be loaded

The site is fine; the data file is missing. Run a refresh (step 8) and check:

```bash
ssh ubuntu@<VM_IP> 'ls -la /var/www/stats.clinbolt.com/dashboards/study-size/data/'
ssh ubuntu@<VM_IP> 'sudo journalctl -u stats-refresh.service -n 50 --no-pager'
```

If a build failed, the previous data is deliberately left in place — the symptom
of a *first* failed build is an empty `data/` directory.

### The refresh fails or the VM runs out of memory

```bash
ssh ubuntu@<VM_IP> 'sudo journalctl -u stats-refresh.service -n 100 --no-pager'
```

The unit caps memory at 768 MB, sized for the 1 GB E2.1.Micro. On an A1 instance
with 6 GB you can raise `MemoryMax=` in
`deploy/stats-refresh.service` and redeploy. If you are on E2.1.Micro and the
registry has grown enough for the cap to bite, add swap:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### Changes do not show up after a deploy

HTML is served with `must-revalidate`, but `/assets/*` is cached for a year as
immutable. If you change a shared asset, either rename the file or hard-reload
(Ctrl+Shift+R) while testing.

### SSH stops working after an instance restart

The public IP can change if it was ephemeral. On the instance page, check the
current public IP, and consider reserving it: **Networking → Reserved public
IPs**, then attach it to the instance's VNIC. If you do that, update the DNS A
record from step 4 to match.
