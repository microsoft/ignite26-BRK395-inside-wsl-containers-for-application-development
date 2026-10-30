#!/bin/bash
# Keep microk8s' local control-plane traffic on the Linux loopback.
#
# In WSL "Consomme" networking mode every TCP connection to 127.0.0.0/8 is policy-routed
# (ip rule -> table 127 -> loopback0) to the Windows host, which relays it back into WSL.
# kubelite listens on a dual-stack [::]:16443 socket, which the relay mirrors on Windows as
# IPv6-only, so IPv4 127.0.0.1:16443 is refused. These narrowly scoped rules keep only the
# apiserver (16443) and cluster-agent (25000) ports local.
for p in 16443 25000; do
  for d in dport sport; do
    ip rule show | grep -q "to 127.0.0.0/8 ipproto tcp $d $p lookup local" ||
      ip rule add pref 0 to 127.0.0.0/8 ipproto tcp "$d" "$p" lookup local
  done
done

# Docker (e.g. in another WSL distro sharing this network namespace) sets the nftables
# FORWARD policy to DROP, which silently breaks pod-to-pod traffic. Whitelist Calico.
if nft list chain ip filter DOCKER-USER >/dev/null 2>&1; then
  nft list chain ip filter DOCKER-USER | grep -q 'cali' || {
    nft insert rule ip filter DOCKER-USER iifname "cali*" accept
    nft insert rule ip filter DOCKER-USER oifname "cali*" accept
    nft insert rule ip filter DOCKER-USER iifname "vxlan.calico" accept
    nft insert rule ip filter DOCKER-USER oifname "vxlan.calico" accept
  }
fi
exit 0
