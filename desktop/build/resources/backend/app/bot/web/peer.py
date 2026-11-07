"""Peer-address checks shared by local-only control and demo-path responses."""
import ipaddress


def _loopback_peer(remote: str | None) -> bool:
    if remote is None:
        return False  # No peer address: fail closed on a LAN bind.
    try:
        address = ipaddress.ip_address(remote)
    except ValueError:
        return False
    mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    return address.is_loopback or bool(mapped and mapped.is_loopback)
