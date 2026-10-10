"""The host's LAN address, for the app's --dart-define values."""

import socket


def lan_address() -> str | None:
    """The IPv4 address of the interface with the default route.

    Connecting a UDP socket sends nothing; it only picks the outgoing interface,
    which is the Wi-Fi or Ethernet adapter rather than Docker's virtual ones.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed anywhere real
            address = str(probe.getsockname()[0])
    except OSError:
        return None
    return None if address.startswith("127.") or address == "0.0.0.0" else address
