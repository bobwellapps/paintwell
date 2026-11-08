#!/bin/bash
# Fixes "Timed out waiting for networking to be ready" when building the snap.
# Snapcraft's build container reaches the internet through LXD's bridge (lxdbr0); a firewall
# or Docker often blocks it.  This finds the cause, fixes it (asking first) and tests it.
BR=lxdbr0
ask() { read -r -p "$1 [Y/n] " a; [ "${a,,}" != "n" ]; }

echo "== LXD network =="
if ! lxc network show $BR >/dev/null 2>&1; then
    echo "The LXD bridge ($BR) doesn't exist. Setting LXD up:"
    sudo lxd init --auto
fi
lxc network show $BR | grep -E "ipv4.address|ipv4.nat" || true
echo

fixed=0
# --- ufw
if command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active"; then
    echo "The ufw firewall is on. It blocks LXD's bridge unless allowed."
    if ask "Allow LXD's bridge through ufw?"; then
        sudo ufw allow in on $BR
        sudo ufw route allow in on $BR
        sudo ufw route allow out on $BR
        fixed=1
    fi
fi
# --- firewalld
if command -v firewall-cmd >/dev/null && sudo firewall-cmd --state 2>/dev/null | grep -q running; then
    echo "firewalld is running. It blocks LXD's bridge unless it's trusted."
    if ask "Mark LXD's bridge as trusted in firewalld?"; then
        sudo firewall-cmd --zone=trusted --change-interface=$BR --permanent
        sudo firewall-cmd --reload
        fixed=1
    fi
fi
# --- Docker
if command -v docker >/dev/null || ip link show docker0 >/dev/null 2>&1; then
    echo "Docker is installed. It blocks forwarded traffic for everything except its own containers."
    if ask "Let LXD's bridge through Docker's rules?"; then
        for ipt in iptables ip6tables; do
            command -v $ipt >/dev/null || continue
            sudo $ipt -C DOCKER-USER -i $BR -j ACCEPT 2>/dev/null || sudo $ipt -I DOCKER-USER -i $BR -j ACCEPT 2>/dev/null
            sudo $ipt -C DOCKER-USER -o $BR -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
                sudo $ipt -I DOCKER-USER -o $BR -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null
        done
        echo "Note: Docker's rules reset when the computer restarts; run this script again if"
        echo "the build stops getting online after a restart."
        fixed=1
    fi
fi
# --- IP forwarding
if [ "$(sysctl -n net.ipv4.ip_forward)" != "1" ]; then
    echo "IP forwarding is off, so containers can't reach the internet."
    if ask "Turn IP forwarding on (and keep it on)?"; then
        sudo sysctl -w net.ipv4.ip_forward=1
        echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/60-lxd-forward.conf >/dev/null
        fixed=1
    fi
fi
[ $fixed = 0 ] && echo "No firewall, Docker or forwarding problem found."

echo
echo "== Testing: starting a small Ubuntu container and checking it can get online =="
lxc delete -f pw-nettest >/dev/null 2>&1
if ! lxc launch ubuntu:24.04 pw-nettest >/dev/null; then
    echo "Couldn't start a test container (this download needs the internet on your computer itself)."
    exit 1
fi
ok=0
for i in $(seq 1 30); do
    if lxc exec pw-nettest -- getent hosts snapcraft.io >/dev/null 2>&1 && \
       lxc exec pw-nettest -- bash -c "timeout 5 bash -c '</dev/tcp/snapcraft.io/443'" 2>/dev/null; then
        ok=1; break
    fi
    sleep 2
done
echo "Container address: $(lxc list pw-nettest -c 4 --format csv)"
lxc delete -f pw-nettest >/dev/null 2>&1
echo
if [ $ok = 1 ]; then
    echo "SUCCESS: containers can get online. Now run:"
    echo "    snapcraft clean"
    echo "    ./snap-tools/2-build.sh"
else
    echo "The container still can't get online. Please send this output to Claude, along with:"
    echo "    lxc network show $BR"
    echo "    sudo nft list ruleset | head -60"
    echo "If you use a VPN, try again with it switched off."
fi
